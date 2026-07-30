"""Tests for the disk-cache gate and the VRAM measurement of a model load.

Loading is only allowed for a model that is actually on disk, and a load that
succeeds must record what it cost in VRAM. Both are contract requirements, so
both are pinned here.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from lmrs.model_cache import CacheState
from lmrs.model_lifecycle import LifecycleState, ModelMemoryLifecycle
from lmrs.runtime_client import VLLMModelProbeResult

_GIB = 1024**3


@dataclass
class FakeCacheResult:
    """Minimal stand-in for a CacheCommandResult."""

    status: str
    success: bool = True
    reason_code: str | None = None


@dataclass
class FakeCache:
    """Disk cache double reporting a scripted state."""

    cached: bool = True

    def status(self, model_name: str) -> FakeCacheResult:
        """Report the scripted disk state."""
        if self.cached:
            return FakeCacheResult(CacheState.CACHED_ON_DISK, True)
        return FakeCacheResult(CacheState.NOT_CACHED, False, "MODEL_NOT_CACHED")

    def preload(self, model_name: str) -> FakeCacheResult:
        """Preload is never expected in these tests."""
        raise AssertionError("the lifecycle must not preload on its own")


def _served(model_name: str) -> VLLMModelProbeResult:
    """Report the model as served by the runtime."""
    return VLLMModelProbeResult(True, (model_name,))


def test_a_model_absent_from_disk_is_not_loaded() -> None:
    """An uncached model is refused with MODEL_NOT_CACHED."""
    lifecycle = ModelMemoryLifecycle(model_probe=_served, disk_cache=FakeCache(cached=False))

    result = lifecycle.load_model("acme/tiny-model")

    assert result.success is False
    assert result.reason_code == "MODEL_NOT_CACHED"
    assert result.state == LifecycleState.NOT_LOADED
    assert lifecycle.current_residency is None


def test_allow_preload_skips_the_disk_gate() -> None:
    """An explicit allow_preload lets the caller own the cache decision."""
    lifecycle = ModelMemoryLifecycle(model_probe=_served, disk_cache=FakeCache(cached=False))

    result = lifecycle.load_model("acme/tiny-model", allow_preload=True)

    assert result.success is True
    assert result.state == LifecycleState.LOADED


def test_a_cached_model_passes_the_gate_and_records_vram() -> None:
    """A successful load stores the measured VRAM facts on the residency."""
    calls: list[str] = []

    def recorder(model_name: str) -> Mapping[str, Any]:
        calls.append(model_name)
        return {
            "model_loaded_free_vram_bytes": 8 * _GIB,
            "measured_model_static_vram_bytes": 15 * _GIB,
        }

    lifecycle = ModelMemoryLifecycle(model_probe=_served, disk_cache=FakeCache(), vram_recorder=recorder)

    result = lifecycle.load_model("acme/tiny-model")

    assert calls == ["acme/tiny-model"]
    assert result.measured_model_static_vram_bytes == 15 * _GIB
    assert result.model_loaded_free_vram_bytes == 8 * _GIB
    assert lifecycle.current_residency is not None
    assert lifecycle.current_residency.measured_model_static_vram_bytes == 15 * _GIB
    assert lifecycle.model_status("acme/tiny-model").measured_model_static_vram_bytes == 15 * _GIB


def test_an_unmeasurable_gpu_leaves_the_facts_absent() -> None:
    """A load whose VRAM could not be measured records no invented figure."""
    lifecycle = ModelMemoryLifecycle(
        model_probe=_served,
        disk_cache=FakeCache(),
        vram_recorder=lambda model_name: {"measurement_error": "driver unavailable"},
    )

    result = lifecycle.load_model("acme/tiny-model")

    assert result.success is True
    assert result.measured_model_static_vram_bytes is None
    assert result.metadata["vram_facts"] == {"measurement_error": "driver unavailable"}


def test_a_model_the_runtime_does_not_serve_is_not_resident() -> None:
    """The runtime probe still decides residency after the disk gate passes."""
    lifecycle = ModelMemoryLifecycle(
        model_probe=lambda name: VLLMModelProbeResult(False, (), "model is not reported by vLLM /v1/models"),
        disk_cache=FakeCache(),
    )

    result = lifecycle.load_model("acme/tiny-model")

    assert result.success is False
    assert result.reason_code == "MODEL_NOT_SERVED_BY_VLLM"
    assert lifecycle.current_residency is None
