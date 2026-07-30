"""Disk model cache for the LMRS package.

The cache is backed by the HuggingFace hub cache directory the runtime already
uses for model weights, so what LMRS reports is what vLLM will actually find on
disk. Nothing here loads weights into VRAM: disk residency and memory residency
are separate states by contract.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class CachedModelRecord:
    """Disk cache metadata for one downloaded model.

    Attributes:
        model_name: Name of the cached model.
        runtime_backend: Runtime backend this model is compatible with.
        model_path: Filesystem path to the cached model artifacts.
        quantization_profile: Quantization profile identifier.
        declared_context_window: Declared maximum context window in tokens.
        tokenizer_profile: Tokenizer profile identifier.
        checksum_or_revision: Integrity checksum or revision identifier.
        size_bytes: Total size of the cached model in bytes.
        downloaded_at: ISO timestamp when the model was downloaded.
        compatibility_flags: Mapping of compatibility requirement flags.
        cache_status: Current disk cache status of this model.
        readiness_marker_path: Path to the readiness marker file.
        metadata: Arbitrary metadata for this cache record.
    """

    model_name: str
    runtime_backend: str
    model_path: str
    quantization_profile: str
    declared_context_window: int
    tokenizer_profile: str
    checksum_or_revision: str
    size_bytes: int
    downloaded_at: str
    compatibility_flags: Mapping[str, bool] = field(default_factory=dict)
    cache_status: str = "not_cached"
    readiness_marker_path: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


class CacheState:
    """Disk cache and memory residency state constants.

    Separates disk cache state from VRAM residency state.

    Attributes:
        NOT_CACHED: Model not present in disk cache.
        CACHING: Model download or caching in progress.
        CACHED_ON_DISK: Model fully cached on disk and ready for loading.
        LOADING_TO_MEMORY: Model being loaded from disk to GPU memory.
        LOADED_IN_MEMORY: Model resident in GPU memory.
        UNLOADING: Model being unloaded from GPU memory.
        FAILED: Cache or load operation failed.
    """

    NOT_CACHED: str = "not_cached"
    CACHING: str = "caching"
    CACHED_ON_DISK: str = "cached_on_disk"
    LOADING_TO_MEMORY: str = "loading_to_memory"
    LOADED_IN_MEMORY: str = "loaded_in_memory"
    UNLOADING: str = "unloading"
    FAILED: str = "failed"


_HUB_REPO_PREFIX = "models--"
_STATE_DIR_NAME = ".lmrs"
_READINESS_MARKER_NAME = "ready.json"
_METADATA_FILE_NAME = "metadata.json"
_WEIGHT_SUFFIXES: tuple[str, ...] = (".safetensors", ".bin", ".gguf", ".pt", ".pth")
_TOKENIZER_FILES: tuple[str, ...] = ("tokenizer.json", "tokenizer.model", "tokenizer_config.json", "vocab.json")


def hub_cache_root(cache_root: str) -> Path:
    """Return the directory holding HuggingFace ``models--`` repositories.

    The container sets ``HF_HOME`` to the cache volume and
    ``HUGGINGFACE_HUB_CACHE`` to its ``hub`` subdirectory, so a configured cache
    root may name either level. A configured root that already is, or contains,
    a ``hub`` directory resolves to it; anything else is taken at face value.
    This function reads no environment: which root is configured is the caller's
    decision, and silently overriding it from the ambient environment would make
    the cache report a directory nobody asked for.

    Args:
        cache_root: Configured filesystem root of the disk model cache.

    Returns:
        The path under which repository directories live.
    """
    root = Path(cache_root)
    if root.name == "hub":
        return root
    hub = root / "hub"
    if hub.is_dir():
        return hub
    return root


def repo_directory_name(model_name: str) -> str:
    """Return the hub cache directory name for a model.

    Args:
        model_name: Repository-style model name, for example ``Qwen/Qwen3-8B``.

    Returns:
        The ``models--org--name`` directory name used by the hub cache.
    """
    return _HUB_REPO_PREFIX + model_name.strip("/").replace("/", "--")


def model_name_from_repo_directory(directory_name: str) -> str:
    """Return the model name a hub cache directory holds.

    The hub encoding is ambiguous for names that themselves contain ``--``;
    that ambiguity belongs to the hub layout and is reproduced here rather than
    invented, so the round trip matches what the runtime resolves.

    Args:
        directory_name: A ``models--org--name`` directory name.

    Returns:
        The decoded model name, or an empty string when the name is not a
        repository directory.
    """
    if not directory_name.startswith(_HUB_REPO_PREFIX):
        return ""
    return directory_name[len(_HUB_REPO_PREFIX):].replace("--", "/")


def _read_json(path: Path) -> dict[str, Any]:
    """Return a parsed JSON object, or an empty mapping when unreadable.

    Args:
        path: File to read.

    Returns:
        The parsed object, or an empty dict when the file is missing or invalid.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _resolve_revision(repo_directory: Path) -> str:
    """Return the revision a cached repository currently points at.

    Args:
        repo_directory: The ``models--`` directory of one model.

    Returns:
        The revision hash, or an empty string when it cannot be resolved.
    """
    refs = repo_directory / "refs"
    if refs.is_dir():
        for name in ("main", "master"):
            ref = refs / name
            if ref.is_file():
                try:
                    return ref.read_text(encoding="utf-8").strip()
                except OSError:
                    return ""
        for ref in sorted(refs.iterdir()):
            if ref.is_file():
                try:
                    return ref.read_text(encoding="utf-8").strip()
                except OSError:
                    continue
    snapshots = repo_directory / "snapshots"
    if snapshots.is_dir():
        directories = sorted(child.name for child in snapshots.iterdir() if child.is_dir())
        if len(directories) == 1:
            return directories[0]
    return ""


