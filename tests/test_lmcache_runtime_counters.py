"""Tests for LMCache hit/miss counters read from the runtime metrics endpoint.

The sample exposition text is taken verbatim from the deployed vLLM v0.25.1
with the LMCache KV connector active (2026-07-30): the external prefix cache is
the KV-connector tier and its counters are measured in tokens. The rules under
test: figures come only from families that are actually present, absent sources
are named rather than reported as zeros, and the GPU-internal prefix cache
never leaks into the LMCache accounting.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from pathlib import Path

from lmrs.lmcache import observations_from_vllm_metrics

LIVE_SAMPLE = """\
# HELP vllm:prefix_cache_queries_total Number of prefix cache queries.
vllm:prefix_cache_queries_total{engine="0",model_name="Qwen/Qwen2.5-Coder-7B-Instruct"} 72.0
vllm:prefix_cache_queries_created{engine="0",model_name="Qwen/Qwen2.5-Coder-7B-Instruct"} 1.785414856271799e+09
vllm:prefix_cache_hits_total{engine="0",model_name="Qwen/Qwen2.5-Coder-7B-Instruct"} 32.0
vllm:external_prefix_cache_queries_total{engine="0",model_name="Qwen/Qwen2.5-Coder-7B-Instruct"} 40.0
vllm:external_prefix_cache_hits_total{engine="0",model_name="Qwen/Qwen2.5-Coder-7B-Instruct"} 12.0
vllm:kv_cache_usage_perc{engine="0",model_name="Qwen/Qwen2.5-Coder-7B-Instruct"} 0.25
"""


def test_external_cache_counters_become_hit_and_miss_tokens() -> None:
    """Hits map to hit_tokens and misses are queries minus hits."""
    observations = observations_from_vllm_metrics(LIVE_SAMPLE)

    assert observations["hit_tokens"] == 12
    assert observations["miss_tokens"] == 28
    assert observations["metadata"]["external_cache_families_present"] is True
    assert observations["metadata"]["counter_source"] == "vllm_metrics"


def test_the_gpu_internal_cache_stays_out_of_the_accounting() -> None:
    """The GPU prefix cache is context, never LMCache hit/miss figures."""
    observations = observations_from_vllm_metrics(LIVE_SAMPLE)

    assert observations["hit_tokens"] != 32
    context = observations["metadata"]["gpu_internal_context"]
    assert context["vllm:prefix_cache_queries_total"] == 72.0
    assert context["vllm:kv_cache_usage_perc"] == 0.25


def test_multiple_engines_are_summed() -> None:
    """Counters split across label sets are summed per family."""
    two_engines = (
        'vllm:external_prefix_cache_queries_total{engine="0"} 30.0\n'
        'vllm:external_prefix_cache_queries_total{engine="1"} 10.0\n'
        'vllm:external_prefix_cache_hits_total{engine="0"} 4.0\n'
        'vllm:external_prefix_cache_hits_total{engine="1"} 2.0\n'
    )

    observations = observations_from_vllm_metrics(two_engines)

    assert observations["hit_tokens"] == 6
    assert observations["miss_tokens"] == 34


def test_absent_families_produce_no_counters() -> None:
    """Without the external-cache families no figures are invented."""
    observations = observations_from_vllm_metrics("vllm:num_requests_running 0.0\n")

    assert "hit_tokens" not in observations
    assert "miss_tokens" not in observations
    assert observations["metadata"]["external_cache_families_present"] is False


def test_lmcache_own_families_are_surfaced_verbatim() -> None:
    """A future LMCache publishing its own families becomes visible unchanged."""
    text = LIVE_SAMPLE + 'lmcache:local_cache_usage_bytes 2048.0\n'

    observations = observations_from_vllm_metrics(text)

    assert observations["metadata"]["lmcache_families"] == {"lmcache:local_cache_usage_bytes": 2048.0}


def test_malformed_lines_are_skipped() -> None:
    """Garbage in the exposition text is ignored, not fatal."""
    text = "not a metric line\nvllm:external_prefix_cache_queries_total nan-ish\n" + LIVE_SAMPLE

    observations = observations_from_vllm_metrics(text)

    assert observations["hit_tokens"] == 12


def test_status_command_merges_disk_and_runtime_sources(tmp_path: Path, monkeypatch) -> None:
    """local_lmcache_status reports the measured disk tier plus runtime counters."""
    import asyncio

    from lmrs.adapter import registration
    from lmrs.lmcache import LMCacheStoragePolicy

    storage = tmp_path / "lmcache" / "ns" / "s1"
    storage.mkdir(parents=True)
    (storage / "chunk").write_bytes(b"0" * 512)
    monkeypatch.setattr(registration, "_LMCACHE_POLICY", LMCacheStoragePolicy(enabled=True, cache_storage_path=str(tmp_path / "lmcache")))
    # The client is a frozen dataclass, so the method is replaced on the class.
    monkeypatch.setattr(type(registration._VLLM_CLIENT), "fetch_metrics", lambda self: LIVE_SAMPLE)

    result = asyncio.run(registration.LocalLmcacheStatusCommand().execute())
    payload = result.to_dict()["data"]["payload"]

    assert payload["hit_tokens"] == 12
    assert payload["miss_tokens"] == 28
    assert payload["disk_cache_usage_bytes"] == 512
    assert payload["metadata"]["disk_tier_observed"] is True
    assert payload["metadata"]["runtime_counters_available"] is True


def test_status_command_names_an_unreachable_runtime(tmp_path: Path, monkeypatch) -> None:
    """An unanswering metrics endpoint is named, not reported as zero hits."""
    import asyncio

    from lmrs.adapter import registration
    from lmrs.lmcache import LMCacheStoragePolicy

    monkeypatch.setattr(registration, "_LMCACHE_POLICY", LMCacheStoragePolicy(enabled=True, cache_storage_path=str(tmp_path / "absent")))
    monkeypatch.setattr(type(registration._VLLM_CLIENT), "fetch_metrics", lambda self: None)

    result = asyncio.run(registration.LocalLmcacheStatusCommand().execute())
    payload = result.to_dict()["data"]["payload"]

    assert payload["metadata"]["runtime_counters_available"] is False
    assert "did not answer" in payload["metadata"]["runtime_counters_reason"]
    assert payload["metadata"]["disk_tier_observed"] is False
