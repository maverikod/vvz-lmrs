"""Pin the pipeline CLI invocation contract against fake checks.

Drives ``pipeline.cli.main`` with a registry of fake checks whose run
callables only record that they were called and return a scripted status. The
real pytest/ruff/flake8/mypy checks are never run here, so the test stays fast
and does not depend on those tools being installed. The critical assertion is
that a full run does NOT stop at the first failing check: a runner that aborts
early hides everything after the first failure.
"""

from __future__ import annotations

import pytest

from pipeline.cli import main
from pipeline.registry import Check, CheckRegistry


def _make_registry(statuses: dict[str, int], calls: list[str]) -> CheckRegistry:
    """Build a registry of fake checks recording their invocations.

    Args:
        statuses: Mapping of check name to the exit status its run returns.
        calls: Shared list every fake run appends its check name to.

    Returns:
        CheckRegistry: Registry with one fake check per statuses entry,
        registered in mapping order.
    """
    registry = CheckRegistry()
    for name, status in statuses.items():
        def run(name: str = name, status: int = status) -> int:
            calls.append(name)
            return status

        registry.register(Check(name, f"fake check {name}", run))
    return registry


def test_list_prints_every_name_and_runs_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    """--list prints every registered name and invokes no check."""
    calls: list[str] = []
    registry = _make_registry({"alpha": 0, "beta": 0, "gamma": 0}, calls)

    assert main(["--list"], registry=registry) == 0

    output = capsys.readouterr().out
    for name in ("alpha", "beta", "gamma"):
        assert name in output
    assert calls == []


def test_full_run_invokes_every_check_once_in_order() -> None:
    """A no-arguments run invokes every check exactly once, in registration order."""
    calls: list[str] = []
    registry = _make_registry({"alpha": 0, "beta": 0, "gamma": 0}, calls)

    assert main([], registry=registry) == 0
    assert calls == ["alpha", "beta", "gamma"]


def test_full_run_does_not_stop_at_first_failure() -> None:
    """An early failure still leaves every later check invoked."""
    calls: list[str] = []
    registry = _make_registry({"alpha": 1, "beta": 0, "gamma": 2}, calls)

    assert main([], registry=registry) != 0
    assert calls == ["alpha", "beta", "gamma"]


def test_full_run_exit_status_aggregation() -> None:
    """A full run returns 0 only when every check passes."""
    all_pass: list[str] = []
    assert main([], registry=_make_registry({"a": 0, "b": 0}, all_pass)) == 0

    one_fails: list[str] = []
    assert main([], registry=_make_registry({"a": 0, "b": 3}, one_fails)) != 0


def test_single_check_runs_only_that_check_and_returns_its_status() -> None:
    """Naming one check invokes only that check and propagates its status."""
    calls: list[str] = []
    registry = _make_registry({"alpha": 0, "beta": 5}, calls)

    assert main(["beta"], registry=registry) == 5
    assert calls == ["beta"]


def test_unknown_name_returns_nonzero_without_invoking_anything(capsys: pytest.CaptureFixture[str]) -> None:
    """An unknown check name exits nonzero, prints the available names, runs nothing."""
    calls: list[str] = []
    registry = _make_registry({"alpha": 0, "beta": 0}, calls)

    assert main(["nope"], registry=registry) != 0

    output = capsys.readouterr().out
    assert "alpha" in output and "beta" in output
    assert calls == []
