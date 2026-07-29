"""Tests pinning the single canonical LMRS startup path (C-050).

Both ``lmrs.__main__`` and ``lmrs.adapter.runtime.run_lmrs_adapter`` must
delegate to one shared startup function; neither may reimplement the
registration-plus-factory sequence.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any

import pytest

import lmrs.__main__ as entrypoint
from lmrs.adapter import runtime


def _valid_config() -> dict[str, Any]:
    """Return a configuration that passes validate_lmrs_config.

    Returns:
        The generated default LMRS configuration.
    """
    return runtime.generate_lmrs_config()


def test_entrypoint_delegates_to_the_shared_startup_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """lmrs.__main__.main calls the shared startup function exactly once."""
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def _spy(*args: Any, **kwargs: Any) -> str:
        calls.append((args, kwargs))
        return "started"

    monkeypatch.setattr(runtime, "start_adapter_server", _spy)

    entrypoint.main(["--config", "/etc/lmrs/config.json"])

    assert len(calls) == 1
    assert calls[0][0] == ("/etc/lmrs/config.json",)


def test_run_lmrs_adapter_delegates_to_the_shared_startup_function(monkeypatch: pytest.MonkeyPatch) -> None:
    """run_lmrs_adapter routes the server start through the same shared function."""
    calls: list[dict[str, Any]] = []

    def _spy(*args: Any, **kwargs: Any) -> str:
        calls.append(kwargs)
        return "started"

    monkeypatch.setattr(runtime, "start_adapter_server", _spy)

    def _factory(**kwargs: Any) -> str:
        raise AssertionError("run_lmrs_adapter must not call the server factory itself")

    result = runtime.run_lmrs_adapter(
        config_loader=_valid_config,
        create_and_run_server=_factory,
    )

    assert result == "started"
    assert len(calls) == 1
    assert calls[0]["create_and_run_server"] is _factory
    assert calls[0]["config"]["lmrs"]["runtime_backends"] == {"default": "vllm"}


def test_no_second_factory_call_site_exists_in_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the shared function patched out, the injected factory is never reached."""
    monkeypatch.setattr(runtime, "start_adapter_server", lambda *args, **kwargs: "started")
    factory_calls: list[dict[str, Any]] = []

    def _factory(**kwargs: Any) -> str:
        factory_calls.append(kwargs)
        return "factory"

    runtime.run_lmrs_adapter(config_loader=_valid_config, create_and_run_server=_factory)

    assert factory_calls == []


def test_shared_startup_registers_commands_before_calling_the_factory() -> None:
    """The canonical path installs command registration, then calls the factory."""
    order: list[str] = []

    def _factory(**kwargs: Any) -> str:
        import sys

        order.append("factory")
        assert "lmrs.adapter.registration" in sys.modules
        return "served"

    result = runtime.start_adapter_server("/etc/lmrs/config.json", create_and_run_server=_factory)

    assert result == "served"
    assert order == ["factory"]


def test_shared_startup_runs_an_awaitable_factory_result() -> None:
    """An async factory result is run through the supplied runner."""
    runner_calls: list[Any] = []

    async def _async_factory(**kwargs: Any) -> str:
        return "awaited"

    def _runner(awaitable: Any) -> str:
        runner_calls.append(awaitable)
        awaitable.close()
        return "ran"

    result = runtime.start_adapter_server(
        "/etc/lmrs/config.json",
        create_and_run_server=_async_factory,
        runner=_runner,
    )

    assert result == "ran"
    assert len(runner_calls) == 1


def test_run_lmrs_adapter_without_a_factory_starts_no_server() -> None:
    """The no-factory seam prepares startup state without starting a server."""
    result = runtime.run_lmrs_adapter(config_loader=_valid_config)

    assert result["server_result"] is None
    assert result["lifecycle_services_started"] == 0
