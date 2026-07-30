"""The all-commands live check (C-062).

Registers one named check, ``commands-live``, that drives every public LMRS
command through the LMRS client against a real running server. This module
registers a check and nothing else: no script, no main, no second runner, since
the pipeline is the only verification entrypoint the project has.

The command list is derived from the client's own public surface rather than
written out here. A hard-coded list would reproduce exactly the drift this
check exists to catch: a command added to client and server would simply never
be exercised.

Skipping is a failure. When connection settings are missing or the server
cannot be reached the check returns nonzero, because a check that reports
success without running is worse than no check at all.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from pathlib import Path
from typing import Any

from pipeline.registry import REGISTRY, Check

_CLIENT_ROOT = Path(__file__).resolve().parent.parent / "client"

# Read-only state first, then accounting, then generation, then the mutating
# cache and lifecycle commands. Anything the client exposes that is not named
# here still runs, after these, so a new command cannot be silently skipped.
_ORDER: tuple[str, ...] = (
    "healthcheck",
    "info",
    "capacity",
    "model_status",
    "queue_status",
    "local_model_cache_status",
    "local_lmcache_status",
    "token_count",
    "estimate",
    "chat",
    "local_model_cache_preload",
    "local_model_load",
    "local_model_reload",
    "local_lmcache_purge",
    "cancel",
    "local_model_unload",
    "local_model_cache_delete",
    "local_model_switch",
)


def _load_client_class() -> Any:
    """Import LmrsClient from the client package.

    Returns:
        The LmrsClient class.

    Raises:
        RuntimeError: If the client package is not importable.
    """
    if str(_CLIENT_ROOT) not in sys.path:
        sys.path.insert(0, str(_CLIENT_ROOT))
    try:
        from lmrs_client.client import LmrsClient
    except ImportError as error:
        raise RuntimeError(f"the LMRS client package is not importable: {error}") from error
    return LmrsClient


def _client_commands(client_class: Any) -> list[str]:
    """Return every public command method of the client, in execution order.

    Args:
        client_class: The LmrsClient class.

    Returns:
        Method names ordered by _ORDER first, then any method the order does
        not mention, so a newly added command is still exercised.
    """
    public = {
        name
        for name in dir(client_class)
        if not name.startswith("_") and callable(getattr(client_class, name))
    }
    ordered = [name for name in _ORDER if name in public]
    return ordered + sorted(public - set(ordered))


def _settings() -> dict[str, Any]:
    """Read connection settings for the live server from the environment.

    Returns:
        Constructor arguments for LmrsClient.

    Raises:
        RuntimeError: If the host is not configured, since running against a
            guessed address would prove nothing.
    """
    host = os.environ.get("LMRS_LIVE_HOST")
    if not host:
        raise RuntimeError(
            "LMRS_LIVE_HOST is not set; the live check needs a deployed server "
            "and must not report success without reaching one"
        )
    settings: dict[str, Any] = {
        "host": host,
        "port": int(os.environ.get("LMRS_LIVE_PORT", "8012")),
        "protocol": os.environ.get("LMRS_LIVE_PROTOCOL", "https"),
    }
    for env_name, kwarg in (
        ("LMRS_LIVE_TOKEN", "token"),
        ("LMRS_LIVE_CERT", "cert"),
        ("LMRS_LIVE_KEY", "key"),
        ("LMRS_LIVE_CA", "ca"),
    ):
        value = os.environ.get(env_name)
        if value:
            settings[kwarg] = value
    return settings


def _arguments(command: str) -> dict[str, Any]:
    """Build the acceptance-profile arguments for one command.

    Destructive commands act on the acceptance model only, and the order in
    ``_ORDER`` reloads it afterwards so the server is left serving.

    Args:
        command: Client method name.

    Returns:
        Keyword arguments for the call.
    """
    model = os.environ.get("LMRS_LIVE_MODEL", "acceptance-model")
    per_command: dict[str, dict[str, Any]] = {
        "model_status": {"model_name": model},
        "token_count": {"input_tokens": 8, "tokenizer_name": "acceptance", "tokenizer_accuracy": "rough"},
        "estimate": {
            "request_id": "live-estimate",
            "model_name": model,
            "token_breakdown": {"input_tokens": 8, "tool_tokens": 0, "service_tokens": 0, "reserved_output_tokens": 8},
            "declared_context_window": 4096,
            "capacity": {"usable_dynamic_vram_bytes": 1 << 30},
            "kv_bytes_per_token": 1024,
            "per_request_overhead_bytes": 0,
            "runtime_batch_overhead_bytes": 0,
        },
        "chat": {"message": "Reply with the single word: ready", "model_name": model, "max_tokens": 16},
        "cancel": {"request_id": "live-nonexistent-request"},
        "local_model_cache_preload": {"model_name": model},
        "local_model_cache_status": {"model_name": model},
        "local_model_cache_delete": {"model_name": model},
        "local_model_load": {"model_name": model},
        "local_model_unload": {"model_name": model},
        "local_model_reload": {"model_name": model},
        "local_model_switch": {"model_name": model},
    }
    return per_command.get(command, {})


async def _drive(client_class: Any) -> int:
    """Call every public client command against the live server.

    Args:
        client_class: The LmrsClient class.

    Returns:
        0 when every command succeeded, 1 otherwise.
    """
    client = client_class(**_settings())
    failures: list[str] = []
    for command in _client_commands(client_class):
        method = getattr(client, command)
        try:
            result = method(**_arguments(command))
            if inspect.isawaitable(result):
                await result
            print(f"PASS {command}", flush=True)
        except Exception as error:  # noqa: BLE001 - one broken command must not hide the rest
            code = getattr(error, "code", None) or type(error).__name__
            failures.append(command)
            print(f"FAIL {command}: {code}: {error}", flush=True)
    total = len(_client_commands(client_class))
    print(f"commands-live summary: {total - len(failures)} passed, {len(failures)} failed", flush=True)
    return 0 if not failures else 1


def run_commands_live() -> int:
    """Run every public LMRS command against the deployed server.

    Returns:
        0 when every command passed; nonzero when any command failed, the
        client is unavailable, or no live server is configured.
    """
    try:
        client_class = _load_client_class()
        settings_error: Exception | None = None
        try:
            _settings()
        except RuntimeError as error:
            settings_error = error
        if settings_error is not None:
            print(f"FAIL commands-live: {settings_error}", file=sys.stderr, flush=True)
            return 1
        return asyncio.run(_drive(client_class))
    except RuntimeError as error:
        print(f"FAIL commands-live: {error}", file=sys.stderr, flush=True)
        return 1


REGISTRY.register(
    Check(
        "commands-live",
        "Drive every public LMRS command through the client against the deployed server.",
        run_commands_live,
    )
)
