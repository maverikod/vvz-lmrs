"""Tests for the full model switch (C-061).

switch_model auto-preloads an absent target, unloads the resident model, loads
the target and reports progress; admission rejects new requests with
MODEL_SWITCHING while a switch runs.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from lmrs.adapter.registration import LocalModelSwitchCommand
from lmrs.commands import CanonicalChatHandler, ErrorCode
from lmrs.model_cache import CacheState
from lmrs.model_lifecycle import (
    LifecycleCommandResult,
    LifecycleState,
    ModelResidency,
    model_switch_in_progress,
    switch_model,
)


@dataclass
class FakeCacheResult:
    """Minimal stand-in for a CacheCommandResult."""

    status: str
    success: bool = True
    reason_code: str | None = None


@dataclass
class FakeCache:
    """Disk cache double recording the calls a switch makes."""

    cached: bool = False
    preload_succeeds: bool = True
    calls: list[str] = field(default_factory=list)

    def status(self, model_name: str) -> FakeCacheResult:
        """Report whether the model is already cached."""
        return FakeCacheResult(
            status=CacheState.CACHED_ON_DISK if self.cached else CacheState.NOT_CACHED,
            success=self.cached,
        )

    def preload(self, model_name: str) -> FakeCacheResult:
        """Record a preload request and report the scripted outcome."""
        self.calls.append("preload")
        return FakeCacheResult(
            status=CacheState.CACHED_ON_DISK if self.preload_succeeds else CacheState.NOT_CACHED,
            success=self.preload_succeeds,
            reason_code=None if self.preload_succeeds else "PRELOAD_EXECUTOR_UNAVAILABLE",
        )


@dataclass
class FakeLifecycle:
    """Lifecycle double recording unload/load order and outcomes."""

    current_residency: ModelResidency | None = None
    load_succeeds: bool = True
    unload_succeeds: bool = True
    load_raises: bool = False
    calls: list[str] = field(default_factory=list)

    def unload_model(self, model_name: str) -> LifecycleCommandResult:
        """Record an unload request and report the scripted outcome."""
        self.calls.append("unload")
        if not self.unload_succeeds:
            return LifecycleCommandResult(
                command="unload_model",
                model_name=model_name,
                state=LifecycleState.LOADED,
                success=False,
                reason_code="VLLM_DYNAMIC_UNLOAD_UNSUPPORTED",
            )
        self.current_residency = None
        return LifecycleCommandResult(
            command="unload_model",
            model_name=model_name,
            state=LifecycleState.NOT_LOADED,
            success=True,
        )

    def load_model(self, model_name: str, allow_preload: bool = False) -> LifecycleCommandResult:
        """Record a load request and report the scripted outcome."""
        self.calls.append("load")
        if self.load_raises:
            raise RuntimeError("engine start failed")
        if not self.load_succeeds:
            return LifecycleCommandResult(
                command="load_model",
                model_name=model_name,
                state=LifecycleState.FAILED,
                success=False,
                reason_code="MODEL_NOT_SERVED_BY_VLLM",
            )
        return LifecycleCommandResult(
            command="load_model",
            model_name=model_name,
            state=LifecycleState.LOADED,
            success=True,
            measured_model_static_vram_bytes=1024,
            model_loaded_free_vram_bytes=2048,
        )


def _resident(model_name: str) -> ModelResidency:
    """Build a residency record for a loaded model.

    Args:
        model_name: Name of the resident model.

    Returns:
        A ModelResidency in the loaded state.
    """
    return ModelResidency(model_name=model_name, runtime_backend="vllm", state=LifecycleState.LOADED)


def test_uncached_target_preloads_unloads_and_loads_in_order() -> None:
    """An absent target is preloaded, the resident model unloaded, then the target loaded."""
    cache = FakeCache(cached=False)
    lifecycle = FakeLifecycle(current_residency=_resident("old-model"))
    stages: list[str] = []

    result = switch_model("new-model", stages.append, lifecycle=lifecycle, cache=cache)

    assert stages == ["preloading", "unloading", "loading"]
    assert cache.calls == ["preload"]
    assert lifecycle.calls == ["unload", "load"]
    assert result["status"] == CacheState.LOADED_IN_MEMORY
    assert result["model_name"] == "new-model"
    assert result["runtime_facts"]["measured_model_static_vram_bytes"] == 1024


def test_cached_target_skips_the_preload() -> None:
    """A cached target is switched to without a preload."""
    cache = FakeCache(cached=True)
    lifecycle = FakeLifecycle(current_residency=_resident("old-model"))
    stages: list[str] = []

    result = switch_model("new-model", stages.append, lifecycle=lifecycle, cache=cache)

    assert cache.calls == []
    assert stages == ["unloading", "loading"]
    assert result["status"] == CacheState.LOADED_IN_MEMORY


def test_switch_without_a_resident_model_only_loads() -> None:
    """With nothing resident the switch skips the unload stage."""
    cache = FakeCache(cached=True)
    lifecycle = FakeLifecycle(current_residency=None)
    stages: list[str] = []

    switch_model("new-model", stages.append, lifecycle=lifecycle, cache=cache)

    assert stages == ["loading"]
    assert lifecycle.calls == ["load"]


def test_failed_preload_aborts_with_a_stable_reason() -> None:
    """A preload failure stops the switch before touching residency."""
    cache = FakeCache(cached=False, preload_succeeds=False)
    lifecycle = FakeLifecycle(current_residency=_resident("old-model"))

    result = switch_model("new-model", lifecycle=lifecycle, cache=cache)

    assert result["status"] == LifecycleState.FAILED
    assert result["reason_code"] == "PRELOAD_EXECUTOR_UNAVAILABLE"
    assert result["failed_stage"] == "preloading"
    assert lifecycle.calls == []


def test_failed_unload_aborts_with_a_stable_reason() -> None:
    """An unload failure stops the switch before loading the target."""
    cache = FakeCache(cached=True)
    lifecycle = FakeLifecycle(current_residency=_resident("old-model"), unload_succeeds=False)

    result = switch_model("new-model", lifecycle=lifecycle, cache=cache)

    assert result["status"] == LifecycleState.FAILED
    assert result["reason_code"] == "VLLM_DYNAMIC_UNLOAD_UNSUPPORTED"
    assert lifecycle.calls == ["unload"]


def test_raising_load_returns_failed_instead_of_propagating() -> None:
    """A load that raises is reported as a failed switch, not an exception."""
    cache = FakeCache(cached=True)
    lifecycle = FakeLifecycle(current_residency=None, load_raises=True)

    result = switch_model("new-model", lifecycle=lifecycle, cache=cache)

    assert result["status"] == LifecycleState.FAILED
    assert result["reason_code"] == "RuntimeError"
    assert result["failed_stage"] == "loading"


def test_missing_collaborators_are_refused() -> None:
    """A switch without the caller's lifecycle and cache is refused loudly."""
    with pytest.raises(ValueError):
        switch_model("new-model")


