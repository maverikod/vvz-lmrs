"""LMCache storage and telemetry contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class LMCacheStoragePolicy:
    """Storage policy for the LMCache backend.

    LMCache is a separate caching backend distinct from the disk model cache
    and does not replace admission control.

    Attributes:
        enabled: Whether LMCache is enabled.
        storage_tiers: Active storage tiers (for example, "ram" and "disk").
        cache_storage_path: Filesystem path for the disk cache tier.
        namespace_binding: Namespace key that scopes cache entries.
        session_binding: Session key that scopes cache entries.
        cpu_cache_limit_bytes: Maximum CPU/RAM cache size in bytes.
        disk_cache_limit_bytes: Maximum disk cache size in bytes.
        eviction_policy: Eviction policy identifier.
        compatibility_mode: Runtime and model compatibility mode identifier.
        metadata: Arbitrary metadata about this policy.
    """

    enabled: bool = False
    storage_tiers: tuple[str, ...] = ("ram", "disk")
    cache_storage_path: str | None = None
    namespace_binding: str | None = None
    session_binding: str | None = None
    cpu_cache_limit_bytes: int | None = None
    disk_cache_limit_bytes: int | None = None
    eviction_policy: str | None = None
    compatibility_mode: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LMCacheEstimate:
    """Optional pre-execution cache reuse information for the estimate response.

    If a cache hit is not guaranteed before launch, the values represent a full
    cache miss and must not weaken admission or extend context or VRAM capacity.

    Attributes:
        cache_reuse_possible: Whether cache reuse is possible before execution.
        estimated_cache_hit_tokens: Estimated tokens served from cache.
        effective_new_tokens: Tokens that must still be computed fresh.
        cache_estimate_quality: Qualitative confidence of the estimate.
        metadata: Arbitrary metadata about this estimate.
    """

    cache_reuse_possible: bool = False
    estimated_cache_hit_tokens: int = 0
    effective_new_tokens: int = 0
    cache_estimate_quality: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LMCacheTelemetry:
    """Telemetry accounting for the LMCache CPU and disk tiers.

    Attributes:
        hit_tokens: Tokens served from cache hits.
        miss_tokens: Tokens that missed the cache.
        hit_quality: Qualitative quality of cache hits.
        restore_latency: Latency restoring cached entries.
        write_latency: Latency writing cache entries.
        cpu_cache_usage: Current CPU/RAM cache usage in bytes.
        cpu_cache_limit: CPU/RAM cache limit in bytes.
        disk_cache_usage: Current disk cache usage in bytes.
        disk_cache_limit: Disk cache limit in bytes.
        evictions: Number of cache evictions observed.
        memory_pressure: Observed memory pressure indicator.
        swap_indicators: Observed swap activity indicators.
        metadata: Arbitrary metadata about this telemetry record.
    """

    hit_tokens: int = 0
    miss_tokens: int = 0
    hit_quality: str | None = None
    restore_latency: float | None = None
    write_latency: float | None = None
    cpu_cache_usage: int = 0
    cpu_cache_limit: int | None = None
    disk_cache_usage: int = 0
    disk_cache_limit: int | None = None
    evictions: int = 0
    memory_pressure: str | None = None
    swap_indicators: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


def lmcache_storage_separation(
    usable_dynamic_vram_bytes: int,
    telemetry: LMCacheTelemetry,
) -> int:
    """Prove CPU and disk LMCache capacity never count as free GPU VRAM.

    Args:
        usable_dynamic_vram_bytes: Usable dynamic GPU VRAM pool in bytes.
        telemetry: Observed LMCache telemetry for the CPU and disk tiers.

    Returns:
        The usable dynamic VRAM in bytes, unchanged by LMCache storage.
    """
    if usable_dynamic_vram_bytes < 0:
        raise ValueError("usable_dynamic_vram_bytes must be non-negative")
    return usable_dynamic_vram_bytes


def build_lmcache_telemetry(
    policy: LMCacheStoragePolicy,
    observations: Mapping[str, Any],
) -> LMCacheTelemetry:
    """Assemble an LMCacheTelemetry record from policy and runtime observations.

    Reports observed facts only and must not weaken admission or count cache
    storage as free VRAM.

    Args:
        policy: The active LMCache storage policy.
        observations: Mapping of runtime backend observations.

    Returns:
        An LMCacheTelemetry record populated from the observations.
    """
    return LMCacheTelemetry(
        hit_tokens=int(observations.get("hit_tokens", 0)),
        miss_tokens=int(observations.get("miss_tokens", 0)),
        hit_quality=observations.get("hit_quality"),
        restore_latency=observations.get("restore_latency"),
        write_latency=observations.get("write_latency"),
        cpu_cache_usage=int(observations.get("cpu_cache_usage", 0)),
        cpu_cache_limit=policy.cpu_cache_limit_bytes,
        disk_cache_usage=int(observations.get("disk_cache_usage", 0)),
        disk_cache_limit=policy.disk_cache_limit_bytes,
        evictions=int(observations.get("evictions", 0)),
        memory_pressure=observations.get("memory_pressure"),
        swap_indicators=observations.get("swap_indicators", {}),
        metadata=observations.get("metadata", {}),
    )