def _snapshot_directory(repo_directory: Path, revision: str) -> Path | None:
    """Return the snapshot directory carrying the resolved revision.

    Args:
        repo_directory: The ``models--`` directory of one model.
        revision: Revision hash resolved for that repository.

    Returns:
        The snapshot directory, or None when it does not exist.
    """
    if not revision:
        return None
    snapshot = repo_directory / "snapshots" / revision
    return snapshot if snapshot.is_dir() else None


def _snapshot_files(snapshot: Path) -> list[Path]:
    """Return every file entry inside a snapshot directory.

    Broken symlinks are included: a snapshot entry whose blob is gone is exactly
    what an integrity check must see.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        The entries found under the snapshot, in sorted order.
    """
    return sorted(path for path in snapshot.rglob("*") if path.is_file() or path.is_symlink())


def _snapshot_size_bytes(snapshot: Path) -> int:
    """Return the on-disk size of the blobs a snapshot references.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        Total size in bytes of the resolvable files, counting each blob once.
    """
    total = 0
    counted: set[str] = set()
    for path in _snapshot_files(snapshot):
        try:
            resolved = path.resolve(strict=True)
            key = str(resolved)
            if key in counted:
                continue
            counted.add(key)
            total += resolved.stat().st_size
        except OSError:
            continue
    return total


def _incomplete_downloads(repo_directory: Path) -> list[str]:
    """Return partially downloaded blob names of a repository.

    Args:
        repo_directory: The ``models--`` directory of one model.

    Returns:
        Names of ``.incomplete`` blobs, which mark a download still in flight.
    """
    blobs = repo_directory / "blobs"
    if not blobs.is_dir():
        return []
    return sorted(path.name for path in blobs.iterdir() if path.name.endswith(".incomplete"))


