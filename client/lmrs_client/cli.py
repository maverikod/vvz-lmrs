"""The ``lmrs-client`` console script: drive an LMRS server from the shell.

One subcommand per public server command, derived from the client class by
inspection so the CLI surface cannot drift from the client surface: a method
added to ``LmrsClient`` becomes a subcommand without an edit here. The
``estimate`` pass-through method additionally gets explicit prompt-size flags,
because sizing a prompt before sending it is the service's reason to exist.

Connection settings come from flags, falling back to the same ``LMRS_LIVE_*``
environment the acceptance pipeline uses. The full response envelope is
printed as JSON; the exit code comes from the three-layer verdict, so the CLI
is honest scriptable ground truth: exit 0 means the server really succeeded,
including the domain layer.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import json
import logging
import os
import sys
from typing import Any, Callable

# The adapter framework prints a banner and emits INFO log lines to stdout at
# import time. This CLI's stdout is machine-readable JSON (its stated purpose
# is scriptable ground truth), so the import happens with stdout redirected to
# stderr and the framework loggers are quieted to errors afterwards.
with contextlib.redirect_stdout(sys.stderr):
    from lmrs_client.client import LmrsClient
from lmrs_client.verdict import verdict

logging.getLogger("mcp_proxy_adapter").setLevel(logging.ERROR)

_CONNECTION_FLAGS: tuple[tuple[str, str, str], ...] = (
    ("--host", "LMRS_LIVE_HOST", "Server hostname."),
    ("--port", "LMRS_LIVE_PORT", "Server port (default 8012)."),
    ("--protocol", "LMRS_LIVE_PROTOCOL", "http, https or mtls (default https)."),
    ("--token", "LMRS_LIVE_TOKEN", "Authentication token."),
    ("--cert", "LMRS_LIVE_CERT", "Client certificate path (mTLS)."),
    ("--key", "LMRS_LIVE_KEY", "Client private key path (mTLS)."),
    ("--ca", "LMRS_LIVE_CA", "CA certificate path."),
    ("--timeout", "LMRS_LIVE_TIMEOUT", "Request timeout in seconds."),
)

# Explicit flag specs for the estimate pass-through method: its signature is
# **request, which inspection cannot turn into flags. The flags mirror the
# server's estimate schema; --json supplies the raw-mode body verbatim.
_ESTIMATE_FLAGS: tuple[tuple[str, type, str], ...] = (
    ("--message", str, "Text mode: the user message to size."),
    ("--system", str, "Text mode: optional system instruction."),
    ("--model-name", str, "Model the request targets."),
    ("--max-tokens", int, "Text mode: output tokens to reserve (default 128)."),
    ("--request-id", str, "Request identifier."),
)


def _flag_name(parameter: str) -> str:
    """Return the CLI flag of one method parameter.

    Args:
        parameter: Python parameter name.

    Returns:
        The ``--kebab-case`` flag.
    """
    return "--" + parameter.replace("_", "-")


def _parser_for_annotation(annotation: str) -> Callable[[str], Any]:
    """Return the value parser for a parameter annotation.

    Args:
        annotation: The stringified annotation from the client signature.

    Returns:
        A callable converting the CLI string into the parameter value.
    """
    if "int" in annotation:
        return int
    if "float" in annotation:
        return float
    if "bool" in annotation:
        return lambda value: value.lower() in {"1", "true", "yes"}
    return str


def command_methods() -> dict[str, inspect.Signature]:
    """Return every public client method with its signature.

    Returns:
        Mapping of method name to signature, sorted by name.
    """
    methods: dict[str, inspect.Signature] = {}
    for name in sorted(dir(LmrsClient)):
        if name.startswith("_"):
            continue
        attribute = getattr(LmrsClient, name)
        if callable(attribute):
            methods[name] = inspect.signature(attribute)
    return methods


def _add_method_subparser(subparsers: Any, name: str, signature: inspect.Signature) -> None:
    """Register the subcommand of one client method.

    Args:
        subparsers: The argparse subparsers container.
        name: Method name.
        signature: The method signature.
    """
    subparser = subparsers.add_parser(name.replace("_", "-"), help=f"Call the {name} command.")
    has_var_keyword = False
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        if parameter.kind is inspect.Parameter.VAR_KEYWORD:
            has_var_keyword = True
            continue
        annotation = str(parameter.annotation)
        required = parameter.default is inspect.Parameter.empty
        if "bool" in annotation and parameter.default is False:
            subparser.add_argument(_flag_name(parameter.name), action="store_true", dest=parameter.name)
            continue
        subparser.add_argument(
            _flag_name(parameter.name),
            dest=parameter.name,
            type=_parser_for_annotation(annotation),
            required=required,
            default=None if not required else None,
            help=f"{parameter.name} ({annotation})",
        )
    if has_var_keyword:
        for flag, value_type, help_text in _ESTIMATE_FLAGS:
            subparser.add_argument(flag, type=value_type, default=None, help=help_text)
        subparser.add_argument("--json", default=None, help="Raw JSON body merged under the explicit flags.")


def build_parser() -> argparse.ArgumentParser:
    """Build the full CLI parser.

    Returns:
        The parser with one subcommand per client method.
    """
    parser = argparse.ArgumentParser(
        prog="lmrs-client",
        description=(
            "Drive an LMRS server: size prompts before sending them "
            "(token-count, estimate), run admitted chat, and operate the "
            "model cache and lifecycle. Exit code 0 means the server really "
            "succeeded, including the domain layer."
        ),
    )
    for flag, env_name, help_text in _CONNECTION_FLAGS:
        parser.add_argument(flag, default=None, help=f"{help_text} Env: {env_name}.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name, signature in command_methods().items():
        _add_method_subparser(subparsers, name, signature)
    return parser


def _connection(args: argparse.Namespace) -> dict[str, Any]:
    """Resolve connection settings from flags and environment.

    Args:
        args: Parsed CLI arguments.

    Returns:
        Constructor arguments for LmrsClient.

    Raises:
        SystemExit: If no host is configured anywhere.
    """
    values: dict[str, Any] = {}
    for flag, env_name, _help in _CONNECTION_FLAGS:
        name = flag.lstrip("-").replace("-", "_")
        value = getattr(args, name, None)
        if value is None:
            value = os.environ.get(env_name)
        if value is not None:
            values[name] = value
    if "host" not in values:
        print("no server: pass --host or set LMRS_LIVE_HOST", file=sys.stderr)
        raise SystemExit(2)
    if "port" in values:
        values["port"] = int(values["port"])
    if "timeout" in values:
        values["timeout"] = float(values["timeout"])
    return values


def _method_arguments(args: argparse.Namespace, signature: inspect.Signature) -> dict[str, Any]:
    """Collect the method keyword arguments a subcommand received.

    Args:
        args: Parsed CLI arguments.
        signature: The target method's signature.

    Returns:
        Keyword arguments for the client method.
    """
    arguments: dict[str, Any] = {}
    has_var_keyword = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()
    )
    if has_var_keyword:
        raw = getattr(args, "json", None)
        if raw:
            loaded = json.loads(raw)
            if not isinstance(loaded, dict):
                print("--json must carry a JSON object", file=sys.stderr)
                raise SystemExit(2)
            arguments.update(loaded)
        for flag, _value_type, _help in _ESTIMATE_FLAGS:
            name = flag.lstrip("-").replace("-", "_")
            value = getattr(args, name, None)
            if value is not None:
                arguments[name] = value
        return arguments
    for parameter in signature.parameters.values():
        if parameter.name == "self":
            continue
        value = getattr(args, parameter.name, None)
        if value is None:
            continue
        if parameter.default is False and value is False:
            continue
        arguments[parameter.name] = value
    return arguments


def main(argv: list[str] | None = None) -> int:
    """Entry point of the ``lmrs-client`` command.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.

    Returns:
        0 when the server reported success through every layer, 1 on a
        reported failure, 2 on usage errors.
    """
    args = build_parser().parse_args(argv)
    method_name = args.command.replace("-", "_")
    signature = command_methods()[method_name]
    client = LmrsClient(**_connection(args))

    async def call() -> Any:
        result = getattr(client, method_name)(**_method_arguments(args, signature))
        if inspect.isawaitable(result):
            result = await result
        return result

    try:
        response = asyncio.run(call())
    except Exception as error:  # noqa: BLE001 - a CLI reports, it does not traceback
        print(json.dumps({"success": False, "error": f"{type(error).__name__}: {error}"}, indent=2))
        return 1
    print(json.dumps(response, indent=2, default=str))
    succeeded, code = verdict(response)
    if not succeeded:
        print(f"verdict: FAIL ({code})", file=sys.stderr)
    return 0 if succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
