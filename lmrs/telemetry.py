"""Telemetry feedback contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class TelemetryRecord:
    """Structured telemetry for one request: predicted versus actual facts.

    Attributes:
        request_id: Identifier of the request this record describes.
        predicted_tokens: Predicted total token count.
        predicted_vram: Predicted VRAM requirement in bytes.
        actual_prompt_tokens: Measured prompt token count.
        actual_output_tokens: Measured output token count.
        latency: Measured end-to-end latency.
        queue_wait: Measured time spent waiting in the queue.
        vram_snapshot: Measured VRAM snapshot at execution time.
        admission_verdict: Admission verdict recorded for the request.
        reason_code: Stable reason code associated with the outcome.
        lmcache_hit_tokens: Tokens served from LMCache hits.
        lmcache_miss_tokens: Tokens that missed LMCache.
        lmcache_lookup_latency: Measured LMCache lookup latency.
        lmcache_write_latency: Measured LMCache write latency.
        eviction_events: Number of cache eviction events observed.
        metadata: Arbitrary metadata about this record.
    """

    request_id: str
    predicted_tokens: int | None = None
    predicted_vram: int | None = None
    actual_prompt_tokens: int | None = None
    actual_output_tokens: int | None = None
    latency: float | None = None
    queue_wait: float | None = None
    vram_snapshot: Mapping[str, Any] = field(default_factory=dict)
    admission_verdict: str | None = None
    reason_code: str | None = None
    lmcache_hit_tokens: int = 0
    lmcache_miss_tokens: int = 0
    lmcache_lookup_latency: float | None = None
    lmcache_write_latency: float | None = None
    eviction_events: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


def build_telemetry_record(
    observations: Mapping[str, Any],
    prediction_context: Mapping[str, Any],
) -> TelemetryRecord:
    """Assemble one TelemetryRecord from observations and a prediction snapshot.

    Records observed facts only and must not alter admission, scheduling, or
    VRAM accounting.

    Args:
        observations: Execution observations for the request.
        prediction_context: Prediction snapshot taken at estimate time.

    Returns:
        A TelemetryRecord comparing predicted versus actual facts.
    """
    return TelemetryRecord(
        request_id=str(observations.get("request_id", "")),
        predicted_tokens=prediction_context.get("predicted_tokens"),
        predicted_vram=prediction_context.get("predicted_vram"),
        actual_prompt_tokens=observations.get("actual_prompt_tokens"),
        actual_output_tokens=observations.get("actual_output_tokens"),
        latency=observations.get("latency"),
        queue_wait=observations.get("queue_wait"),
        vram_snapshot=observations.get("vram_snapshot", {}),
        admission_verdict=observations.get("admission_verdict"),
        reason_code=observations.get("reason_code"),
        lmcache_hit_tokens=int(observations.get("lmcache_hit_tokens", 0)),
        lmcache_miss_tokens=int(observations.get("lmcache_miss_tokens", 0)),
        lmcache_lookup_latency=observations.get("lmcache_lookup_latency"),
        lmcache_write_latency=observations.get("lmcache_write_latency"),
        eviction_events=int(observations.get("eviction_events", 0)),
        metadata=observations.get("metadata", {}),
    )


@dataclass(frozen=True)
class TelemetryCalibrationObservation:
    """Aggregated observation input offered to Calibration Profile.

    Keyed by model, runtime, quantization, and hardware. It does not own or
    compute the calibration profile and is distinct from the calibration-owned
    CalibrationObservation defined in lmrs.calibration.

    Attributes:
        model_name: Model name key.
        runtime_backend: Runtime backend key.
        quantization_profile: Quantization profile key.
        hardware_profile: Hardware profile key.
        prompt_length: Representative prompt length for the aggregate.
        measured_vram_growth: Measured VRAM growth sample.
        kv_cache_cost_sample: Sampled KV-cache cost.
        per_request_overhead_sample: Sampled per-request overhead.
        sample_count: Number of telemetry records aggregated.
        metadata: Arbitrary metadata about this observation.
    """

    model_name: str
    runtime_backend: str
    quantization_profile: str
    hardware_profile: str
    prompt_length: int | None = None
    measured_vram_growth: int | None = None
    kv_cache_cost_sample: int | None = None
    per_request_overhead_sample: int | None = None
    sample_count: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)


def collect_calibration_observations(
    records: list[TelemetryRecord],
) -> list[TelemetryCalibrationObservation]:
    """Aggregate telemetry records into calibration observations.

    Groups by model, runtime backend, quantization profile, and hardware
    profile. It only makes observations available to Calibration Profile and
    must not change calibration ownership, recompute the profile, or feed back
    into admission.

    Args:
        records: Telemetry records to aggregate.

    Returns:
        A list of aggregated TelemetryCalibrationObservation items.
    """
    groups: dict[tuple[str, str, str, str], list[TelemetryRecord]] = {}
    for record in records:
        metadata = record.metadata
        key = (
            str(metadata.get("model_name", "")),
            str(metadata.get("runtime_backend", "")),
            str(metadata.get("quantization_profile", "")),
            str(metadata.get("hardware_profile", "")),
        )
        groups.setdefault(key, []).append(record)

    def average_int(values: list[int]) -> int | None:
        if not values:
            return None
        return int(sum(values) / len(values))

    observations: list[TelemetryCalibrationObservation] = []
    for key, group_records in groups.items():
        sample_count = len(group_records)
        prompt_lengths = [
            record.actual_prompt_tokens
            for record in group_records
            if record.actual_prompt_tokens is not None
        ]
        vram_growth = [
            int(record.metadata["measured_vram_growth"])
            for record in group_records
            if "measured_vram_growth" in record.metadata
        ]
        kv_cache_costs = [
            int(record.metadata["kv_cache_cost_sample"])
            for record in group_records
            if "kv_cache_cost_sample" in record.metadata
        ]
        overhead_samples = [
            int(record.metadata["per_request_overhead_sample"])
            for record in group_records
            if "per_request_overhead_sample" in record.metadata
        ]
        metadata = {
            "record_count": sample_count,
            "request_ids": [record.request_id for record in group_records],
        }
        observations.append(
            TelemetryCalibrationObservation(
                model_name=key[0],
                runtime_backend=key[1],
                quantization_profile=key[2],
                hardware_profile=key[3],
                prompt_length=average_int(prompt_lengths),
                measured_vram_growth=average_int(vram_growth),
                kv_cache_cost_sample=average_int(kv_cache_costs),
                per_request_overhead_sample=average_int(overhead_samples),
                sample_count=sample_count,
                metadata=metadata,
            )
        )

    return observations
