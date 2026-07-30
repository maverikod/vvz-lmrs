"""Proxy-only deployment acceptance for LMRS (C-060).

Drives the whole acceptance flow through the MCP proxy alone: the server is
visible in the registry, it reports active registration, the configured model
is resident, and estimate and chat smoke requests succeed end to end. No model
or GPU is touched outside those proxy calls, which is what makes this an
acceptance of the deployment rather than of the local process.

Exits nonzero when any check fails, so a caller cannot mistake a partial run
for a green one.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class CheckOutcome:
    """Result of one acceptance check.

    Attributes:
        name: Check name shown in the summary.
        passed: Whether the check succeeded.
        detail: Human-readable reason, always filled on failure.
    """

    name: str
    passed: bool
    detail: str


def _entries(payload: Any, *keys: str) -> Any:
    """Read the first present key from a possibly nested response.

    Args:
        payload: The response object returned by the proxy.
        *keys: Candidate keys to look for, outermost first.

    Returns:
        The first value found, or None when the payload carries none of them.
    """
    current: Any = payload
    for key in keys:
        if isinstance(current, dict) and key in current:
            current = current[key]
        else:
            return None
    return current


def check_list_servers(proxy: Any, server_id: str) -> CheckOutcome:
    """Assert the target server is present in the proxy registry.

    Args:
        proxy: Proxy client exposing list_servers.
        server_id: Identifier the server registers under.

    Returns:
        The check outcome.
    """
    try:
        response = proxy.list_servers()
    except Exception as error:  # noqa: BLE001 - a failed call is a failed check
        return CheckOutcome("list_servers", False, f"call failed: {error}")
    servers = _entries(response, "servers") or []
    names = {str(item.get("server_id", "")) for item in servers if isinstance(item, dict)}
    if server_id in names:
        return CheckOutcome("list_servers", True, f"{server_id} is registered")
    return CheckOutcome("list_servers", False, f"{server_id} not in registry: {sorted(names)}")


def check_health(proxy: Any, server_id: str) -> CheckOutcome:
    """Assert the server reports healthy with active registration.

    Args:
        proxy: Proxy client exposing call_server.
        server_id: Identifier the server registers under.

    Returns:
        The check outcome.
    """
    try:
        response = proxy.call_server(server_id, "healthcheck", {})
    except Exception as error:  # noqa: BLE001
        return CheckOutcome("health", False, f"call failed: {error}")
    payload = _entries(response, "payload") or response
    status = _entries(payload, "status")
    if status == "ok":
        return CheckOutcome("health", True, "server reports ok")
    return CheckOutcome("health", False, f"unhealthy or unregistered: {payload!r}")


def check_model_status(proxy: Any, server_id: str, model_name: str) -> CheckOutcome:
    """Assert the acceptance model is resident in memory.

    Args:
        proxy: Proxy client exposing call_server.
        server_id: Identifier the server registers under.
        model_name: Model the deployment should have loaded.

    Returns:
        The check outcome.
    """
    try:
        response = proxy.call_server(server_id, "model_status", {"model_name": model_name})
    except Exception as error:  # noqa: BLE001
        return CheckOutcome("model_status", False, f"call failed: {error}")
    payload = _entries(response, "payload") or response
    state = _entries(payload, "state")
    if state in {"loaded_in_memory", "loaded"}:
        return CheckOutcome("model_status", True, f"{model_name} is {state}")
    return CheckOutcome("model_status", False, f"{model_name} is not loaded: {state!r}")


def check_estimate(proxy: Any, server_id: str, model_name: str) -> CheckOutcome:
    """Assert a small estimate request is admitted.

    Args:
        proxy: Proxy client exposing call_server.
        server_id: Identifier the server registers under.
        model_name: Model the estimate targets.

    Returns:
        The check outcome.
    """
    request = {
        "request_id": "acceptance-estimate",
        "model_name": model_name,
        "token_breakdown": {"input_tokens": 8, "tool_tokens": 0, "service_tokens": 0, "reserved_output_tokens": 8},
        "declared_context_window": 4096,
        "capacity": {"usable_dynamic_vram_bytes": 1 << 30},
        "kv_bytes_per_token": 1024,
        "per_request_overhead_bytes": 0,
        "runtime_batch_overhead_bytes": 0,
    }
    try:
        response = proxy.call_server(server_id, "estimate", request)
    except Exception as error:  # noqa: BLE001
        return CheckOutcome("estimate", False, f"call failed: {error}")
    payload = _entries(response, "payload") or response
    outcome = _entries(payload, "outcome")
    breakdown = _entries(payload, "token_breakdown")
    if outcome in {"would_execute", "would_queue"} and breakdown is not None:
        return CheckOutcome("estimate", True, f"{outcome} with a token breakdown")
    return CheckOutcome("estimate", False, f"not admitted or malformed: {payload!r}")


def check_chat(proxy: Any, server_id: str, model_name: str) -> CheckOutcome:
    """Assert a smoke chat request returns a non-empty assistant message.

    Args:
        proxy: Proxy client exposing call_server.
        server_id: Identifier the server registers under.
        model_name: Model serving the request.

    Returns:
        The check outcome.
    """
    try:
        response = proxy.call_server(
            server_id,
            "chat",
            {"message": "Reply with the single word: ready", "model_name": model_name, "max_tokens": 16},
        )
    except Exception as error:  # noqa: BLE001
        return CheckOutcome("chat", False, f"call failed: {error}")
    payload = _entries(response, "payload") or response
    content = _entries(payload, "message", "content")
    if isinstance(content, str) and content.strip():
        return CheckOutcome("chat", True, "assistant replied")
    return CheckOutcome("chat", False, f"empty or failed reply: {payload!r}")


def run_acceptance_checks(proxy: Any, server_id: str, model_name: str) -> list[CheckOutcome]:
    """Run every acceptance check in order.

    Args:
        proxy: Proxy client the whole acceptance goes through.
        server_id: Identifier the server registers under.
        model_name: Model the deployment should serve.

    Returns:
        One outcome per check, in execution order.
    """
    checks: list[Callable[[], CheckOutcome]] = [
        lambda: check_list_servers(proxy, server_id),
        lambda: check_health(proxy, server_id),
        lambda: check_model_status(proxy, server_id, model_name),
        lambda: check_estimate(proxy, server_id, model_name),
        lambda: check_chat(proxy, server_id, model_name),
    ]
    return [check() for check in checks]


def _build_parser() -> argparse.ArgumentParser:
    """Build the acceptance CLI parser.

    Returns:
        Parser accepting proxy connection settings, the server id and a model
        override for a smaller acceptance profile.
    """
    parser = argparse.ArgumentParser(
        prog="acceptance_e2e",
        description="Verify an LMRS deployment end to end through the MCP proxy.",
    )
    parser.add_argument("--proxy-url", required=True, help="base URL of the MCP proxy")
    parser.add_argument("--server-id", default="lmrs", help="server id LMRS registers under")
    parser.add_argument("--model", default=None, help="override the model with a smaller acceptance profile")
    parser.add_argument("--token", default=None, help="proxy authentication token, when required")
    return parser


def _default_proxy(url: str, token: str | None) -> Any:
    """Build the proxy client used when the caller supplies none.

    Args:
        url: Base URL of the MCP proxy.
        token: Optional authentication token.

    Returns:
        A proxy client exposing list_servers and call_server.

    Raises:
        RuntimeError: If no proxy client is available in this environment.
    """
    try:
        from mcp_proxy_adapter.client.proxy import ProxyClient
    except ImportError as error:  # pragma: no cover - depends on the install
        raise RuntimeError(f"no proxy client available: {error}") from error
    return ProxyClient(url, token=token) if token else ProxyClient(url)


def main(argv: list[str] | None = None, proxy: Any = None) -> int:
    """Run the acceptance flow and report a per-check summary.

    Args:
        argv: Command-line arguments; defaults to ``sys.argv[1:]``.
        proxy: Proxy client override, used by tests.

    Returns:
        0 when every check passed, 1 otherwise.
    """
    args = _build_parser().parse_args(argv)
    model_name = args.model or "default"
    if proxy is None:
        try:
            proxy = _default_proxy(args.proxy_url, args.token)
        except RuntimeError as error:
            print(f"FAIL setup: {error}")
            return 1

    outcomes = run_acceptance_checks(proxy, args.server_id, model_name)
    for outcome in outcomes:
        print(f"{'PASS' if outcome.passed else 'FAIL'} {outcome.name}: {outcome.detail}")
    failed = [outcome.name for outcome in outcomes if not outcome.passed]
    print(f"acceptance summary: {len(outcomes) - len(failed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
