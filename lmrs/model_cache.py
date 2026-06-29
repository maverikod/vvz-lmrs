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
