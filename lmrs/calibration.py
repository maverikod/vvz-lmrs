"""Calibration contract objects for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class CalibrationKey:
    """Identifies the model/runtime/quantization/hardware tuple
    owning calibration facts.

    Attributes:
        model_name: Name of the model being calibrated.
        runtime_backend: Backend runtime used during calibration.
        quantization_profile: Quantization profile identifier.
        hardware_profile_id: Hardware profile identifier.
        tokenizer_profile: Optional tokenizer profile identifier.
        metadata: Arbitrary metadata associated with this key.
    """

    model_name: str
    runtime_backend: str
    quantization_profile: str
    hardware_profile_id: str
    tokenizer_profile: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationObservation:
    """Empirical observation consumed by capacity estimation calibration.

    Attributes:
        prompt_tokens: Number of prompt tokens in this observation.
        output_tokens: Number of output tokens generated.
        predicted_dynamic_vram_bytes: Predicted dynamic VRAM usage in bytes.
        actual_dynamic_vram_bytes: Measured actual dynamic VRAM usage in bytes.
        per_request_overhead_bytes: Per-request memory overhead in bytes.
        runtime_batch_overhead_bytes: Batch-level runtime memory overhead in bytes.
        metadata: Arbitrary metadata for this observation.
    """

    prompt_tokens: int
    output_tokens: int
    predicted_dynamic_vram_bytes: int
    actual_dynamic_vram_bytes: int
    per_request_overhead_bytes: int = 0
    runtime_batch_overhead_bytes: int = 0
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CalibrationProfile:
    """Calibrated KV and overhead costs for a specific model/runtime combination.

    Attributes:
        key: Calibration key identifying the model/runtime/hardware combination.
        kv_bytes_per_token: Calibrated KV-cache bytes per token.
        per_request_overhead_bytes: Per-request memory overhead in bytes.
        runtime_batch_overhead_bytes: Batch-level runtime memory overhead in bytes.
        observation_count: Number of empirical observations used in calibration.
        source: Source of the calibration data.
        valid: Whether this profile is currently valid.
        invalidation_reason: Reason for invalidation when not valid.
        metadata: Arbitrary metadata associated with this profile.
    """

    key: CalibrationKey
    kv_bytes_per_token: int
    per_request_overhead_bytes: int
    runtime_batch_overhead_bytes: int
    observation_count: int
    source: str = "empirical"
    valid: bool = True
    invalidation_reason: str | None = None
    metadata: Mapping[str, str] = field(default_factory=dict)


def build_calibration_profile(
    key: CalibrationKey,
    observations: tuple[CalibrationObservation, ...],
) -> CalibrationProfile:
    """Build a CalibrationProfile from empirical observations.

    Args:
        key: Calibration key identifying the model/runtime/hardware combination.
        observations: Tuple of empirical calibration observations.

    Returns:
        A CalibrationProfile with calibrated KV bytes per token and overheads.
    """
    if not observations:
        raise ValueError("observations must not be empty")
    total_tokens = 0
    total_actual_vram = 0
    total_overhead = 0
    max_per_request_overhead = 0
    max_batch_overhead = 0
    for obs in observations:
        if obs.prompt_tokens < 0 or obs.output_tokens < 0:
            raise ValueError("token counts must be non-negative")
        if obs.actual_dynamic_vram_bytes < 0 or obs.predicted_dynamic_vram_bytes < 0:
            raise ValueError("VRAM byte values must be non-negative")
        tokens = obs.prompt_tokens + obs.output_tokens
        total_tokens += tokens
        total_actual_vram += obs.actual_dynamic_vram_bytes
        total_overhead += (
            obs.per_request_overhead_bytes
            + obs.runtime_batch_overhead_bytes
        )
        if obs.per_request_overhead_bytes > max_per_request_overhead:
            max_per_request_overhead = obs.per_request_overhead_bytes
        if obs.runtime_batch_overhead_bytes > max_batch_overhead:
            max_batch_overhead = obs.runtime_batch_overhead_bytes
    if total_tokens > 0:
        kv_bytes_per_token = max(
            0,
            (total_actual_vram - total_overhead) // total_tokens,
        )
    else:
        kv_bytes_per_token = 0
    return CalibrationProfile(
        key=key,
        kv_bytes_per_token=kv_bytes_per_token,
        per_request_overhead_bytes=max_per_request_overhead,
        runtime_batch_overhead_bytes=max_batch_overhead,
        observation_count=len(observations),
    )


def calibration_snapshot(profile: CalibrationProfile) -> dict[str, object]:
    """Return a structured snapshot of calibration identity, costs, and validity.

    Args:
        profile: The CalibrationProfile to snapshot.

    Returns:
        A structured dictionary with calibration facts and validity state.
    """
    return {
        "model_name": profile.key.model_name,
        "runtime_backend": profile.key.runtime_backend,
        "quantization_profile": profile.key.quantization_profile,
        "hardware_profile_id": profile.key.hardware_profile_id,
        "tokenizer_profile": profile.key.tokenizer_profile,
        "kv_bytes_per_token": profile.kv_bytes_per_token,
        "per_request_overhead_bytes": profile.per_request_overhead_bytes,
        "runtime_batch_overhead_bytes": profile.runtime_batch_overhead_bytes,
        "observation_count": profile.observation_count,
        "source": profile.source,
        "valid": profile.valid,
        "invalidation_reason": profile.invalidation_reason,
        "metadata": dict(profile.metadata),
    }
