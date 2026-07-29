"""LMCache storage and telemetry contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping


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


TelemetrySource = (
    LMCacheTelemetry
    | Mapping[str, Any]
    | Callable[[], "LMCacheTelemetry | Mapping[str, Any]"]
)


def _resolve_telemetry(
    policy: LMCacheStoragePolicy,
    telemetry_source: TelemetrySource,
) -> LMCacheTelemetry:
    """Normalize a telemetry source into an LMCacheTelemetry record.

    Args:
        policy: The active LMCache storage policy, used for per-tier limits.
        telemetry_source: An LMCacheTelemetry record, a mapping of raw runtime
            observations, or a zero-argument callable returning either.

    Returns:
        An LMCacheTelemetry record describing the current cache tiers.

    Raises:
        TypeError: If the source is neither telemetry, a mapping, nor a callable
            returning one of those.
    """
    source = telemetry_source() if callable(telemetry_source) else telemetry_source
    if isinstance(source, LMCacheTelemetry):
        return source
    if isinstance(source, Mapping):
        return build_lmcache_telemetry(policy, source)
    raise TypeError("telemetry_source must be LMCacheTelemetry, a mapping of observations, or a callable returning one")


def get_lmcache_status(
    policy: LMCacheStoragePolicy,
    telemetry_source: TelemetrySource,
) -> dict[str, Any]:
    """Report LMCache enablement, per-tier usage and limits, and hit accounting.

    This is a read-only view assembled from the same telemetry facts as
    ``build_lmcache_telemetry``. It performs no admission-related computation
    and never consults the disk model cache; per-tier limits are policy facts,
    so a telemetry record that omits them falls back to the policy values.

    Args:
        policy: The active LMCache storage policy.
        telemetry_source: Current telemetry, raw observations, or a callable
            returning either.

    Returns:
        A dict with the keys ``enabled``, ``cpu_cache_usage_bytes``,
        ``cpu_cache_limit_bytes``, ``disk_cache_usage_bytes``,
        ``disk_cache_limit_bytes``, ``hit_tokens``, ``miss_tokens``,
        ``hit_quality`` and ``evictions``.
    """
    telemetry = _resolve_telemetry(policy, telemetry_source)
    cpu_limit = telemetry.cpu_cache_limit if telemetry.cpu_cache_limit is not None else policy.cpu_cache_limit_bytes
    disk_limit = telemetry.disk_cache_limit if telemetry.disk_cache_limit is not None else policy.disk_cache_limit_bytes
    return {
        "enabled": policy.enabled,
        "cpu_cache_usage_bytes": telemetry.cpu_cache_usage,
        "cpu_cache_limit_bytes": cpu_limit,
        "disk_cache_usage_bytes": telemetry.disk_cache_usage,
        "disk_cache_limit_bytes": disk_limit,
        "hit_tokens": telemetry.hit_tokens,
        "miss_tokens": telemetry.miss_tokens,
        "hit_quality": telemetry.hit_quality,
        "evictions": telemetry.evictions,
    }


def _purge_scope(namespace: str | None, session: str | None) -> str:
    """Describe the requested purge scope.

    Args:
        namespace: Namespace binding to scope the purge to, if any.
        session: Session binding to scope the purge to, if any.

    Returns:
        ``"global"`` when neither binding is given, otherwise a descriptor
        naming the bindings that scope the purge.
    """
    if namespace is None and session is None:
        return "global"
    parts = []
    if namespace is not None:
        parts.append(f"namespace:{namespace}")
    if session is not None:
        parts.append(f"session:{session}")
    return "/".join(parts)


def _purge_targets(root: Path, namespace: str | None, session: str | None) -> list[Path]:
    """Resolve the artifact paths a purge request covers.

    Cached artifacts live under ``<root>/<namespace>/<session>/``; artifacts
    written without a binding sit directly under the root.

    Args:
        root: The LMCache disk storage root.
        namespace: Namespace binding to scope the purge to, if any.
        session: Session binding to scope the purge to, if any.

    Returns:
        Existing paths to remove; an empty list when nothing matches.
    """
    if namespace is None and session is None:
        return sorted(root.iterdir())
    if namespace is not None and session is not None:
        target = root / namespace / session
        return [target] if target.exists() else []
    if namespace is not None:
        target = root / namespace
        return [target] if target.exists() else []
    return sorted(child / str(session) for child in root.iterdir() if child.is_dir() and (child / str(session)).exists())


def _remove_artifact(target: Path) -> int:
    """Remove one artifact path and report how many files it held.

    Args:
        target: File or directory to remove.

    Returns:
        The number of files removed.
    """
    if target.is_dir():
        removed = sum(1 for path in target.rglob("*") if path.is_file())
        shutil.rmtree(target)
        return removed
    target.unlink()
    return 1


def purge_lmcache(
    policy: LMCacheStoragePolicy,
    namespace: str | None = None,
    session: str | None = None,
) -> dict[str, Any]:
    """Remove cached LMCache artifacts globally or for one binding.

    With neither binding given every artifact under
    ``policy.cache_storage_path`` is removed; with a namespace and/or session
    only the artifacts bound to them are removed. This touches neither
    admission control nor the disk model cache, and enabling, disabling or
    resizing LMCache remains a configuration change.

    Args:
        policy: The active LMCache storage policy.
        namespace: Namespace binding to scope the purge to, if any.
        session: Session binding to scope the purge to, if any.

    Returns:
        A summary dict with the keys ``scope`` and ``removed_count``.
    """
    scope = _purge_scope(namespace, session)
    if not policy.cache_storage_path:
        return {"scope": scope, "removed_count": 0}
    root = Path(policy.cache_storage_path)
    if not root.is_dir():
        return {"scope": scope, "removed_count": 0}
    removed = sum(_remove_artifact(target) for target in _purge_targets(root, namespace, session))
    return {"scope": scope, "removed_count": removed}
