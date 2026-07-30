"""Tests for the admitted chat path and the measured capacity behind it.

The invariant under test is the one the service exists for: a prompt that does
not fit must be refused with a stable reason code and must never reach the
runtime. The runtime double therefore records every call it receives, and the
rejection tests assert that record is empty.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Mapping

import pytest

from lmrs.adapter import registration
from lmrs.commands import CommandOutcome, ErrorCode
from lmrs.model_cache import DiskModelCache, repo_directory_name
from lmrs.model_lifecycle import ModelMemoryLifecycle, model_switch_in_progress
from lmrs.runtime_client import RuntimeBackend, RuntimeClient, VLLMModelProbeResult
from lmrs.vram import GpuMemoryMeasurement, VramFactsStore

MODEL = "acme/tiny-model"
REVISION = "b1946ac92492d2347c6235b4d2611184"
_GIB = 1024**3


class FakeVllm:
    """A vLLM client double that answers from a script and records calls."""

    def __init__(self, *, served: bool = True, prompt_tokens: int | None = 10) -> None:
        """Build the double.

        Args:
            served: Whether the runtime reports the model as served.
            prompt_tokens: Token count to report, or None for no answer.
        """
        self.base_url = "http://127.0.0.1:8000"
        self.served = served
        self.prompt_tokens = prompt_tokens
        self.completions: list[dict[str, Any]] = []

    def is_model_served(self, model_name: str) -> VLLMModelProbeResult:
        """Report whether the runtime serves the model."""
        if not self.served:
            return VLLMModelProbeResult(False, (), "model is not reported by vLLM /v1/models")
        return VLLMModelProbeResult(True, (model_name,))

    def list_models(self) -> VLLMModelProbeResult:
        """Report the served models."""
        return VLLMModelProbeResult(self.served, (MODEL,) if self.served else ())

    def count_prompt_tokens(self, model_name: str, messages: object) -> int | None:
        """Report the scripted prompt token count."""
        return self.prompt_tokens

    def chat_completion(self, model_name: str, messages: object, **options: object) -> Mapping[str, object]:
        """Record the call and answer with a completion."""
        self.completions.append({"model_name": model_name, "messages": messages, "options": options})
        return {
            "id": "cmpl-1",
            "model": model_name,
            "choices": [{"message": {"role": "assistant", "content": "ready"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
        }


def _cache_with_model(tmp_path: Path, *, context_window: int = 4096) -> DiskModelCache:
    """Create a disk cache holding one complete model.

    Args:
        tmp_path: Directory to build the cache in.
        context_window: Context window the model config declares.

    Returns:
        A DiskModelCache rooted at that directory.
    """
    repo = tmp_path / repo_directory_name(MODEL)
    snapshot = repo / "snapshots" / REVISION
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(REVISION, encoding="utf-8")
    (snapshot / "config.json").write_text(
        json.dumps({
            "num_hidden_layers": 4,
            "num_attention_heads": 8,
            "num_key_value_heads": 2,
            "head_dim": 64,
            "torch_dtype": "bfloat16",
            "max_position_embeddings": context_window,
        }),
        encoding="utf-8",
    )
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    blob = blobs / "weights"
    blob.write_bytes(b"0" * 512)
    (snapshot / "model.safetensors").symlink_to(blob)
    return DiskModelCache(cache_root=str(tmp_path))


@pytest.fixture
def wired(tmp_path: Path, monkeypatch) -> FakeVllm:
    """Point the adapter singletons at a temporary, measurable environment.

    Args:
        tmp_path: Directory for the cache and the VRAM facts file.
        monkeypatch: Fixture replacing the module-level collaborators.

    Returns:
        The vLLM double the adapter now talks to.
    """
    vllm = FakeVllm()
    cache = _cache_with_model(tmp_path / "cache")
    store = VramFactsStore(path=str(tmp_path / "vram-facts.json"))
    store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 23 * _GIB, 1 * _GIB), ("vectorizer",))
    monkeypatch.setattr(registration, "_CACHE", cache)
    monkeypatch.setattr(registration, "_VLLM_CLIENT", vllm)
    monkeypatch.setattr(registration, "_VRAM_STORE", store)
    monkeypatch.setattr(registration, "_RUNTIME_CLIENT", RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=vllm))
    monkeypatch.setattr(registration, "_LIFECYCLE", ModelMemoryLifecycle(runtime_backend=RuntimeBackend.VLLM, model_probe=vllm.is_model_served, disk_cache=cache))
    monkeypatch.setattr(registration, "_QUEUE", registration._QueueHolder())
    monkeypatch.setattr(registration, "measure_gpu_memory", lambda *args, **kwargs: GpuMemoryMeasurement(True, 24 * _GIB, 8 * _GIB, 16 * _GIB))
    return vllm


def _chat(**params: Any) -> dict[str, Any]:
    """Run the chat command and return its adapter payload.

    Args:
        **params: Command parameters.

    Returns:
        The command result payload.
    """
    result = asyncio.run(registration.ChatCommand().execute(**params))
    return dict(result.to_dict()["data"]["payload"])


def test_a_fitting_request_is_admitted_and_executed(wired: FakeVllm) -> None:
    """An admitted request reaches the runtime and carries its reply."""
    payload = _chat(message="say ready", model_name=MODEL, max_tokens=16)

    assert payload["outcome"] == CommandOutcome.EXECUTED
    assert payload["success"] is True
    assert payload["payload"]["assistant_message"] == "ready"
    assert payload["token_breakdown"]["input_tokens"] == 10
    assert payload["token_breakdown"]["rough_estimate"] is False
    assert len(wired.completions) == 1


def test_an_oversized_prompt_never_reaches_the_runtime(tmp_path: Path, monkeypatch, wired: FakeVllm) -> None:
    """A prompt longer than the context window is rejected before execution."""
    wired.prompt_tokens = 5000

    payload = _chat(message="a very long prompt", model_name=MODEL, max_tokens=16)

    assert payload["outcome"] == CommandOutcome.REJECTED
    assert payload["success"] is False
    assert payload["reason_code"] == ErrorCode.CONTEXT_OVERFLOW
    assert wired.completions == []


def test_a_request_too_large_for_the_pool_is_rejected(wired: FakeVllm, monkeypatch) -> None:
    """A request that cannot fit the whole dynamic pool is refused, not queued."""
    monkeypatch.setattr(registration, "measure_gpu_memory", lambda *args, **kwargs: GpuMemoryMeasurement(True, 24 * _GIB, 1024, 24 * _GIB))

    payload = _chat(message="say ready", model_name=MODEL, max_tokens=1024)

    assert payload["outcome"] == CommandOutcome.REJECTED
    assert payload["reason_code"] == ErrorCode.REQUEST_TOO_LARGE
    assert wired.completions == []


def test_a_capacity_constrained_request_is_queued(wired: FakeVllm, monkeypatch) -> None:
    """A request that fits the pool but not the free VRAM waits in the queue."""
    from lmrs.admission import CapacitySnapshot

    monkeypatch.setattr(
        registration,
        "capacity_snapshot",
        lambda model_name: CapacitySnapshot(
            usable_dynamic_vram_bytes=1024,
            max_dynamic_pool_bytes=8 * _GIB,
            model_loaded=True,
            runtime_ready=True,
        ),
    )

    payload = _chat(message="say ready", model_name=MODEL, max_tokens=16)

    assert payload["outcome"] == CommandOutcome.QUEUED
    assert wired.completions == []
    assert registration._QUEUE.snapshot()[0]["model_name"] == MODEL
    assert registration._QUEUE.reserved_bytes() > 0


def test_an_uncached_model_cannot_be_sized_and_is_refused(wired: FakeVllm) -> None:
    """Without a cached config there is no KV cost, so the request is refused."""
    payload = _chat(message="say ready", model_name="absent/model", max_tokens=16)

    assert payload["success"] is False
    assert payload["reason_code"] == ErrorCode.HARDWARE_CAPACITY_UNKNOWN
    assert wired.completions == []


def test_a_switch_in_progress_rejects_before_admission(wired: FakeVllm, monkeypatch) -> None:
    """A model mid-switch is treated as not loaded."""
    monkeypatch.setattr(registration._CHAT_HANDLER, "model_switch_in_progress", lambda name: True)

    payload = _chat(message="say ready", model_name=MODEL, max_tokens=16)

    assert payload["reason_code"] == ErrorCode.MODEL_SWITCHING
    assert wired.completions == []


def test_the_switch_predicate_is_wired_into_the_handler() -> None:
    """The adapter handler consults the real switch registry."""
    assert registration._CHAT_HANDLER.model_switch_in_progress is model_switch_in_progress


def test_a_runtime_without_a_tokenizer_falls_back_and_says_so(wired: FakeVllm) -> None:
    """A rough count is marked rough rather than passed off as exact."""
    wired.prompt_tokens = None

    payload = _chat(message="12345678", model_name=MODEL, max_tokens=16)

    assert payload["token_breakdown"]["rough_estimate"] is True
    assert payload["token_breakdown"]["input_tokens"] == 2
    assert payload["token_breakdown"]["tokenizer_accuracy"] == "rough"


def test_capacity_reports_measured_facts(wired: FakeVllm) -> None:
    """The capacity command reports measurements and derived pools."""
    payload = registration.vram_payload()

    assert payload["measured"] is True
    assert payload["service_baseline_free_vram_bytes"] == 23 * _GIB
    assert payload["model_loaded_free_vram_bytes"] == 8 * _GIB
    assert payload["measured_model_static_vram_bytes"] == 15 * _GIB
    assert payload["usable_dynamic_vram_bytes"] == 8 * _GIB
    assert payload["resident_services"] == []


def test_a_baseline_is_not_recorded_while_the_runtime_serves_a_model(tmp_path: Path, wired: FakeVllm, monkeypatch) -> None:
    """A first reading taken with weights resident yields no static VRAM."""
    monkeypatch.setattr(registration, "_VRAM_STORE", VramFactsStore(path=str(tmp_path / "fresh-facts.json")))

    payload = registration.vram_payload()

    assert payload["measured"] is True
    assert payload["measured_model_static_vram_bytes"] is None
    assert payload["service_baseline_free_vram_bytes"] is None
    assert payload["measurement_metadata"]["baseline_model_served"] is True


def test_capacity_reports_an_unreadable_gpu_as_unmeasured(wired: FakeVllm, monkeypatch) -> None:
    """Without a reading the pools are zero and measured is false."""
    monkeypatch.setattr(registration, "measure_gpu_memory", lambda *args, **kwargs: GpuMemoryMeasurement(False, error="driver unavailable"))
    monkeypatch.setattr(registration, "_VRAM_STORE", VramFactsStore(path=str(Path("/nonexistent/vram-facts.json"))))

    payload = registration.vram_payload()

    assert payload["measured"] is False
    assert payload["usable_dynamic_vram_bytes"] == 0
    assert payload["model_loaded_free_vram_bytes"] is None
    assert payload["measurement_metadata"]["error"] == "driver unavailable"


def test_lmcache_observations_measure_the_disk_tier(tmp_path: Path, monkeypatch) -> None:
    """The disk tier usage is measured from the storage path."""
    from lmrs.lmcache import LMCacheStoragePolicy

    storage = tmp_path / "lmcache" / "namespace" / "session"
    storage.mkdir(parents=True)
    (storage / "chunk-1").write_bytes(b"0" * 2048)
    monkeypatch.setattr(registration, "_LMCACHE_POLICY", LMCacheStoragePolicy(enabled=True, cache_storage_path=str(tmp_path / "lmcache")))

    observations = registration._lmcache_observations()

    assert observations["disk_cache_usage"] == 2048
    assert observations["metadata"]["entry_count"] == 1
    assert observations["metadata"]["disk_tier_observed"] is True
