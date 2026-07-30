"""Tests for the proxy-only deployment acceptance script (C-060).

Each check is driven against a fake proxy returning scripted responses, so the
acceptance contract is pinned without a deployed server.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from acceptance_e2e import (  # noqa: E402  (path set up above)
    check_chat,
    check_estimate,
    check_health,
    check_list_servers,
    check_model_status,
    main,
    run_acceptance_checks,
)


class FakeProxy:
    """Proxy double returning scripted responses per command."""

    def __init__(self, servers: list[str] | None = None, responses: dict[str, Any] | None = None) -> None:
        """Script the registry contents and per-command responses.

        Args:
            servers: server_id values list_servers reports.
            responses: Mapping of command name to the response to return.
        """
        self._servers = servers if servers is not None else ["lmrs"]
        self._responses = responses or {}
        self.calls: list[str] = []

    def list_servers(self) -> dict[str, Any]:
        """Return the scripted registry listing."""
        self.calls.append("list_servers")
        return {"servers": [{"server_id": name} for name in self._servers]}

    def call_server(self, server_id: str, command: str, params: dict[str, Any]) -> Any:
        """Return the scripted response for one command.

        Args:
            server_id: Target server id.
            command: Command name.
            params: Command parameters, recorded but not inspected.

        Returns:
            The scripted response.

        Raises:
            RuntimeError: When the script says this command should fail.
        """
        self.calls.append(command)
        response = self._responses.get(command, {})
        if isinstance(response, Exception):
            raise response
        return response


def _healthy_responses(model_state: str = "loaded_in_memory") -> dict[str, Any]:
    """Build a full set of passing responses.

    Args:
        model_state: State the model_status check should see.

    Returns:
        Scripted responses for every command the acceptance flow calls.
    """
    return {
        "healthcheck": {"payload": {"status": "ok"}},
        "model_status": {"payload": {"state": model_state}},
        "estimate": {"payload": {"outcome": "would_execute", "token_breakdown": {"input_tokens": 8}}},
        "chat": {"payload": {"message": {"content": "ready"}}},
    }


def test_list_servers_passes_when_the_target_is_registered() -> None:
    """The registry check passes when the server id is present."""
    assert check_list_servers(FakeProxy(servers=["lmrs", "other"]), "lmrs").passed


def test_list_servers_fails_when_the_target_is_absent() -> None:
    """The registry check fails, naming what it did see."""
    outcome = check_list_servers(FakeProxy(servers=["other"]), "lmrs")

    assert not outcome.passed
    assert "other" in outcome.detail


def test_health_passes_only_on_an_ok_status() -> None:
    """The health check distinguishes a healthy server from an unhealthy one."""
    assert check_health(FakeProxy(responses=_healthy_responses()), "lmrs").passed
    assert not check_health(FakeProxy(responses={"healthcheck": {"payload": {"status": "down"}}}), "lmrs").passed


def test_model_status_passes_only_when_the_model_is_loaded() -> None:
    """The model check requires the model to be resident."""
    assert check_model_status(FakeProxy(responses=_healthy_responses()), "lmrs", "m").passed

    not_loaded = FakeProxy(responses=_healthy_responses(model_state="not_loaded"))
    assert not check_model_status(not_loaded, "lmrs", "m").passed


def test_estimate_passes_on_an_admitted_outcome() -> None:
    """An admitted estimate with a token breakdown passes."""
    assert check_estimate(FakeProxy(responses=_healthy_responses()), "lmrs", "m").passed

    queued = FakeProxy(responses={"estimate": {"payload": {"outcome": "would_queue", "token_breakdown": {}}}})
    assert check_estimate(queued, "lmrs", "m").passed


def test_estimate_fails_on_rejection_or_a_malformed_response() -> None:
    """A rejected or shapeless estimate fails."""
    rejected = FakeProxy(responses={"estimate": {"payload": {"outcome": "would_reject", "token_breakdown": {}}}})
    malformed = FakeProxy(responses={"estimate": {"payload": {"outcome": "would_execute"}}})

    assert not check_estimate(rejected, "lmrs", "m").passed
    assert not check_estimate(malformed, "lmrs", "m").passed


def test_chat_passes_only_on_a_non_empty_reply() -> None:
    """The chat smoke check requires actual generated content."""
    assert check_chat(FakeProxy(responses=_healthy_responses()), "lmrs", "m").passed

    empty = FakeProxy(responses={"chat": {"payload": {"message": {"content": "   "}}}})
    assert not check_chat(empty, "lmrs", "m").passed


def test_a_raising_call_is_a_failed_check_not_a_crash() -> None:
    """A transport error is reported as a failed check."""
    proxy = FakeProxy(responses={"healthcheck": RuntimeError("connection refused")})

    outcome = check_health(proxy, "lmrs")

    assert not outcome.passed
    assert "connection refused" in outcome.detail


def test_every_check_runs_even_when_an_early_one_fails() -> None:
    """The flow does not stop at the first failure."""
    responses = _healthy_responses()
    responses["healthcheck"] = {"payload": {"status": "down"}}
    proxy = FakeProxy(responses=responses)

    outcomes = run_acceptance_checks(proxy, "lmrs", "m")

    assert [outcome.name for outcome in outcomes] == [
        "list_servers",
        "health",
        "model_status",
        "estimate",
        "chat",
    ]
    assert sum(1 for outcome in outcomes if not outcome.passed) == 1


def test_main_exits_zero_only_when_every_check_passes(capsys: Any) -> None:
    """The script's exit status is the acceptance verdict."""
    argv = ["--proxy-url", "https://proxy.example", "--server-id", "lmrs", "--model", "m"]

    assert main(argv, proxy=FakeProxy(responses=_healthy_responses())) == 0

    failing = _healthy_responses()
    failing["chat"] = {"payload": {"message": {"content": ""}}}
    assert main(argv, proxy=FakeProxy(responses=failing)) == 1

    assert "acceptance summary" in capsys.readouterr().out


def test_main_fails_when_the_server_is_not_registered() -> None:
    """A deployment the proxy cannot see is not accepted."""
    argv = ["--proxy-url", "https://proxy.example", "--server-id", "lmrs"]

    assert main(argv, proxy=FakeProxy(servers=["other"], responses=_healthy_responses())) == 1