def test_in_progress_flag_is_cleared_after_the_switch() -> None:
    """The in-progress marker is set during the switch and cleared afterwards."""
    seen: list[bool] = []
    cache = FakeCache(cached=True)

    def _observe(stage: str) -> None:
        seen.append(model_switch_in_progress("new-model"))

    switch_model("new-model", _observe, lifecycle=FakeLifecycle(), cache=cache)

    assert seen == [True]
    assert model_switch_in_progress("new-model") is False


def test_in_progress_flag_is_cleared_after_a_raising_switch() -> None:
    """A failed switch also clears the in-progress marker."""
    cache = FakeCache(cached=True)
    switch_model("new-model", lifecycle=FakeLifecycle(load_raises=True), cache=cache)

    assert model_switch_in_progress("new-model") is False


def test_model_switching_error_code_exists() -> None:
    """The stable MODEL_SWITCHING reason code is published."""
    assert ErrorCode.MODEL_SWITCHING == "MODEL_SWITCHING"


def test_admission_rejects_with_model_switching_while_a_switch_runs() -> None:
    """Both entry paths reject immediately with MODEL_SWITCHING during a switch."""
    handler = CanonicalChatHandler(model_switch_in_progress=lambda name: name == "busy-model")
    request: dict[str, Any] = {"model_name": "busy-model", "token_breakdown": {"total": 10}, "capacity": {"usable": 0}}

    chat_result = handler.chat(queue=None, request_metadata={}, queue_metadata={}, **request)
    estimate_result = handler.estimate(**request)

    assert chat_result.reason_code == ErrorCode.MODEL_SWITCHING
    assert estimate_result.reason_code == ErrorCode.MODEL_SWITCHING


