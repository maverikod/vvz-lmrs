"""Live acceptance checks: drive the deployed LMRS server through the client.

This module is the single implementation of the live acceptance run. The
server repository's ``pipeline`` and the installed client's ``pipeline`` both
register these functions, so there is exactly one runner and it cannot drift
between the two entrypoints.

Connection settings come from the environment (``LMRS_LIVE_*``). Skipping is a
failure: when the server cannot be reached the checks return nonzero, because
a check that reports success without running is worse than no check at all.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
from typing import Any

from lmrs_client.client import LmrsClient
from lmrs_client.verdict import command_payload, verdict

# Read-only state first, then accounting, then generation, then the mutating
# cache and lifecycle commands. Anything the client exposes that is not named
# here still runs, after these, so a new command cannot be silently skipped.
#
# The cache status runs after the preload, not before it: the cache commands act
# on a scratch model that this run downloads and deletes again, so asking for its
# status first would report a model nothing had fetched yet. model_status runs
# after local_model_load for the same reason: residency is operator-declared
# state, and before the load this run performs there is honestly nothing loaded.
ORDER: tuple[str, ...] = (
    "healthcheck",
    "info",
    "capacity",
    "queue_status",
    "local_lmcache_status",
    "token_count",
    "estimate",
    "chat",
    "local_model_cache_preload",
    "local_model_cache_status",
    "local_model_load",
    "model_status",
    "local_model_reload",
    "local_lmcache_purge",
    "cancel",
    "local_model_unload",
    "local_model_cache_delete",
    "local_model_switch",
)

# Commands whose correct answer on this deployment is a specific negative
# outcome. vLLM cannot dynamically unload a model, so the honest reply to
# unload is exactly VLLM_DYNAMIC_UNLOAD_UNSUPPORTED: that reply is asserted as
# the expected behavior, and any other outcome - including success - fails.
EXPECTED_REASONS: dict[str, str] = {
    "local_model_unload": "VLLM_DYNAMIC_UNLOAD_UNSUPPORTED",
}


def client_commands() -> list[str]:
    """Return every public command method of the client, in execution order.

    Returns:
        Method names ordered by ORDER first, then any method the order does
        not mention, so a newly added command is still exercised.
    """
    public = {
        name
        for name in dir(LmrsClient)
        if not name.startswith("_") and callable(getattr(LmrsClient, name))
    }
    ordered = [name for name in ORDER if name in public]
    return ordered + sorted(public - set(ordered))


def settings() -> dict[str, Any]:
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
    connection: dict[str, Any] = {
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
            connection[kwarg] = value
    return connection


def acceptance_models() -> tuple[str, str]:
    """Return the served model and the scratch model the cache commands use.

    Two names, not one, and the difference matters: the disk-cache commands act
    on real weights, so pointing them at the served model would have the
    acceptance run delete the very weights the server is running. The scratch
    model is a tiny public repository that can be downloaded and deleted freely.

    Returns:
        The served model name and the scratch cache model name.

    Raises:
        RuntimeError: If the served model is not configured, or if the scratch
            model is the served one.
    """
    served = os.environ.get("LMRS_LIVE_MODEL")
    if not served:
        raise RuntimeError(
            "LMRS_LIVE_MODEL is not set; it must name the model the deployed "
            "server serves, because commands driven against an invented name "
            "prove nothing about the deployment"
        )
    scratch = os.environ.get("LMRS_LIVE_CACHE_MODEL", "hf-internal-testing/tiny-random-gpt2")
    if scratch == served:
        raise RuntimeError(
            "LMRS_LIVE_CACHE_MODEL must differ from LMRS_LIVE_MODEL: the cache "
            "commands delete what they preload, and deleting the served model "
            "would remove the weights the server is running on"
        )
    return served, scratch


def arguments_for(command: str) -> dict[str, Any]:
    """Build the acceptance-profile arguments for one command.

    Lifecycle and generation commands act on the served model; the disk-cache
    commands act on the scratch model, which they preload and delete again, so
    the run leaves the deployment exactly as it found it.

    Args:
        command: Client method name.

    Returns:
        Keyword arguments for the call.
    """
    model, scratch = acceptance_models()
    per_command: dict[str, dict[str, Any]] = {
        "model_status": {"model_name": model},
        "token_count": {"message": "Count the tokens of this sentence.", "model_name": model, "reserved_output_tokens": 8},
        "estimate": {"message": "Would this prompt fit?", "model_name": model, "max_tokens": 16},
        "chat": {"message": "Reply with the single word: ready", "model_name": model, "max_tokens": 16},
        "cancel": {"request_id": "live-nonexistent-request"},
        "local_model_cache_preload": {"model_name": scratch},
        "local_model_cache_status": {"model_name": scratch},
        "local_model_cache_delete": {"model_name": scratch},
        "local_model_load": {"model_name": model},
        "local_model_unload": {"model_name": model},
        "local_model_reload": {"model_name": model},
        "local_model_switch": {"model_name": model},
    }
    return per_command.get(command, {})


async def _call(client: LmrsClient, command: str, arguments: dict[str, Any]) -> Any:
    """Invoke one client method, awaiting when needed.

    Args:
        client: The connected client.
        command: Method name.
        arguments: Keyword arguments.

    Returns:
        The response value.
    """
    result = getattr(client, command)(**arguments)
    if inspect.isawaitable(result):
        result = await result
    return result


async def _drive(client: LmrsClient) -> int:
    """Call every public client command against the live server.

    Args:
        client: The connected client.

    Returns:
        0 when every command succeeded, 1 otherwise.
    """
    failures: list[str] = []
    for command in client_commands():
        try:
            response = await _call(client, command, arguments_for(command))
            succeeded, code = verdict(response)
            expected = EXPECTED_REASONS.get(command)
            if expected is not None:
                if not succeeded and code == expected:
                    print(f"PASS {command}: failed with the expected {code}", flush=True)
                else:
                    failures.append(command)
                    print(f"FAIL {command}: expected {expected}, got {'success' if succeeded else code}", flush=True)
            elif succeeded:
                print(f"PASS {command}", flush=True)
            else:
                failures.append(command)
                print(f"FAIL {command}: {code}", flush=True)
        except Exception as error:  # noqa: BLE001 - one broken command must not hide the rest
            code = getattr(error, "code", None) or type(error).__name__
            failures.append(command)
            print(f"FAIL {command}: {code}: {error}", flush=True)
    total = len(client_commands())
    print(f"commands-live summary: {total - len(failures)} passed, {len(failures)} failed", flush=True)
    return 0 if not failures else 1


def _fail(check: str, error: Exception) -> int:
    """Report a check that could not run.

    Args:
        check: Check name.
        error: What stopped it.

    Returns:
        Always 1.
    """
    print(f"FAIL {check}: {error}", file=sys.stderr, flush=True)
    return 1


def run_commands_live() -> int:
    """Run every public LMRS command against the deployed server.

    Returns:
        0 when every command passed; nonzero when any command failed, or no
        live server is configured.
    """
    try:
        connection = settings()
        acceptance_models()
    except RuntimeError as error:
        return _fail("commands-live", error)
    return asyncio.run(_drive(LmrsClient(**connection)))


def run_prompt_admission_live() -> int:
    """Prove the service invariant against the live server.

    Four facts are asserted: real text is counted by the runtime tokenizer; a
    fitting prompt is admitted (or queued - both are admission working); an
    output reservation beyond the context window is refused with
    CONTEXT_OVERFLOW in the dry run; and the same oversized request through
    chat is rejected without the runtime ever producing a message.

    Returns:
        0 when the invariant holds, 1 otherwise.
    """
    try:
        connection = settings()
        model, _scratch = acceptance_models()
    except RuntimeError as error:
        return _fail("prompt-admission-live", error)

    async def drive() -> int:
        client = LmrsClient(**connection)
        failures: list[str] = []

        counted = command_payload(await _call(client, "token_count", {"message": "Count the tokens of this sentence.", "model_name": model}))
        breakdown = counted.get("token_breakdown", {})
        if breakdown.get("tokenizer_accuracy") == "runtime_tokenizer" and int(breakdown.get("input_tokens", 0)) > 0:
            print("PASS token-count-text: runtime tokenizer counted the prompt", flush=True)
        else:
            failures.append("token-count-text")
            print(f"FAIL token-count-text: {breakdown}", flush=True)

        fitting = command_payload(await _call(client, "estimate", {"message": "Would this prompt fit?", "model_name": model, "max_tokens": 16}))
        if fitting.get("outcome") in {"would_execute", "would_queue"} and fitting.get("success") is True:
            print(f"PASS estimate-fitting: {fitting.get('outcome')}", flush=True)
        else:
            failures.append("estimate-fitting")
            print(f"FAIL estimate-fitting: {fitting.get('outcome')} {fitting.get('reason_code')}", flush=True)

        oversized = command_payload(await _call(client, "estimate", {"message": "short", "model_name": model, "max_tokens": 10_000_000}))
        if oversized.get("outcome") == "would_reject" and oversized.get("reason_code") == "CONTEXT_OVERFLOW":
            print("PASS estimate-oversized: would_reject CONTEXT_OVERFLOW", flush=True)
        else:
            failures.append("estimate-oversized")
            print(f"FAIL estimate-oversized: {oversized.get('outcome')} {oversized.get('reason_code')}", flush=True)

        rejected = command_payload(await _call(client, "chat", {"message": "short", "model_name": model, "max_tokens": 10_000_000}))
        never_executed = not isinstance(rejected.get("payload"), dict) or "assistant_message" not in rejected.get("payload", {})
        if rejected.get("outcome") == "rejected" and rejected.get("reason_code") == "CONTEXT_OVERFLOW" and never_executed:
            print("PASS chat-oversized: rejected before the runtime", flush=True)
        else:
            failures.append("chat-oversized")
            print(f"FAIL chat-oversized: {rejected.get('outcome')} {rejected.get('reason_code')}", flush=True)

        print(f"prompt-admission-live summary: {4 - len(failures)} passed, {len(failures)} failed", flush=True)
        return 0 if not failures else 1

    return asyncio.run(drive())


_MANDATORY_METADATA_KEYS = {
    "name",
    "summary",
    "detailed_description",
    "parameters",
    "return_value",
    "usage_examples",
    "error_cases",
    "best_practices",
}


def run_info_docs_live() -> int:
    """Verify the live server documents itself exhaustively through info.

    Returns:
        0 when the guide and every command's full documentation are present.
    """
    try:
        connection = settings()
    except RuntimeError as error:
        return _fail("info-docs-live", error)

    async def drive() -> int:
        client = LmrsClient(**connection)
        payload = command_payload(await _call(client, "info", {}))
        failures: list[str] = []

        documentation = payload.get("documentation", {})
        for key in ("purpose", "invariant", "admission_workflow", "command_families"):
            if key not in documentation:
                failures.append(f"documentation.{key}")
        capabilities = payload.get("capabilities", {})
        entries = [entry for family in capabilities.values() for entry in family]
        names = {entry.get("name") for entry in entries}
        listed = {name for family in documentation.get("command_families", {}).values() for name in family}
        if names != listed:
            failures.append(f"families!=surface: {sorted(names ^ listed)}")
        for entry in entries:
            if not entry.get("schema"):
                failures.append(f"{entry.get('name')}: no schema")
            metadata = entry.get("metadata") or {}
            missing = _MANDATORY_METADATA_KEYS - set(metadata)
            if missing:
                failures.append(f"{entry.get('name')}: metadata lacks {sorted(missing)}")
        if failures:
            for failure in failures:
                print(f"FAIL info-docs-live: {failure}", flush=True)
            return 1
        print(f"PASS info-docs-live: {len(entries)} commands fully documented", flush=True)
        return 0

    return asyncio.run(drive())
