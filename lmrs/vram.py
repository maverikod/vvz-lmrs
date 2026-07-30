"""VRAM runtime facts and capacity calculation functions for the LMRS package.

The measurement side of this module reads the GPU through ``nvidia-smi`` and
persists what it read, because the service baseline is only observable before a
model is loaded: once vLLM holds its weights, no later reading can reconstruct
it. Every derived figure here comes from a stored measurement, never from a
theoretical model size.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class VramRuntimeFacts:
    """Measured VRAM facts for one model/runtime/hardware profile.

    Attributes:
        resident_services: Names of always-running GPU services.
        service_baseline_free_vram_bytes: Free VRAM after baseline services start.
        model_loaded_free_vram_bytes: Free VRAM after the model is loaded.
        model_name: Optional name of the loaded model.
        runtime_backend: Optional runtime backend identifier.
        quantization_profile: Optional quantization profile identifier.
        hardware_profile_id: Optional hardware profile identifier.
        measurement_metadata: Measurement provenance metadata.
    """

    resident_services: tuple[str, ...]
    service_baseline_free_vram_bytes: int
    model_loaded_free_vram_bytes: int | None = None
    model_name: str | None = None
    runtime_backend: str | None = None
    quantization_profile: str | None = None
    hardware_profile_id: str | None = None
    measurement_metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DynamicVramState:
    """Dynamic VRAM capacity state used by admission and scheduling.

    Attributes:
        model_loaded_free_vram_bytes: Free VRAM after model load.
        safety_margin_bytes: VRAM reserved as safety margin.
        runtime_reserve_bytes: VRAM reserved for runtime overhead.
        active_reservation_bytes: VRAM reserved for active requests.
    """

    model_loaded_free_vram_bytes: int
    safety_margin_bytes: int
    runtime_reserve_bytes: int
    active_reservation_bytes: int = 0


def calculate_static_vram(facts: VramRuntimeFacts) -> int:
    """Return measured model static VRAM bytes from baseline and loaded facts.

    Args:
        facts: Measured VRAM runtime facts containing baseline and loaded values.

    Returns:
        Measured static VRAM bytes consumed by the model.
    """
    if facts.model_loaded_free_vram_bytes is None:
        raise ValueError("model_loaded_free_vram_bytes is required")
    if (
        facts.service_baseline_free_vram_bytes < 0
        or facts.model_loaded_free_vram_bytes < 0
    ):
        raise ValueError("VRAM measurements must be non-negative")
    if facts.model_loaded_free_vram_bytes > facts.service_baseline_free_vram_bytes:
        raise ValueError(
            "model-loaded free VRAM cannot exceed service baseline free VRAM"
        )
    return facts.service_baseline_free_vram_bytes - facts.model_loaded_free_vram_bytes


def calculate_max_dynamic_pool(state: DynamicVramState) -> int:
    """Return dynamic VRAM before active request reservations are subtracted.

    Args:
        state: Current dynamic VRAM state with margins and reserves.

    Returns:
        Maximum dynamic VRAM pool in bytes, clamped to zero.
    """
    if state.model_loaded_free_vram_bytes < 0:
        raise ValueError("model_loaded_free_vram_bytes must be non-negative")
    if state.safety_margin_bytes < 0 or state.runtime_reserve_bytes < 0:
        raise ValueError("VRAM reserves must be non-negative")
    return max(
        0,
        state.model_loaded_free_vram_bytes
        - state.safety_margin_bytes
        - state.runtime_reserve_bytes,
    )


def calculate_usable_dynamic_vram(state: DynamicVramState) -> int:
    """Return currently usable dynamic VRAM after active reservations.

    Args:
        state: Current dynamic VRAM state including active reservations.

    Returns:
        Usable dynamic VRAM in bytes after all reservations, clamped to zero.
    """
    if state.active_reservation_bytes < 0:
        raise ValueError("active_reservation_bytes must be non-negative")
    return max(0, calculate_max_dynamic_pool(state) - state.active_reservation_bytes)


_MIB_BYTES = 1024 * 1024
_NVIDIA_SMI_QUERY = (
    "nvidia-smi",
    "--query-gpu=index,memory.total,memory.free,memory.used",
    "--format=csv,noheader,nounits",
)


@dataclass(frozen=True)
class GpuMemoryMeasurement:
    """One reading of GPU memory taken from the driver.

    Attributes:
        ok: Whether the reading succeeded.
        total_bytes: Total VRAM across the visible devices.
        free_bytes: Free VRAM across the visible devices.
        used_bytes: Used VRAM across the visible devices.
        devices: Per-device totals, in device index order.
        measured_at: ISO timestamp of the reading.
        source: Tool that produced the reading.
        error: Failure description when the reading did not succeed.
    """

    ok: bool
    total_bytes: int = 0
    free_bytes: int = 0
    used_bytes: int = 0
    devices: tuple[Mapping[str, int], ...] = ()
    measured_at: str = ""
    source: str = "nvidia-smi"
    error: str | None = None


def _run_nvidia_smi(command: Sequence[str]) -> tuple[int, str, str]:
    """Run one nvidia-smi query.

    Args:
        command: The command line to execute.

    Returns:
        The exit status, standard output and standard error.

    Raises:
        FileNotFoundError: If nvidia-smi is not installed.
    """
    if shutil.which(command[0]) is None:
        raise FileNotFoundError(f"{command[0]} is not installed")
    completed = subprocess.run(list(command), capture_output=True, text=True, timeout=15, check=False)
    return completed.returncode, completed.stdout, completed.stderr


def measure_gpu_memory(
    runner: Callable[[Sequence[str]], tuple[int, str, str]] | None = None,
) -> GpuMemoryMeasurement:
    """Read current GPU memory from the driver.

    Args:
        runner: Executes the query and returns status, stdout and stderr; the
            nvidia-smi runner is used when omitted.

    Returns:
        A GpuMemoryMeasurement; ``ok`` is False when the driver could not be
        read, and the failure is carried in ``error`` rather than raised, so a
        capacity report can state that it has no measurement instead of
        inventing one.
    """
    execute = runner if runner is not None else _run_nvidia_smi
    measured_at = datetime.now(UTC).isoformat()
    try:
        status, stdout, stderr = execute(_NVIDIA_SMI_QUERY)
    except Exception as error:  # noqa: BLE001 - an unreadable GPU is reported, not raised
        return GpuMemoryMeasurement(False, measured_at=measured_at, error=f"{type(error).__name__}: {error}")
    if status != 0:
        return GpuMemoryMeasurement(False, measured_at=measured_at, error=(stderr or stdout).strip() or f"nvidia-smi exited with {status}")
    devices: list[Mapping[str, int]] = []
    for line in stdout.splitlines():
        fields = [field_value.strip() for field_value in line.split(",")]
        if len(fields) != 4:
            continue
        try:
            index, total, free, used = (int(value) for value in fields)
        except ValueError:
            continue
        devices.append({
            "index": index,
            "total_bytes": total * _MIB_BYTES,
            "free_bytes": free * _MIB_BYTES,
            "used_bytes": used * _MIB_BYTES,
        })
    if not devices:
        return GpuMemoryMeasurement(False, measured_at=measured_at, error="nvidia-smi reported no devices")
    return GpuMemoryMeasurement(
        ok=True,
        total_bytes=sum(int(device["total_bytes"]) for device in devices),
        free_bytes=sum(int(device["free_bytes"]) for device in devices),
        used_bytes=sum(int(device["used_bytes"]) for device in devices),
        devices=tuple(devices),
        measured_at=measured_at,
    )


@dataclass
class VramFactsStore:
    """Persisted VRAM measurements for one host.

    The service baseline can only be observed while no model is loaded, so it is
    written once and read back afterwards; without it a static-VRAM figure would
    be a guess, and a guess is exactly what the invariant forbids.

    Attributes:
        path: File holding the recorded measurements.
    """

    path: str

    def read(self) -> dict[str, Any]:
        """Return the stored measurements.

        Returns:
            The stored mapping, empty when nothing has been recorded yet.
        """
        try:
            loaded = json.loads(Path(self.path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    def _write(self, facts: Mapping[str, Any]) -> dict[str, Any]:
        """Persist the measurements, ignoring an unwritable location.

        Args:
            facts: The mapping to store.

        Returns:
            The same mapping, so callers can use it whether or not the write
            reached disk.
        """
        stored = dict(facts)
        try:
            target = Path(self.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(stored, indent=2, sort_keys=True), encoding="utf-8")
        except OSError as error:
            stored["write_error"] = str(error)
        return stored

    def record_service_baseline(
        self,
        measurement: GpuMemoryMeasurement,
        resident_services: Sequence[str] = (),
        model_served: bool = False,
    ) -> dict[str, Any]:
        """Record free VRAM measured before a model is loaded.

        A reading taken while the runtime already serves a model is not a
        baseline, and it is the reading a restart would otherwise produce: LMRS
        can restart at any time while vLLM keeps its weights. Such a reading is
        stored only when nothing better exists, is flagged as model-served, and
        never replaces a model-free baseline - otherwise the next static-VRAM
        figure would come out near zero and admission would believe the model
        costs nothing.

        Args:
            measurement: The reading to record.
            resident_services: Always-on GPU services running at that moment.
            model_served: Whether the runtime already served a model when the
                reading was taken.

        Returns:
            The stored facts.
        """
        if not measurement.ok:
            return self.read()
        facts = self.read()
        if model_served and facts.get("baseline_model_served") is False:
            return facts
        facts.update({
            "service_baseline_free_vram_bytes": measurement.free_bytes,
            "total_vram_bytes": measurement.total_bytes,
            "resident_services": list(resident_services),
            "baseline_measured_at": measurement.measured_at,
            "baseline_model_served": model_served,
        })
        return self._write(facts)

    def record_model_loaded(
        self,
        model_name: str,
        measurement: GpuMemoryMeasurement,
        runtime_backend: str = "",
        quantization_profile: str = "",
    ) -> dict[str, Any]:
        """Record free VRAM measured after a model became resident.

        Args:
            model_name: The model that is now resident.
            measurement: The reading to record.
            runtime_backend: Backend hosting the model.
            quantization_profile: Quantization profile of the loaded model.

        Returns:
            The stored facts, including the derived static VRAM when both
            measurements are present and consistent.
        """
        if not measurement.ok:
            return self.read()
        facts = self.read()
        facts.update({
            "model_name": model_name,
            "model_loaded_free_vram_bytes": measurement.free_bytes,
            "model_loaded_measured_at": measurement.measured_at,
            "runtime_backend": runtime_backend,
            "quantization_profile": quantization_profile,
        })
        baseline = facts.get("service_baseline_free_vram_bytes")
        if facts.get("baseline_model_served"):
            facts.pop("measured_model_static_vram_bytes", None)
            facts["static_vram_unavailable_reason"] = "the stored baseline was measured while a model was already served"
        elif isinstance(baseline, int) and baseline >= measurement.free_bytes:
            facts["measured_model_static_vram_bytes"] = baseline - measurement.free_bytes
            facts.pop("static_vram_unavailable_reason", None)
        else:
            facts.pop("measured_model_static_vram_bytes", None)
            facts["static_vram_unavailable_reason"] = "no service baseline below the model-loaded reading"
        return self._write(facts)


def runtime_fact_snapshot(
    facts: VramRuntimeFacts, state: DynamicVramState
) -> dict[str, object]:
    """Return a structured snapshot of measured and derived VRAM facts.

    Args:
        facts: Measured VRAM runtime facts for the model/runtime/hardware profile.
        state: Current dynamic VRAM state for derived capacity calculations.

    Returns:
        A structured dictionary of measured and derived VRAM facts.
    """
    return {
        "resident_services": list(facts.resident_services),
        "service_baseline_free_vram_bytes": facts.service_baseline_free_vram_bytes,
        "model_loaded_free_vram_bytes": facts.model_loaded_free_vram_bytes,
        "measured_model_static_vram_bytes": calculate_static_vram(facts),
        "max_dynamic_pool_bytes": calculate_max_dynamic_pool(state),
        "usable_dynamic_vram_bytes": calculate_usable_dynamic_vram(state),
        "active_reservation_bytes": state.active_reservation_bytes,
        "model_name": facts.model_name,
        "runtime_backend": facts.runtime_backend,
        "quantization_profile": facts.quantization_profile,
        "hardware_profile_id": facts.hardware_profile_id,
        "measurement_metadata": dict(facts.measurement_metadata),
    }
