"""Tests for the disk-backed model cache.

The cache reports what is on disk, not what a caller hoped for: a fabricated hub
layout stands in for a real download here, so every state the commands can
report - complete, still downloading, corrupted, absent - is exercised without
touching the network.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

from lmrs.model_cache import (
    CacheState,
    DiskModelCache,
    hub_cache_root,
    model_name_from_repo_directory,
    repo_directory_name,
)

MODEL = "acme/tiny-model"
REVISION = "c03e6d358207e414f1eca0bb1891e29f1db0e242"


def _build_snapshot(hub_root: Path, model_name: str = MODEL, *, shards: int = 1, with_index: bool = False) -> Path:
    """Create a hub cache layout for one model.

    Args:
        hub_root: Directory holding repository directories.
        model_name: Model to fabricate.
        shards: Number of weight shards to write.
        with_index: Whether to write a weight index naming those shards.

    Returns:
        The snapshot directory that was created.
    """
    repo = hub_root / repo_directory_name(model_name)
    snapshot = repo / "snapshots" / REVISION
    blobs = repo / "blobs"
    snapshot.mkdir(parents=True)
    blobs.mkdir(parents=True)
    (repo / "refs").mkdir()
    (repo / "refs" / "main").write_text(REVISION, encoding="utf-8")
    (snapshot / "config.json").write_text(
        json.dumps({
            "num_hidden_layers": 48,
            "num_attention_heads": 32,
            "num_key_value_heads": 4,
            "head_dim": 128,
            "torch_dtype": "bfloat16",
            "max_position_embeddings": 40960,
        }),
        encoding="utf-8",
    )
    (snapshot / "tokenizer.json").write_text("{}", encoding="utf-8")
    names = [f"model-{index:05d}-of-{shards:05d}.safetensors" for index in range(1, shards + 1)]
    for name in names:
        blob = blobs / f"blob-{name}"
        blob.write_bytes(b"0" * 1024)
        (snapshot / name).symlink_to(blob)
    if with_index:
        (snapshot / "model.safetensors.index.json").write_text(
            json.dumps({"weight_map": {f"layer.{index}": name for index, name in enumerate(names)}}),
            encoding="utf-8",
        )
    return snapshot


def test_hub_root_prefers_the_hub_subdirectory(tmp_path: Path) -> None:
    """A cache root holding a hub directory resolves to it."""
    (tmp_path / "hub").mkdir()

    assert hub_cache_root(str(tmp_path)) == tmp_path / "hub"
    assert hub_cache_root(str(tmp_path / "hub")) == tmp_path / "hub"


def test_repo_directory_names_round_trip() -> None:
    """The hub directory encoding is reproduced in both directions."""
    assert repo_directory_name(MODEL) == "models--acme--tiny-model"
    assert model_name_from_repo_directory("models--acme--tiny-model") == MODEL
    assert model_name_from_repo_directory("not-a-repo") == ""


def test_status_reports_a_complete_snapshot_as_cached(tmp_path: Path) -> None:
    """A complete snapshot is reported as cached with its measured facts."""
    _build_snapshot(tmp_path)
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.status(MODEL)

    assert result.success is True
    assert result.status == CacheState.CACHED_ON_DISK
    assert result.record is not None
    assert result.record.checksum_or_revision == REVISION
    # Weights plus config and tokenizer: the size is what the snapshot occupies,
    # not the weight blob alone.
    assert result.record.size_bytes > 1024
    assert result.record.declared_context_window == 40960
    assert result.record.quantization_profile == "bfloat16"
    assert result.record.tokenizer_profile == "tokenizer.json"


def test_status_reports_a_missing_model(tmp_path: Path) -> None:
    """A model with no repository directory is not cached."""
    result = DiskModelCache(cache_root=str(tmp_path)).status("absent/model")

    assert result.success is False
    assert result.reason_code == "MODEL_NOT_CACHED"
    assert result.status == CacheState.NOT_CACHED


def test_an_interrupted_download_reports_caching(tmp_path: Path) -> None:
    """An in-flight download is caching, not cached."""
    _build_snapshot(tmp_path)
    (tmp_path / repo_directory_name(MODEL) / "blobs" / "part.incomplete").write_bytes(b"0")
    cache = DiskModelCache(cache_root=str(tmp_path))

    status = cache.status(MODEL)
    integrity = cache.check_integrity(MODEL)

    assert status.status == CacheState.CACHING
    assert status.success is False
    assert integrity.success is False
    assert integrity.reason_code == "MODEL_CACHE_CORRUPTED"


def test_integrity_detects_a_missing_shard(tmp_path: Path) -> None:
    """A shard the weight index names but the snapshot lacks fails integrity."""
    snapshot = _build_snapshot(tmp_path, shards=2, with_index=True)
    (snapshot / "model-00002-of-00002.safetensors").unlink()
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.check_integrity(MODEL)

    assert result.success is False
    assert result.reason_code == "MODEL_CACHE_CORRUPTED"
    assert result.metadata["problems"]["missing_shards"] == ["model-00002-of-00002.safetensors"]


def test_integrity_detects_a_broken_snapshot_entry(tmp_path: Path) -> None:
    """A snapshot entry whose blob is gone fails integrity."""
    snapshot = _build_snapshot(tmp_path)
    (tmp_path / repo_directory_name(MODEL) / "blobs" / "blob-model-00001-of-00001.safetensors").unlink()
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.check_integrity(MODEL)

    assert result.success is False
    assert "broken_entries" in result.metadata["problems"]
    assert snapshot.is_dir()


def test_list_models_enumerates_the_cache_directory(tmp_path: Path) -> None:
    """Every repository directory is listed with its state."""
    _build_snapshot(tmp_path)
    _build_snapshot(tmp_path, "acme/second-model")
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.list_models()

    assert result.success is True
    assert result.metadata["count"] == 2
    assert sorted(result.metadata["models"]) == ["acme/second-model", "acme/tiny-model"]


def test_delete_removes_the_weights_from_disk(tmp_path: Path) -> None:
    """Deleting a model removes its repository directory and its LMRS state."""
    _build_snapshot(tmp_path)
    cache = DiskModelCache(cache_root=str(tmp_path))
    cache.update_metadata(MODEL, {"owner": "acceptance"})

    result = cache.delete(MODEL)

    assert result.success is True
    assert result.metadata["freed_bytes"] > 1024
    assert not (tmp_path / repo_directory_name(MODEL)).exists()
    assert not (tmp_path / ".lmrs" / repo_directory_name(MODEL)).exists()
    assert cache.status(MODEL).reason_code == "MODEL_NOT_CACHED"


def test_delete_reports_a_model_that_was_never_cached(tmp_path: Path) -> None:
    """Deleting an absent model is refused with a stable reason."""
    result = DiskModelCache(cache_root=str(tmp_path)).delete("absent/model")

    assert result.success is False
    assert result.reason_code == "MODEL_NOT_CACHED"


def test_update_metadata_persists_beside_the_cache(tmp_path: Path) -> None:
    """Operator metadata is merged and stored outside the hub layout."""
    _build_snapshot(tmp_path)
    cache = DiskModelCache(cache_root=str(tmp_path))

    cache.update_metadata(MODEL, {"owner": "acceptance"})
    result = cache.update_metadata(MODEL, {"purpose": "live-check"})

    stored = json.loads((tmp_path / ".lmrs" / repo_directory_name(MODEL) / "metadata.json").read_text(encoding="utf-8"))
    assert result.success is True
    assert stored == {"owner": "acceptance", "purpose": "live-check"}
    assert cache.status(MODEL).record is not None


def test_preload_reports_an_already_cached_model_without_downloading(tmp_path: Path) -> None:
    """A complete model is not downloaded again; the marker is written."""
    _build_snapshot(tmp_path)
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.preload(MODEL)

    assert result.success is True
    assert result.metadata["already_cached"] is True
    assert (tmp_path / ".lmrs" / repo_directory_name(MODEL) / "ready.json").is_file()


def test_preload_downloads_a_missing_model(tmp_path: Path, monkeypatch) -> None:
    """A missing model is fetched through the hub client and then verified."""
    calls: list[dict[str, object]] = []

    def fake_snapshot_download(**kwargs: object) -> str:
        calls.append(kwargs)
        return str(_build_snapshot(tmp_path))

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=fake_snapshot_download))
    monkeypatch.setenv("HF_TOKEN", "token-value")
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.preload(MODEL)

    assert result.success is True
    assert result.status == CacheState.CACHED_ON_DISK
    assert calls[0]["repo_id"] == MODEL
    assert calls[0]["token"] == "token-value"
    assert (tmp_path / ".lmrs" / repo_directory_name(MODEL) / "ready.json").is_file()


def test_preload_reports_a_failed_download(tmp_path: Path, monkeypatch) -> None:
    """A download failure is reported with a stable reason, never raised."""
    def failing_download(**kwargs: object) -> str:
        raise OSError("hub is unreachable")

    monkeypatch.setitem(sys.modules, "huggingface_hub", types.SimpleNamespace(snapshot_download=failing_download))
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.preload(MODEL)

    assert result.success is False
    assert result.reason_code == "MODEL_CACHE_PRELOAD_FAILED"
    assert result.metadata["exception_type"] == "OSError"


def test_preload_reports_a_missing_hub_client(tmp_path: Path, monkeypatch) -> None:
    """Without the hub client the command says so instead of failing opaquely."""
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)
    cache = DiskModelCache(cache_root=str(tmp_path))

    result = cache.preload(MODEL)

    assert result.success is False
    assert result.reason_code == "HF_HUB_UNAVAILABLE"
