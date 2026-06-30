"""Disk model cache contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


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


@dataclass
class DiskModelCache:
    """Disk-backed model cache owning cached model records.

    Exposes the public disk-cache command surface as contract stubs: preload,
    status, delete, list, integrity check, and metadata update. All commands
    operate on disk cache metadata and readiness only and never load model
    weights into GPU memory.

    Attributes:
        cache_root: Filesystem root directory holding cached model artifacts.
        records: Cache records currently tracked on disk by this cache.
    """

    cache_root: str
    records: tuple[CachedModelRecord, ...] = ()

    def preload(self, model_name: str) -> CacheCommandResult:
        """Download and cache a model on disk without loading it into memory.

        Args:
            model_name: Name of the model to preload onto disk.

        Returns:
            A CacheCommandResult describing the preload outcome.
        """
        raise NotImplementedError("DiskModelCache.preload is a contract stub")

    def status(self, model_name: str) -> CacheCommandResult:
        """Report the disk cache status of a model.

        Args:
            model_name: Name of the model to inspect.

        Returns:
            A CacheCommandResult carrying the model's disk cache status.
        """
        raise NotImplementedError("DiskModelCache.status is a contract stub")

    def delete(self, model_name: str) -> CacheCommandResult:
        """Remove a model's artifacts from the disk cache.

        Args:
            model_name: Name of the model to delete from disk.

        Returns:
            A CacheCommandResult describing the deletion outcome.
        """
        raise NotImplementedError("DiskModelCache.delete is a contract stub")

    def list_models(self) -> CacheCommandResult:
        """List all models currently tracked in the disk cache.

        Returns:
            A CacheCommandResult enumerating the cached models.
        """
        raise NotImplementedError("DiskModelCache.list_models is a contract stub")

    def check_integrity(self, model_name: str) -> CacheCommandResult:
        """Verify the on-disk integrity of a cached model.

        Args:
            model_name: Name of the model to integrity-check.

        Returns:
            A CacheCommandResult describing the integrity verification outcome.
        """
        raise NotImplementedError("DiskModelCache.check_integrity is a contract stub")

    def update_metadata(
        self, model_name: str, metadata: Mapping[str, object]
    ) -> CacheCommandResult:
        """Update stored metadata for a cached model.

        Args:
            model_name: Name of the model whose metadata is updated.
            metadata: New metadata mapping to associate with the model.

        Returns:
            A CacheCommandResult describing the metadata update outcome.
        """
        raise NotImplementedError("DiskModelCache.update_metadata is a contract stub")


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