def test_admission_is_unchanged_without_a_switch_in_progress(monkeypatch: pytest.MonkeyPatch) -> None:
    """A model that is not switching still reaches the normal admission path."""
    handler = CanonicalChatHandler(model_switch_in_progress=lambda name: name == "busy-model")
    reached: list[str] = []

    @dataclass
    class _Verdict:
        decision: str = "reject"
        reason_code: str = "CONTEXT_OVERFLOW"

    @dataclass
    class _Estimate:
        token_breakdown: Any = None

    def _spy(**request: Any) -> tuple[Any, Any]:
        reached.append(str(request.get("model_name")))
        return _Estimate(), _Verdict()

    monkeypatch.setattr(CanonicalChatHandler, "_estimate_and_admit", staticmethod(_spy))

    idle = handler.estimate(model_name="idle-model", token_breakdown={"total": 10}, capacity={"usable": 0})
    busy = handler.estimate(model_name="busy-model", token_breakdown={"total": 10}, capacity={"usable": 0})

    # The idle model went through admission and kept admission's own reason
    # code; the switching model never reached it.
    assert reached == ["idle-model"]
    assert idle.reason_code == "CONTEXT_OVERFLOW"
    assert busy.reason_code == ErrorCode.MODEL_SWITCHING


def test_no_predicate_leaves_admission_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a switch predicate the handler never short-circuits."""
    handler = CanonicalChatHandler()
    reached: list[str] = []

    @dataclass
    class _Verdict:
        decision: str = "reject"
        reason_code: str = "CONTEXT_OVERFLOW"

    @dataclass
    class _Estimate:
        token_breakdown: Any = None

    def _spy(**request: Any) -> tuple[Any, Any]:
        reached.append(str(request.get("model_name")))
        return _Estimate(), _Verdict()

    monkeypatch.setattr(CanonicalChatHandler, "_estimate_and_admit", staticmethod(_spy))

    result = handler.estimate(model_name="any-model", token_breakdown={"total": 1}, capacity={"usable": 0})

    assert reached == ["any-model"]
    assert result.reason_code == "CONTEXT_OVERFLOW"


def test_switch_command_delegates_with_a_progress_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """LocalModelSwitchCommand delegates to switch_model and reports progress."""
    captured: dict[str, Any] = {}

    def _fake_switch(model_name: str, progress_callback: Any = None, **kwargs: Any) -> dict[str, Any]:
        captured["model_name"] = model_name
        captured["kwargs"] = kwargs
        progress_callback("loading")
        return {"status": CacheState.LOADED_IN_MEMORY, "model_name": model_name, "runtime_facts": {}}

    monkeypatch.setattr("lmrs.adapter.registration.switch_model", _fake_switch)

    result = asyncio.run(LocalModelSwitchCommand().execute(model_name="new-model"))
    payload = result.to_dict()["data"]["payload"]

    assert captured["model_name"] == "new-model"
    assert set(captured["kwargs"]) == {"lifecycle", "cache"}
    assert payload["progress"] == ["loading"]
    assert payload["status"] == CacheState.LOADED_IN_MEMORY


def test_switch_command_is_queued() -> None:
    """The switch is long-running, so the command runs through the queue."""
    assert LocalModelSwitchCommand.use_queue is True
