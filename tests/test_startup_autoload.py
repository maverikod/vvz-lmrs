"""Tests for startup autoload of the configured default model (C-061).

The default model is preloaded into the disk cache only when absent, then
loaded per policy, without operator intervention; a failure must leave the
server running and visible through model status.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from lmrs.adapter import runtime
from lmrs.model_cache import CacheState


@dataclass
class FakeCacheResult:
    """Minimal stand-in for a CacheCommandResult."""

    status: str
    success: bool = True
    reason_code: str | None = None


@dataclass
class FakeCache:
    """Disk cache double recording the calls a startup autoload makes."""

    cached: bool = False
    preload_succeeds: bool = True
    calls: list[str] = field(default_factory=list)

    def status(self, model_name: str) -> FakeCacheResult:
        """Report whether the model is already cached."""
        self.calls.append(f"status:{model_name}")
        state = CacheState.CACHED_ON_DISK if self.cached else CacheState.NOT_CACHED
        return FakeCacheResult(status=state, success=self.cached)

    def preload(self, model_name: str) -> FakeCacheResult:
        """Record a preload request and report the scripted outcome."""
        self.calls.append(f"preload:{model_name}")
        return FakeCacheResult(
            status=CacheState.CACHED_ON_DISK if self.preload_succeeds else CacheState.NOT_CACHED,
            success=self.preload_succeeds,
            reason_code=None if self.preload_succeeds else "PRELOAD_EXECUTOR_UNAVAILABLE",
        )


@dataclass
class FakeLoadResult:
    """Minimal stand-in for a LifecycleCommandResult."""

    success: bool
    reason_code: str | None = None


@dataclass
class FakeLifecycle:
    """Lifecycle double recording load calls and its resulting state."""

    load_succeeds: bool = True
    raises: bool = False
    calls: list[str] = field(default_factory=list)
    loaded_model: str | None = None

    def load_model(self, model_name: str, allow_preload: bool = False) -> FakeLoadResult:
        """Record a load request and report the scripted outcome."""
        self.calls.append(f"load:{model_name}")
        if self.raises:
            raise RuntimeError("engine start failed")
        if self.load_succeeds:
            self.loaded_model = model_name
            return FakeLoadResult(success=True)
        return FakeLoadResult(success=False, reason_code="MODEL_NOT_SERVED_BY_VLLM")

    def model_status(self, model_name: str) -> FakeLoadResult:
        """Report whether the model ended up resident."""
        return FakeLoadResult(success=self.loaded_model == model_name, reason_code=None if self.loaded_model == model_name else "MODEL_NOT_LOADED")


def _config(default_model: str | None = "demo-model") -> dict[str, Any]:
    """Build a runtime configuration carrying a default model name.

    Args:
        default_model: Value for ``lmrs.default_model_name``; omitted when None.

    Returns:
        A configuration mapping shaped like the generated LMRS config.
    """
    config = runtime.generate_lmrs_config()
    if default_model is not None:
        config["lmrs"]["default_model_name"] = default_model
    return config


def test_uncached_default_model_is_preloaded_before_being_loaded() -> None:
    """An absent default model is preloaded first, then loaded."""
    cache = FakeCache(cached=False)
    lifecycle = FakeLifecycle()

    outcome = runtime.autoload_default_model(_config(), cache=cache, lifecycle=lifecycle)

    assert outcome["attempted"] is True
    assert outcome["preloaded"] is True
    assert outcome["loaded"] is True
    assert cache.calls == ["status:demo-model", "preload:demo-model"]
    assert lifecycle.calls == ["load:demo-model"]


def test_cached_default_model_skips_preload() -> None:
    """An already cached default model is loaded without a preload."""
    cache = FakeCache(cached=True)
    lifecycle = FakeLifecycle()

    outcome = runtime.autoload_default_model(_config(), cache=cache, lifecycle=lifecycle)

    assert outcome["preloaded"] is False
    assert outcome["loaded"] is True
    assert cache.calls == ["status:demo-model"]
    assert lifecycle.calls == ["load:demo-model"]


def test_failed_preload_stops_before_loading() -> None:
    """A preload failure reports a stable reason and never attempts the load."""
    cache = FakeCache(cached=False, preload_succeeds=False)
    lifecycle = FakeLifecycle()

    outcome = runtime.autoload_default_model(_config(), cache=cache, lifecycle=lifecycle)

    assert outcome["loaded"] is False
    assert outcome["reason_code"] == "PRELOAD_EXECUTOR_UNAVAILABLE"
    assert lifecycle.calls == []


def test_load_failure_is_logged_and_does_not_crash_startup() -> None:
    """A raising load is reported through the logger and leaves the model unloaded."""
    cache = FakeCache(cached=True)
    lifecycle = FakeLifecycle(raises=True)
    messages: list[str] = []

    outcome = runtime.autoload_default_model(
        _config(), cache=cache, lifecycle=lifecycle, logger=messages.append
    )

    assert outcome["loaded"] is False
    assert outcome["reason_code"] == "RuntimeError"
    assert len(messages) == 1
    assert "not loaded at startup" in messages[0]
    # A later status query reflects the non-loaded state.
    assert lifecycle.model_status("demo-model").success is False


def test_autoload_is_skipped_without_a_configured_default_model() -> None:
    """No default model in the configuration means nothing is attempted."""
    cache = FakeCache()
    lifecycle = FakeLifecycle()

    outcome = runtime.autoload_default_model(_config(default_model=None), cache=cache, lifecycle=lifecycle)

    assert outcome == {"attempted": False, "reason_code": "NO_DEFAULT_MODEL_CONFIGURED"}
    assert cache.calls == []
    assert lifecycle.calls == []


def test_canonical_startup_path_runs_the_autoload(monkeypatch: pytest.MonkeyPatch) -> None:
    """start_adapter_server autoloads before handing control to the factory."""
    order: list[str] = []

    def _autoload(config: Any, **kwargs: Any) -> dict[str, Any]:
        order.append("autoload")
        return {"attempted": False}

    def _factory(**kwargs: Any) -> str:
        order.append("factory")
        return "served"

    monkeypatch.setattr(runtime, "autoload_default_model", _autoload)

    assert runtime.start_adapter_server("/etc/lmrs/config.json", create_and_run_server=_factory) == "served"
    assert order == ["autoload", "factory"]