def _broken_entries(snapshot: Path) -> list[str]:
    """Return snapshot entries whose target blob is missing.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        Snapshot-relative names of entries that do not resolve to a file.
    """
    broken: list[str] = []
    for path in _snapshot_files(snapshot):
        try:
            if not path.resolve(strict=True).is_file():
                broken.append(str(path.relative_to(snapshot)))
        except OSError:
            broken.append(str(path.relative_to(snapshot)))
    return broken


def _missing_shards(snapshot: Path) -> list[str]:
    """Return shard files a weight index names but the snapshot lacks.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        Names of missing shards; empty when no index is present.
    """
    missing: list[str] = []
    for index_name in ("model.safetensors.index.json", "pytorch_model.bin.index.json"):
        index = snapshot / index_name
        if not index.is_file():
            continue
        weight_map = _read_json(index).get("weight_map")
        if not isinstance(weight_map, dict):
            continue
        for shard in sorted({str(value) for value in weight_map.values()}):
            if not (snapshot / shard).is_file():
                missing.append(shard)
    return missing


def _weight_files(snapshot: Path) -> list[str]:
    """Return the weight files a snapshot carries.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        Snapshot-relative names of files with a known weight suffix.
    """
    return sorted(
        str(path.relative_to(snapshot))
        for path in _snapshot_files(snapshot)
        if path.suffix in _WEIGHT_SUFFIXES
    )


def _declared_context_window(model_config: Mapping[str, Any]) -> int:
    """Return the context window a model config declares.

    Args:
        model_config: Parsed ``config.json`` of the cached model.

    Returns:
        The declared window in tokens, or 0 when the config does not state one.
    """
    for key in ("max_position_embeddings", "max_sequence_length", "n_positions", "model_max_length"):
        value = model_config.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return 0


def _quantization_profile(model_config: Mapping[str, Any]) -> str:
    """Return the quantization profile a model config declares.

    Args:
        model_config: Parsed ``config.json`` of the cached model.

    Returns:
        The quantization method when one is configured, otherwise the weight
        dtype, and ``unknown`` when neither is stated.
    """
    quantization = model_config.get("quantization_config")
    if isinstance(quantization, Mapping):
        method = quantization.get("quant_method") or quantization.get("quant_type")
        if isinstance(method, str) and method:
            return method
    dtype = model_config.get("torch_dtype") or model_config.get("dtype")
    if isinstance(dtype, str) and dtype:
        return dtype
    return "unknown"


def _tokenizer_profile(snapshot: Path) -> str:
    """Return the tokenizer profile a snapshot provides.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        The name of the tokenizer artifact found, or ``unknown``.
    """
    for name in _TOKENIZER_FILES:
        if (snapshot / name).is_file():
            return name
    return "unknown"


def _downloaded_at(snapshot: Path) -> str:
    """Return the ISO timestamp of the snapshot directory.

    Args:
        snapshot: Snapshot directory of one revision.

    Returns:
        The modification time in ISO form, or an empty string when unavailable.
    """
    try:
        return datetime.fromtimestamp(snapshot.stat().st_mtime, UTC).isoformat()
    except OSError:
        return ""


