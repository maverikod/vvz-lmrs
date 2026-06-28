"""VRAM runtime facts and capacity calculation functions for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


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
    if facts.service_baseline_free_vram_bytes < 0 or facts.model_loaded_free_vram_bytes < 0:
        raise ValueError("VRAM measurements must be non-negative")
    if facts.model_loaded_free_vram_bytes > facts.service_baseline_free_vram_bytes:
        raise ValueError("model-loaded free VRAM cannot exceed service baseline free VRAM")
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


def runtime_fact_snapshot(facts: VramRuntimeFacts, state: DynamicVramState) -> dict[str, object]:
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
