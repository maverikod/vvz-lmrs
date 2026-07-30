"""Tests for the lmrs-client CLI and the client-side pipeline.

The CLI derives its subcommands from the client class, so the surface cannot
drift; these tests pin that derivation, the connection resolution, the verdict
exit code, and the pipeline contract shape (list / one / all).

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

_CLIENT_ROOT = Path(__file__).resolve().parent.parent / "client"
if str(_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLIENT_ROOT))

from lmrs_client import cli as cli_module  # noqa: E402
from lmrs_client import pipeline as client_pipeline  # noqa: E402
from lmrs_client.client import LmrsClient  # noqa: E402
from lmrs_client.verdict import command_payload, verdict  # noqa: E402


def _client_methods() -> set[str]:
    """Return the public client method names."""
    return {
        name
        for name in dir(LmrsClient)
        if not name.startswith("_") and callable(getattr(LmrsClient, name))
    }


def test_the_cli_exposes_exactly_the_client_surface() -> None:
    """Every client method is a subcommand; nothing extra exists."""
    assert client_pipeline.run_cli_surface() == 0


def test_the_cli_surface_check_is_part_of_the_pipeline() -> None:
    """The pipeline registers the offline surface check plus the live checks."""
    names = [check.name for check in client_pipeline.CHECKS]

    assert names == ["cli-surface", "info-docs-live", "prompt-admission-live", "commands-live"]


def test_pipeline_list_prints_every_check(capsys: pytest.CaptureFixture[str]) -> None:
    """pipeline --list enumerates names with descriptions."""
    assert client_pipeline.main(["--list"]) == 0
    output = capsys.readouterr().out
    for check in client_pipeline.CHECKS:
        assert check.name in output


def test_pipeline_rejects_an_unknown_check(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown name exits 2 and names the available checks."""
    assert client_pipeline.main(["no-such-check"]) == 2
    assert "available checks" in capsys.readouterr().out


def test_live_checks_without_a_server_fail_instead_of_skipping(monkeypatch: pytest.MonkeyPatch) -> None:
    """No LMRS_LIVE_HOST means FAIL, never a silent pass."""
    monkeypatch.delenv("LMRS_LIVE_HOST", raising=False)

    from lmrs_client.live_check import run_commands_live, run_info_docs_live, run_prompt_admission_live

    assert run_commands_live() == 1
    assert run_prompt_admission_live() == 1
    assert run_info_docs_live() == 1


def test_cli_runs_a_command_and_exits_by_the_verdict(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A domain failure in the payload makes the CLI exit nonzero."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(self: Any, command: str, params: dict[str, Any] | None = None, *, queued: bool = False) -> Any:
        calls.append((command, dict(params or {})))
        return {"result": {"success": True, "data": {"payload": {"success": False, "reason_code": "MODEL_NOT_CACHED"}}}}

    monkeypatch.setattr(LmrsClient, "_call", fake_call)
    monkeypatch.setattr(LmrsClient, "__init__", lambda self, **kwargs: None)

    status = cli_module.main(["--host", "example", "local-model-cache-status", "--model-name", "absent/model"])

    assert status == 1
    assert calls == [("local_model_cache_status", {"model_name": "absent/model"})]
    printed = json.loads(capsys.readouterr().out)
    assert printed["result"]["data"]["payload"]["reason_code"] == "MODEL_NOT_CACHED"


def test_cli_estimate_takes_prompt_size_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """estimate gets ergonomic text-mode flags despite its **request signature."""
    calls: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(self: Any, command: str, params: dict[str, Any] | None = None, *, queued: bool = False) -> Any:
        calls.append((command, dict(params or {})))
        return {"result": {"success": True, "data": {"payload": {"success": True, "outcome": "would_execute"}}}}

    monkeypatch.setattr(LmrsClient, "_call", fake_call)
    monkeypatch.setattr(LmrsClient, "__init__", lambda self, **kwargs: None)

    status = cli_module.main([
        "--host", "example",
        "estimate", "--message", "will it fit?", "--model-name", "acme/m", "--max-tokens", "64",
    ])

    assert status == 0
    assert calls == [("estimate", {"message": "will it fit?", "model_name": "acme/m", "max_tokens": 64})]


def test_cli_estimate_json_carries_the_raw_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """--json passes the raw admission body through verbatim."""
    calls: list[dict[str, Any]] = []

    async def fake_call(self: Any, command: str, params: dict[str, Any] | None = None, *, queued: bool = False) -> Any:
        calls.append(dict(params or {}))
        return {"result": {"success": True, "data": {"payload": {"success": True}}}}

    monkeypatch.setattr(LmrsClient, "_call", fake_call)
    monkeypatch.setattr(LmrsClient, "__init__", lambda self, **kwargs: None)

    body = {"request_id": "probe", "kv_bytes_per_token": 1024}
    status = cli_module.main(["--host", "example", "estimate", "--json", json.dumps(body)])

    assert status == 0
    assert calls[0]["request_id"] == "probe"
    assert calls[0]["kv_bytes_per_token"] == 1024


def test_cli_without_a_host_exits_with_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """No host anywhere is a usage error, not a guessed connection."""
    monkeypatch.delenv("LMRS_LIVE_HOST", raising=False)

    with pytest.raises(SystemExit) as excinfo:
        cli_module.main(["healthcheck"])

    assert excinfo.value.code == 2


def test_connection_falls_back_to_the_live_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """LMRS_LIVE_* variables configure the connection when flags are absent."""
    seen: dict[str, Any] = {}

    async def fake_call(self: Any, command: str, params: dict[str, Any] | None = None, *, queued: bool = False) -> Any:
        return {"result": {"success": True, "data": {"payload": {"status": "ok"}}}}

    def fake_init(self: Any, **kwargs: Any) -> None:
        seen.update(kwargs)

    monkeypatch.setattr(LmrsClient, "_call", fake_call)
    monkeypatch.setattr(LmrsClient, "__init__", fake_init)
    monkeypatch.setenv("LMRS_LIVE_HOST", "192.0.2.1")
    monkeypatch.setenv("LMRS_LIVE_PORT", "9999")
    monkeypatch.setenv("LMRS_LIVE_PROTOCOL", "https")

    assert cli_module.main(["healthcheck"]) == 0
    assert seen["host"] == "192.0.2.1"
    assert seen["port"] == 9999


def test_verdict_helpers_are_the_shared_implementation() -> None:
    """The payload extractor unwraps envelopes and job envelopes alike."""
    envelope = {"result": {"job_id": "j", "result": {"success": True, "data": {"payload": {"outcome": "executed"}}}}}

    assert command_payload(envelope) == {"outcome": "executed"}
    assert verdict(envelope) == (True, "")