@dataclass
class DiskModelCache:
    """Disk-backed model cache owning cached model records.

    Owns the public disk-cache command surface: preload, status, delete, list,
    integrity check, and metadata update. Every command reads the real cache
    directory, so a model reported as cached is a model the runtime can load,
    and none of them ever loads weights into GPU memory.

    LMRS-owned state - readiness markers and operator metadata - is kept in a
    ``.lmrs`` directory beside the hub repositories rather than inside them, so
    the hub cache layout stays exactly as the runtime maintains it.

    Attributes:
        cache_root: Filesystem root directory holding cached model artifacts.
        records: Records supplied by the caller; used only when the cache
            directory does not describe the model, which keeps the object
            usable in contexts that have no filesystem cache.
        runtime_backend: Runtime backend the cached models are prepared for.
    """

    cache_root: str
    records: tuple[CachedModelRecord, ...] = ()
    runtime_backend: str = "vllm"

    def _hub_root(self) -> Path:
        """Return the directory holding hub repository directories."""
        return hub_cache_root(self.cache_root)

    def _state_directory(self, model_name: str) -> Path:
        """Return the LMRS state directory for one model.

        Args:
            model_name: Name of the model.

        Returns:
            The directory holding the readiness marker and stored metadata.
        """
        return Path(self.cache_root) / _STATE_DIR_NAME / repo_directory_name(model_name)

    def _stored_metadata(self, model_name: str) -> dict[str, Any]:
        """Return operator metadata previously stored for a model.

        Args:
            model_name: Name of the model.

        Returns:
            The stored metadata mapping, empty when nothing was stored.
        """
        return _read_json(self._state_directory(model_name) / _METADATA_FILE_NAME)

    def _readiness_marker(self, model_name: str) -> Path:
        """Return the readiness marker path for one model.

        Args:
            model_name: Name of the model.

        Returns:
            The readiness marker path, whether or not it exists yet.
        """
        return self._state_directory(model_name) / _READINESS_MARKER_NAME

    def _write_readiness_marker(self, record: CachedModelRecord) -> str:
        """Write the readiness marker describing a cached model.

        Args:
            record: The record the marker describes.

        Returns:
            The marker path, or an empty string when it could not be written.
        """
        marker = self._readiness_marker(record.model_name)
        payload = {
            "model_name": record.model_name,
            "revision": record.checksum_or_revision,
            "model_path": record.model_path,
            "size_bytes": record.size_bytes,
            "runtime_backend": record.runtime_backend,
            "written_at": datetime.now(UTC).isoformat(),
        }
        try:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return ""
        return str(marker)

    def _scan_record(self, model_name: str) -> CachedModelRecord | None:
        """Build the cache record of one model from the cache directory.

        Args:
            model_name: Name of the model to describe.

        Returns:
            The record describing what is on disk, or None when the model has
            no repository directory at all.
        """
        repo_directory = self._hub_root() / repo_directory_name(model_name)
        if not repo_directory.is_dir():
            return None
        revision = _resolve_revision(repo_directory)
        snapshot = _snapshot_directory(repo_directory, revision)
        incomplete = _incomplete_downloads(repo_directory)
        marker = self._readiness_marker(model_name)
        stored_metadata = self._stored_metadata(model_name)
        if snapshot is None:
            return CachedModelRecord(
                model_name=model_name,
                runtime_backend=self.runtime_backend,
                model_path=str(repo_directory),
                quantization_profile="unknown",
                declared_context_window=0,
                tokenizer_profile="unknown",
                checksum_or_revision=revision,
                size_bytes=0,
                downloaded_at="",
                compatibility_flags={"snapshot_present": False, "download_in_progress": bool(incomplete)},
                cache_status=CacheState.CACHING if incomplete else CacheState.FAILED,
                readiness_marker_path=str(marker) if marker.is_file() else None,
                metadata={"repo_directory": str(repo_directory), "incomplete_blobs": incomplete, "stored_metadata": stored_metadata},
            )
        model_config = _read_json(snapshot / "config.json")
        weights = _weight_files(snapshot)
        broken = _broken_entries(snapshot)
        missing_shards = _missing_shards(snapshot)
        complete = bool(weights) and not broken and not missing_shards and not incomplete
        if incomplete:
            status = CacheState.CACHING
        elif complete:
            status = CacheState.CACHED_ON_DISK
        else:
            status = CacheState.FAILED
        return CachedModelRecord(
            model_name=model_name,
            runtime_backend=self.runtime_backend,
            model_path=str(snapshot),
            quantization_profile=_quantization_profile(model_config),
            declared_context_window=_declared_context_window(model_config),
            tokenizer_profile=_tokenizer_profile(snapshot),
            checksum_or_revision=revision,
            size_bytes=_snapshot_size_bytes(snapshot),
            downloaded_at=_downloaded_at(snapshot),
            compatibility_flags={
                "config_present": bool(model_config),
                "weights_present": bool(weights),
                "tokenizer_present": _tokenizer_profile(snapshot) != "unknown",
                "download_complete": not incomplete,
                "entries_resolvable": not broken,
            },
            cache_status=status,
            readiness_marker_path=str(marker) if marker.is_file() else None,
            metadata={
                "repo_directory": str(repo_directory),
                "weight_files": weights,
                "broken_entries": broken,
                "missing_shards": missing_shards,
                "incomplete_blobs": incomplete,
                "stored_metadata": stored_metadata,
            },
        )

    def _seeded_record(self, model_name: str) -> CachedModelRecord | None:
        """Return a caller-supplied record for a model, if there is one.

        Args:
            model_name: Name of the model.

        Returns:
            The supplied record, or None.
        """
        return next((item for item in self.records if item.model_name == model_name), None)

    def _record(self, model_name: str) -> CachedModelRecord | None:
        """Return the current record for a model.

        The cache directory is the truth; a caller-supplied record answers only
        for a model the directory says nothing about.

        Args:
            model_name: Name of the model.

        Returns:
            The record describing the model, or None when it is not cached.
        """
        return self._scan_record(model_name) or self._seeded_record(model_name)

    def _cached_model_names(self) -> list[str]:
        """Return every model name the cache directory holds.

        Returns:
            Decoded model names, in sorted order.
        """
        hub_root = self._hub_root()
        if not hub_root.is_dir():
            return []
        names = {
            model_name_from_repo_directory(child.name)
            for child in hub_root.iterdir()
            if child.is_dir() and child.name.startswith(_HUB_REPO_PREFIX)
        }
        return sorted(name for name in names if name)

    def preload(self, model_name: str) -> CacheCommandResult:
        """Download and cache a model on disk without loading it into memory.

        A model already complete on disk is not downloaded again; the readiness
        marker is refreshed and the existing record is reported. Otherwise the
        weights, tokenizer files and metadata are fetched into the same hub
        cache directory the runtime reads, and the result reports what landed on
        disk rather than what was requested.

        Args:
            model_name: Name of the model to preload onto disk.

        Returns:
            A CacheCommandResult describing the preload outcome.
        """
        existing = self._record(model_name)
        if existing is not None and existing.cache_status == CacheState.CACHED_ON_DISK:
            marker_path = self._write_readiness_marker(existing)
            return CacheCommandResult(
                command="preload",
                model_name=model_name,
                status=CacheState.CACHED_ON_DISK,
                success=True,
                record=existing,
                metadata={"cache_root": self.cache_root, "already_cached": True, "readiness_marker_path": marker_path},
            )
        try:
            # Imported here, not at module scope: the hub client ships with the
            # runtime image but is not needed to read an existing cache, so the
            # package stays importable without it.
            from huggingface_hub import snapshot_download  # type: ignore[import-not-found]
        except ImportError as error:
            return CacheCommandResult(
                command="preload",
                model_name=model_name,
                status=CacheState.NOT_CACHED,
                success=False,
                reason_code="HF_HUB_UNAVAILABLE",
                metadata={"cache_root": self.cache_root, "error": str(error)},
            )
        hub_root = self._hub_root()
        try:
            hub_root.mkdir(parents=True, exist_ok=True)
            downloaded = snapshot_download(
                repo_id=model_name,
                cache_dir=str(hub_root),
                token=os.environ.get("HF_TOKEN") or None,
            )
        except Exception as error:  # noqa: BLE001 - a failed download is reported, never raised
            return CacheCommandResult(
                command="preload",
                model_name=model_name,
                status=CacheState.FAILED,
                success=False,
                reason_code="MODEL_CACHE_PRELOAD_FAILED",
                metadata={
                    "cache_root": self.cache_root,
                    "hub_cache_root": str(hub_root),
                    "error": str(error),
                    "exception_type": type(error).__name__,
                },
            )
        record = self._scan_record(model_name)
        if record is None or record.cache_status != CacheState.CACHED_ON_DISK:
            return CacheCommandResult(
                command="preload",
                model_name=model_name,
                status=record.cache_status if record else CacheState.NOT_CACHED,
                success=False,
                reason_code="MODEL_CACHE_PRELOAD_FAILED",
                record=record,
                metadata={"cache_root": self.cache_root, "downloaded_path": str(downloaded)},
            )
        marker_path = self._write_readiness_marker(record)
        return CacheCommandResult(
            command="preload",
            model_name=model_name,
            status=record.cache_status,
            success=True,
            record=record,
            metadata={"cache_root": self.cache_root, "downloaded_path": str(downloaded), "readiness_marker_path": marker_path},
        )

    def status(self, model_name: str) -> CacheCommandResult:
        """Report the disk cache status of a model.

        Args:
            model_name: Name of the model to inspect.

        Returns:
            A CacheCommandResult carrying the model's disk cache status.
        """
        record = self._record(model_name)
        if record is None:
            return CacheCommandResult(
                command="status",
                model_name=model_name,
                status=CacheState.NOT_CACHED,
                success=False,
                reason_code="MODEL_NOT_CACHED",
                metadata={"cache_root": self.cache_root, "hub_cache_root": str(self._hub_root())},
            )
        return CacheCommandResult(
            command="status",
            model_name=model_name,
            status=record.cache_status,
            success=record.cache_status == CacheState.CACHED_ON_DISK,
            reason_code=None if record.cache_status == CacheState.CACHED_ON_DISK else "MODEL_CACHE_INCOMPLETE",
            record=record,
        )

    def delete(self, model_name: str) -> CacheCommandResult:
        """Remove a model's artifacts from the disk cache.

        The repository directory and the LMRS state directory are both removed,
        so a deleted model leaves neither weights nor a readiness marker behind.

        Args:
            model_name: Name of the model to delete from disk.

        Returns:
            A CacheCommandResult describing the deletion outcome.
        """
        record = self._record(model_name)
        if record is None:
            return CacheCommandResult(
                command="delete",
                model_name=model_name,
                status=CacheState.NOT_CACHED,
                success=False,
                reason_code="MODEL_NOT_CACHED",
            )
        repo_directory = self._hub_root() / repo_directory_name(model_name)
        removed: list[str] = []
        try:
            if repo_directory.is_dir():
                shutil.rmtree(repo_directory)
                removed.append(str(repo_directory))
            state_directory = self._state_directory(model_name)
            if state_directory.is_dir():
                shutil.rmtree(state_directory)
                removed.append(str(state_directory))
        except OSError as error:
            return CacheCommandResult(
                command="delete",
                model_name=model_name,
                status=record.cache_status,
                success=False,
                reason_code="MODEL_CACHE_DELETE_FAILED",
                record=record,
                metadata={"error": str(error), "removed_paths": removed},
            )
        self.records = tuple(item for item in self.records if item.model_name != model_name)
        return CacheCommandResult(
            command="delete",
            model_name=model_name,
            status=CacheState.NOT_CACHED,
            success=True,
            record=record,
            metadata={"removed_paths": removed, "freed_bytes": record.size_bytes},
        )

    def list_models(self) -> CacheCommandResult:
        """List all models currently tracked in the disk cache.

        Returns:
            A CacheCommandResult enumerating the cached models.
        """
        names = self._cached_model_names()
        seeded = [item.model_name for item in self.records if item.model_name not in names]
        records = [self._record(name) for name in [*names, *seeded]]
        present = [record for record in records if record is not None]
        return CacheCommandResult(
            command="list_models",
            model_name="",
            status=CacheState.CACHED_ON_DISK,
            success=True,
            metadata={
                "models": [record.model_name for record in present],
                "count": len(present),
                "cache_root": self.cache_root,
                "hub_cache_root": str(self._hub_root()),
                "entries": [
                    {
                        "model_name": record.model_name,
                        "cache_status": record.cache_status,
                        "size_bytes": record.size_bytes,
                        "revision": record.checksum_or_revision,
                        "model_path": record.model_path,
                    }
                    for record in present
                ],
            },
        )

    def check_integrity(self, model_name: str) -> CacheCommandResult:
        """Verify the on-disk integrity of a cached model.

        Integrity means the snapshot resolves: a config, at least one weight
        file, every shard a weight index names, no unresolvable entry and no
        download still in flight.

        Args:
            model_name: Name of the model to integrity-check.

        Returns:
            A CacheCommandResult describing the integrity verification outcome.
        """
        record = self._record(model_name)
        if record is None:
            return CacheCommandResult(
                command="check_integrity",
                model_name=model_name,
                status=CacheState.NOT_CACHED,
                success=False,
                reason_code="MODEL_NOT_CACHED",
            )
        problems: dict[str, object] = {}
        for key in ("broken_entries", "missing_shards", "incomplete_blobs"):
            value = record.metadata.get(key)
            if isinstance(value, list) and value:
                problems[key] = value
        if not record.compatibility_flags.get("weights_present", False):
            problems["weights_present"] = False
        success = record.cache_status == CacheState.CACHED_ON_DISK and not problems
        return CacheCommandResult(
            command="check_integrity",
            model_name=model_name,
            status=record.cache_status if success else CacheState.FAILED,
            success=success,
            reason_code=None if success else "MODEL_CACHE_CORRUPTED",
            record=record,
            metadata={"problems": problems, "model_path": record.model_path},
        )

    def update_metadata(
        self, model_name: str, metadata: Mapping[str, object]
    ) -> CacheCommandResult:
        """Update stored metadata for a cached model.

        The metadata is operator-owned and stored beside the cache rather than
        inside the hub repository, so it survives a re-download and never
        confuses the runtime.

        Args:
            model_name: Name of the model whose metadata is updated.
            metadata: New metadata mapping to associate with the model.

        Returns:
            A CacheCommandResult describing the metadata update outcome.
        """
        record = self._record(model_name)
        if record is None:
            return CacheCommandResult(
                command="update_metadata",
                model_name=model_name,
                status=CacheState.NOT_CACHED,
                success=False,
                reason_code="MODEL_NOT_CACHED",
            )
        merged = {**self._stored_metadata(model_name), **dict(metadata)}
        path = self._state_directory(model_name) / _METADATA_FILE_NAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(merged, indent=2, sort_keys=True, default=str), encoding="utf-8")
        except OSError as error:
            return CacheCommandResult(
                command="update_metadata",
                model_name=model_name,
                status=record.cache_status,
                success=False,
                reason_code="MODEL_CACHE_METADATA_WRITE_FAILED",
                record=record,
                metadata={"error": str(error), "metadata_path": str(path)},
            )
        return CacheCommandResult(
            command="update_metadata",
            model_name=model_name,
            status=record.cache_status,
            success=True,
            record=record,
            metadata={"metadata": merged, "metadata_path": str(path)},
        )


@dataclass(frozen=True)
class CacheCommandResult:
    """Structured result of a disk cache public command.

    Attributes:
        command: Name of the disk cache command that produced this result.
        model_name: Name of the model the command acted on.
        status: Resulting disk cache status for the model.
        success: Whether the command completed successfully.
        reason_code: Stable machine-readable reason for the outcome.
        record: Optional cache record describing the affected model.
        metadata: Arbitrary metadata about the command execution.
    """

    command: str
    model_name: str
    status: str
    success: bool
    reason_code: str | None = None
    record: CachedModelRecord | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
