"""Tests for the LMCache operations commands (C-058).

Status is read-only, purge is global or scoped to a namespace/session
binding, and neither command may reach admission control.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from lmrs.adapter.registration import (
    LocalLmcachePurgeCommand,
    LocalLmcacheStatusCommand,
)
from lmrs.lmcache import (
    LMCacheStoragePolicy,
    LMCacheTelemetry,
    get_lmcache_status,
    purge_lmcache,
)


def _policy(tmp_path: Path | None = None, *, enabled: bool = True) -> LMCacheStoragePolicy:
    """Build a storage policy, optionally rooted at a temporary cache path.

    Args:
        tmp_path: Directory used as the disk cache tier, if any.
        enabled: Whether the policy reports LMCache as enabled.

    Returns:
        An LMCacheStoragePolicy for use in a test.
    """
    return LMCacheStoragePolicy(
        enabled=enabled,
        cache_storage_path=str(tmp_path) if tmp_path is not None else None,
        cpu_cache_limit_bytes=4096,
        disk_cache_limit_bytes=8192,
    )


def _artifact(root: Path, *parts: str) -> Path:
    """Create one fake cache artifact under the given path parts.

    Args:
        root: Cache storage root.
        *parts: Path components below the root; the last one names the file.

    Returns:
        The created artifact path.
    """
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("cached", encoding="utf-8")
    return target


def test_status_reports_every_documented_field_from_the_telemetry_source() -> None:
    """get_lmcache_status sources all documented fields from the telemetry source."""
    telemetry = LMCacheTelemetry(
        hit_tokens=120,
        miss_tokens=30,
        hit_quality="high",
        cpu_cache_usage=1024,
        cpu_cache_limit=4096,
        disk_cache_usage=2048,
        disk_cache_limit=8192,
        evictions=7,
    )

    status = get_lmcache_status(_policy(), telemetry)

    assert status == {
        "enabled": True,
        "cpu_cache_usage_bytes": 1024,
        "cpu_cache_limit_bytes": 4096,
        "disk_cache_usage_bytes": 2048,
        "disk_cache_limit_bytes": 8192,
        "hit_tokens": 120,
        "miss_tokens": 30,
        "hit_quality": "high",
        "evictions": 7,
        "metadata": {},
    }


def test_status_accepts_raw_observations_and_a_callable_source() -> None:
    """A mapping of raw observations and a callable both resolve to the same status."""
    observations: dict[str, Any] = {
        "hit_tokens": 5,
        "miss_tokens": 2,
        "hit_quality": "low",
        "cpu_cache_usage": 64,
        "disk_cache_usage": 128,
        "evictions": 1,
    }
    policy = _policy()

    from_mapping = get_lmcache_status(policy, observations)
    from_callable = get_lmcache_status(policy, lambda: observations)

    assert from_mapping == from_callable
    assert from_mapping["hit_tokens"] == 5
    assert from_mapping["miss_tokens"] == 2
    # Limits are policy facts, so raw observations still report them.
    assert from_mapping["cpu_cache_limit_bytes"] == 4096
    assert from_mapping["disk_cache_limit_bytes"] == 8192


def test_status_reports_disabled_policy_without_touching_storage(tmp_path: Path) -> None:
    """Status reflects the policy enablement flag and never inspects the cache path."""
    _artifact(tmp_path, "loose.bin")

    status = get_lmcache_status(_policy(tmp_path, enabled=False), LMCacheTelemetry())

    assert status["enabled"] is False
    assert (tmp_path / "loose.bin").exists()


def test_status_rejects_an_unusable_source() -> None:
    """A source that is neither telemetry, a mapping, nor a callable is refused."""
    with pytest.raises(TypeError):
        get_lmcache_status(_policy(), 42)  # type: ignore[arg-type]


def test_scoped_purge_removes_only_the_matching_binding(tmp_path: Path) -> None:
    """A namespace/session purge removes only artifacts under that binding."""
    _artifact(tmp_path, "loose.bin")
    _artifact(tmp_path, "alpha", "s1", "a.bin")
    _artifact(tmp_path, "alpha", "s2", "b.bin")
    _artifact(tmp_path, "beta", "s1", "c.bin")

    result = purge_lmcache(_policy(tmp_path), namespace="alpha", session="s1")

    assert result == {"scope": "namespace:alpha/session:s1", "removed_count": 1}
    assert not (tmp_path / "alpha" / "s1").exists()
    assert (tmp_path / "alpha" / "s2" / "b.bin").exists()
    assert (tmp_path / "beta" / "s1" / "c.bin").exists()
    assert (tmp_path / "loose.bin").exists()


def test_namespace_purge_removes_the_whole_namespace(tmp_path: Path) -> None:
    """A namespace-only purge removes every session under that namespace."""
    _artifact(tmp_path, "alpha", "s1", "a.bin")
    _artifact(tmp_path, "alpha", "s2", "b.bin")
    _artifact(tmp_path, "beta", "s1", "c.bin")

    result = purge_lmcache(_policy(tmp_path), namespace="alpha")

    assert result == {"scope": "namespace:alpha", "removed_count": 2}
    assert not (tmp_path / "alpha").exists()
    assert (tmp_path / "beta" / "s1" / "c.bin").exists()


def test_session_purge_spans_namespaces(tmp_path: Path) -> None:
    """A session-only purge removes that session under every namespace."""
    _artifact(tmp_path, "alpha", "s1", "a.bin")
    _artifact(tmp_path, "beta", "s1", "b.bin")
    _artifact(tmp_path, "beta", "s2", "c.bin")

    result = purge_lmcache(_policy(tmp_path), session="s1")

    assert result == {"scope": "session:s1", "removed_count": 2}
    assert not (tmp_path / "alpha" / "s1").exists()
    assert not (tmp_path / "beta" / "s1").exists()
    assert (tmp_path / "beta" / "s2" / "c.bin").exists()


def test_global_purge_removes_everything(tmp_path: Path) -> None:
    """A purge with no binding removes every artifact under the cache root."""
    _artifact(tmp_path, "loose.bin")
    _artifact(tmp_path, "alpha", "s1", "a.bin")
    _artifact(tmp_path, "beta", "s2", "b.bin")

    result = purge_lmcache(_policy(tmp_path))

    assert result == {"scope": "global", "removed_count": 3}
    assert list(tmp_path.iterdir()) == []


def test_purge_is_a_noop_without_a_cache_path(tmp_path: Path) -> None:
    """An unset or absent cache path purges nothing instead of failing."""
    assert purge_lmcache(_policy()) == {"scope": "global", "removed_count": 0}

    missing = LMCacheStoragePolicy(enabled=True, cache_storage_path=str(tmp_path / "absent"))
    assert purge_lmcache(missing, namespace="alpha") == {"scope": "namespace:alpha", "removed_count": 0}


def test_status_command_wraps_the_domain_function() -> None:
    """LocalLmcacheStatusCommand returns a SuccessResult around the status dict."""
    result = asyncio.run(LocalLmcacheStatusCommand().execute())
    payload = result.to_dict()["data"]["payload"]

    assert payload.keys() == {
        "enabled",
        "cpu_cache_usage_bytes",
        "cpu_cache_limit_bytes",
        "disk_cache_usage_bytes",
        "disk_cache_limit_bytes",
        "hit_tokens",
        "miss_tokens",
        "hit_quality",
        "evictions",
        "metadata",
    }


def test_purge_command_wraps_the_domain_function(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """LocalLmcachePurgeCommand returns a SuccessResult around the purge summary."""
    _artifact(tmp_path, "alpha", "s1", "a.bin")
    monkeypatch.setattr("lmrs.adapter.registration._LMCACHE_POLICY", _policy(tmp_path))

    result = asyncio.run(LocalLmcachePurgeCommand().execute(namespace="alpha"))
    payload = result.to_dict()["data"]["payload"]

    assert payload == {"scope": "namespace:alpha", "removed_count": 1}
    assert not (tmp_path / "alpha").exists()


def test_neither_command_touches_admission_control(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Admission control is never consulted by the status or purge command."""
    calls: list[Any] = []

    def _spy(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise AssertionError("admission control must not be reached by an LMCache command")

    monkeypatch.setattr("lmrs.admission.decide_admission", _spy)
    _artifact(tmp_path, "alpha", "s1", "a.bin")
    monkeypatch.setattr("lmrs.adapter.registration._LMCACHE_POLICY", _policy(tmp_path))

    asyncio.run(LocalLmcacheStatusCommand().execute())
    asyncio.run(LocalLmcachePurgeCommand().execute(namespace="alpha"))

    assert calls == []
