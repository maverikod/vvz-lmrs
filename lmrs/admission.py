"""Admission control and verdict contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class CapacitySnapshot:
    """Current runtime capacity input for admission decisions.

    Attributes:
        usable_dynamic_vram_bytes: Currently usable dynamic VRAM in bytes.
        max_dynamic_pool_bytes: Maximum dynamic VRAM pool in bytes.
        model_loaded: Whether the target model is currently loaded.
        runtime_ready: Whether the runtime is ready to accept requests.
        metadata: Arbitrary metadata for this snapshot.
    """

    usable_dynamic_vram_bytes: int
    max_dynamic_pool_bytes: int
    model_loaded: bool
    runtime_ready: bool
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class AdmissionVerdict:
    """Admission decision for a gateway request.

    Attributes:
        decision: Admission decision: would_execute, would_queue, or would_reject.
        reason_code: Stable machine-readable reason code for this decision.
        request_id: Unique identifier for the request.
        model_name: Name of the model for this request.
        required_tokens: Total required tokens for this request.
        required_dynamic_vram_bytes: Dynamic VRAM required for this request.
        queueable: Whether this request is eligible for queueing.
        metadata: Arbitrary metadata for this verdict.
    """

    decision: str
    reason_code: str
    request_id: str
    model_name: str
    required_tokens: int
    required_dynamic_vram_bytes: int
    queueable: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


def evaluate_lmcache_admission(
    cache_hit_confirmed: bool,
    full_required_dynamic_vram_bytes: int,
    cache_adjusted_dynamic_vram_bytes: int | None,
) -> int:
    """Return adjusted VRAM requirement using conservative LMCache admission.

    Args:
        cache_hit_confirmed: Whether an LMCache hit has been confirmed.
        full_required_dynamic_vram_bytes: Full cache-miss VRAM requirement in bytes.
        cache_adjusted_dynamic_vram_bytes: Adjusted VRAM when cache hit is confirmed.

    Returns:
        Adjusted dynamic VRAM bytes if cache hit confirmed; full estimate otherwise.
    """
    if cache_hit_confirmed and cache_adjusted_dynamic_vram_bytes is not None:
        return cache_adjusted_dynamic_vram_bytes
    return full_required_dynamic_vram_bytes


def decide_admission(
    estimate: Any,
    capacity: CapacitySnapshot,
    cache_hit_confirmed: bool = False,
    cache_adjusted_dynamic_vram_bytes: int | None = None,
) -> AdmissionVerdict:
    """Return an AdmissionVerdict for a gateway request.

    Args:
        estimate: Request capacity estimate with token and VRAM requirements.
        capacity: Current runtime capacity snapshot.
        cache_hit_confirmed: Whether an LMCache cache hit has been confirmed.
        cache_adjusted_dynamic_vram_bytes: VRAM adjustment from confirmed cache hit.

    Returns:
        An AdmissionVerdict with decision, reason code, and queueability.
    """
    required_tokens = estimate.required_tokens
    required_vram = evaluate_lmcache_admission(
        cache_hit_confirmed=cache_hit_confirmed,
        full_required_dynamic_vram_bytes=estimate.required_dynamic_vram_bytes,
        cache_adjusted_dynamic_vram_bytes=cache_adjusted_dynamic_vram_bytes,
    )
    if required_tokens > estimate.declared_context_window:
        return AdmissionVerdict(
            decision="would_reject",
            reason_code="CONTEXT_OVERFLOW",
            request_id=estimate.request_id,
            model_name=estimate.model_name,
            required_tokens=required_tokens,
            required_dynamic_vram_bytes=required_vram,
            queueable=False,
        )
    if required_vram > capacity.max_dynamic_pool_bytes:
        return AdmissionVerdict(
            decision="would_reject",
            reason_code="REQUEST_TOO_LARGE",
            request_id=estimate.request_id,
            model_name=estimate.model_name,
            required_tokens=required_tokens,
            required_dynamic_vram_bytes=required_vram,
            queueable=False,
        )
    if required_vram > capacity.usable_dynamic_vram_bytes:
        return AdmissionVerdict(
            decision="would_queue",
            reason_code="CAPACITY_CONSTRAINED",
            request_id=estimate.request_id,
            model_name=estimate.model_name,
            required_tokens=required_tokens,
            required_dynamic_vram_bytes=required_vram,
            queueable=True,
        )
    return AdmissionVerdict(
        decision="would_execute",
        reason_code="CAPACITY_AVAILABLE",
        request_id=estimate.request_id,
        model_name=estimate.model_name,
        required_tokens=required_tokens,
        required_dynamic_vram_bytes=required_vram,
        queueable=False,
    )


_REQUIRED_REQUEST_METADATA: tuple[str, ...] = ("session_id",)
_REQUIRED_QUEUE_METADATA: tuple[str, ...] = ("admitted_at", "expires_at")


def build_queue_entry_request(
    verdict: AdmissionVerdict,
    estimate: Any,
    request_metadata: Mapping[str, object],
    queue_metadata: Mapping[str, object],
) -> dict[str, object]:
    """Convert a queueable verdict into a complete structured queue input.

    The admission and expiration timestamps are supplied by the caller through
    queue_metadata rather than generated or placeheld here, so the recorded
    admission time is the actual one.

    Args:
        verdict: Admission verdict; must have queueable set to True.
        estimate: Request capacity estimate carrying the request identity and
            requirement facts.
        request_metadata: Request-side facts. Requires session_id.
        queue_metadata: Queue-side facts. Requires admitted_at and expires_at;
            priority defaults to 0 and status defaults to queued.

    Returns:
        A structured dictionary carrying request and session identity, the
        capacity values, the actual admission and expiration timestamps,
        priority, status, and the stable reason code, suitable for queue entry
        creation.

    Raises:
        ValueError: If the verdict is not queueable, if the verdict and
            estimate describe different requests, or if a required metadata
            field is missing.
    """
    if not verdict.queueable:
        raise ValueError("build_queue_entry_request requires a queueable verdict")
    if estimate.request_id != verdict.request_id:
        raise ValueError(
            "build_queue_entry_request requires the verdict and estimate to "
            "describe the same request"
        )
    missing = [
        name
        for name in _REQUIRED_REQUEST_METADATA
        if request_metadata.get(name) is None
    ]
    missing += [
        name
        for name in _REQUIRED_QUEUE_METADATA
        if queue_metadata.get(name) is None
    ]
    if missing:
        raise ValueError(
            "build_queue_entry_request is missing required metadata: "
            + ", ".join(missing)
        )
    return {
        "request_id": verdict.request_id,
        "session_id": request_metadata["session_id"],
        "model_name": verdict.model_name,
        "required_tokens": verdict.required_tokens,
        "required_dynamic_vram_bytes": verdict.required_dynamic_vram_bytes,
        "admitted_at": queue_metadata["admitted_at"],
        "expires_at": queue_metadata["expires_at"],
        "priority": queue_metadata.get("priority", 0),
        "status": queue_metadata.get("status", "queued"),
        "reason_code": verdict.reason_code,
    }
