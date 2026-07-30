"""Tests for measured VRAM facts and their persistence.

Admission decides against these numbers, so the rules under test are: a reading
that did not happen is never reported as zero-but-fine, and a static-VRAM figure
exists only when a service baseline was measured before the model was loaded.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

from lmrs.vram import (
    GpuMemoryMeasurement,
    VramFactsStore,
    measure_gpu_memory,
)

_GIB = 1024**3


def _runner(stdout: str, status: int = 0, stderr: str = ""):
    """Return a runner answering with a canned nvidia-smi result.

    Args:
        stdout: Standard output to return.
        status: Exit status to return.
        stderr: Standard error to return.

    Returns:
        A callable with the runner signature.
    """
    def run(command: Sequence[str]) -> tuple[int, str, str]:
        return status, stdout, stderr

    return run


def test_measurement_sums_the_visible_devices() -> None:
    """Per-device readings are parsed from MiB and summed."""
    measurement = measure_gpu_memory(_runner("0, 24576, 20480, 4096\n1, 24576, 12288, 12288\n"))

    assert measurement.ok is True
    assert measurement.total_bytes == 48 * 1024 * _GIB // 1024
    assert measurement.free_bytes == 32 * 1024 * _GIB // 1024
    assert len(measurement.devices) == 2
    assert measurement.devices[0]["free_bytes"] == 20480 * 1024 * 1024


def test_a_driver_failure_is_reported_not_raised() -> None:
    """A failing driver yields ok=False with the error text."""
    measurement = measure_gpu_memory(_runner("", status=9, stderr="couldn't communicate with the NVIDIA driver"))

    assert measurement.ok is False
    assert measurement.free_bytes == 0
    assert measurement.error is not None
    assert "NVIDIA driver" in measurement.error


def test_a_missing_tool_is_reported_not_raised() -> None:
    """An absent nvidia-smi is a measurement failure, not an exception."""
    def missing(command: Sequence[str]) -> tuple[int, str, str]:
        raise FileNotFoundError("nvidia-smi is not installed")

    measurement = measure_gpu_memory(missing)

    assert measurement.ok is False
    assert "FileNotFoundError" in str(measurement.error)


def test_unparsable_output_is_not_taken_as_a_measurement() -> None:
    """Output with no device rows does not become a zero reading."""
    measurement = measure_gpu_memory(_runner("no devices were found\n"))

    assert measurement.ok is False


def test_the_store_derives_static_vram_from_two_measurements(tmp_path: Path) -> None:
    """Static VRAM is the drop between baseline and model-loaded readings."""
    store = VramFactsStore(path=str(tmp_path / "vram-facts.json"))
    store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 23 * _GIB, 1 * _GIB), ("vectorizer",))

    facts = store.record_model_loaded("acme/tiny-model", GpuMemoryMeasurement(True, 24 * _GIB, 8 * _GIB, 16 * _GIB), "vllm")

    assert facts["measured_model_static_vram_bytes"] == 15 * _GIB
    assert facts["resident_services"] == ["vectorizer"]
    assert json.loads((tmp_path / "vram-facts.json").read_text(encoding="utf-8"))["model_name"] == "acme/tiny-model"


def test_without_a_baseline_no_static_vram_is_invented(tmp_path: Path) -> None:
    """A model-loaded reading alone yields no static-VRAM figure."""
    store = VramFactsStore(path=str(tmp_path / "vram-facts.json"))

    facts = store.record_model_loaded("acme/tiny-model", GpuMemoryMeasurement(True, 24 * _GIB, 8 * _GIB, 16 * _GIB))

    assert "measured_model_static_vram_bytes" not in facts
    assert facts["static_vram_unavailable_reason"]


def test_a_failed_reading_never_overwrites_stored_facts(tmp_path: Path) -> None:
    """A measurement that did not happen leaves the stored facts intact."""
    store = VramFactsStore(path=str(tmp_path / "vram-facts.json"))
    store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 23 * _GIB, 1 * _GIB))

    store.record_service_baseline(GpuMemoryMeasurement(False, error="driver unavailable"))
    store.record_model_loaded("acme/tiny-model", GpuMemoryMeasurement(False, error="driver unavailable"))

    facts = store.read()
    assert facts["service_baseline_free_vram_bytes"] == 23 * _GIB
    assert "model_name" not in facts


def test_a_baseline_taken_while_a_model_was_served_yields_no_static_vram(tmp_path: Path) -> None:
    """A reading taken with weights already resident is not used as a baseline."""
    store = VramFactsStore(path=str(tmp_path / "vram-facts.json"))
    store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 8 * _GIB, 16 * _GIB), model_served=True)

    facts = store.record_model_loaded("acme/tiny-model", GpuMemoryMeasurement(True, 24 * _GIB, 8 * _GIB, 16 * _GIB))

    assert "measured_model_static_vram_bytes" not in facts
    assert "already served" in facts["static_vram_unavailable_reason"]


def test_a_model_free_baseline_is_never_replaced_by_a_model_served_one(tmp_path: Path) -> None:
    """A restart while vLLM holds weights must not overwrite a real baseline."""
    store = VramFactsStore(path=str(tmp_path / "vram-facts.json"))
    store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 23 * _GIB, 1 * _GIB), model_served=False)

    store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 8 * _GIB, 16 * _GIB), model_served=True)

    facts = store.read()
    assert facts["service_baseline_free_vram_bytes"] == 23 * _GIB
    assert facts["baseline_model_served"] is False


def test_an_unwritable_location_is_reported_in_the_facts(tmp_path: Path) -> None:
    """A store that cannot persist says so instead of failing the caller."""
    blocker = tmp_path / "blocked"
    blocker.write_text("not a directory", encoding="utf-8")
    store = VramFactsStore(path=str(blocker / "vram-facts.json"))

    facts = store.record_service_baseline(GpuMemoryMeasurement(True, 24 * _GIB, 23 * _GIB, 1 * _GIB))

    assert facts["write_error"]
    assert facts["service_baseline_free_vram_bytes"] == 23 * _GIB
