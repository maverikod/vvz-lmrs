"""Model memory lifecycle contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class LifecycleState:
    """In-memory model residency state constants.

    Represents memory-residency state only and stays separate from the disk
    cache state defined in lmrs.model_cache.

    Attributes:
        NOT_LOADED: Model is not resident in GPU memory.
        LOADING: Model is being loaded into GPU memory.
        LOADED: Model is resident in GPU memory.
        UNLOADING: Model is being unloaded from GPU memory.
        RELOADING: Model is being reloaded into GPU memory.
        FAILED: A load, unload, or reload operation failed.
        KEEP_LOADED: Policy marker keeping one model resident until replaced.
    """

    NOT_LOADED: str = "not_loaded"
    LOADING: str = "loading"
    LOADED: str = "loaded"
    UNLOADING: str = "unloading"
    RELOADING: str = "reloading"
    FAILED: str = "failed"
    KEEP_LOADED: str = "keep_loaded"


@dataclass(frozen=True)
class ModelResidency:
    """Measured in-memory residency facts for one resident model.

    Attributes:
        model_name: Name of the resident model.
        runtime_backend: Runtime backend hosting the resident model.
        state: Current lifecycle state from LifecycleState.
        keep_loaded: Whether the model is kept loaded until explicit replacement.
        measured_model_static_vram_bytes: Measured static VRAM the model occupies.
        model_loaded_free_vram_bytes: Measured free VRAM after the model loaded.
        loaded_at: ISO timestamp when the model became resident.
        metadata: Arbitrary metadata about this residency record.
    """

    model_name: str
    runtime_backend: str
    state: str
    keep_loaded: bool = True
    measured_model_static_vram_bytes: int | None = None
    model_loaded_free_vram_bytes: int | None = None
    loaded_at: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


def measure_model_static_vram(
    service_baseline_free_vram_bytes: int,
    model_loaded_free_vram_bytes: int,
) -> int:
    """Compute measured static VRAM occupied by a loaded model.

    Args:
        service_baseline_free_vram_bytes: Measured free VRAM after resident
            services start but before the model is loaded.
        model_loaded_free_vram_bytes: Measured free VRAM after the model loads.

    Returns:
        The measured static VRAM in bytes occupied by the loaded model.
    """
    if service_baseline_free_vram_bytes < 0:
        raise ValueError("service_baseline_free_vram_bytes must be non-negative")
    if model_loaded_free_vram_bytes < 0:
        raise ValueError("model_loaded_free_vram_bytes must be non-negative")
    measured = service_baseline_free_vram_bytes - model_loaded_free_vram_bytes
    if measured < 0:
        raise ValueError("measured model static VRAM must not be negative")
    return measured


@dataclass
class ModelMemoryLifecycle:
    """Owner of model memory residency and the lifecycle command surface.

    Exposes load, unload, reload, and status commands as contract stubs. Before
    loading, a command must verify disk cache presence and runtime
    compatibility or return a MODEL_NOT_CACHED reason unless preload is
    explicitly allowed. Unloading frees memory residency without deleting the
    disk cache. The MVP policy keeps one resident model loaded until explicit
    unload or replacement.

    Attributes:
        current_residency: The currently resident model, if any.
    """

    current_residency: ModelResidency | None = None

    def load_model(
        self, model_name: str, allow_preload: bool = False
    ) -> LifecycleCommandResult:
        """Load a cached model into GPU memory.

        Args:
            model_name: Name of the model to load.
            allow_preload: Whether to allow preloading when not yet cached.

        Returns:
            A LifecycleCommandResult describing the load outcome.
        """
        msg = "ModelMemoryLifecycle.load_model is a contract stub"
        raise NotImplementedError(msg)

    def unload_model(
        self, model_name: str
    ) -> LifecycleCommandResult:
        """Unload a model from GPU memory without deleting its disk cache.

        Args:
            model_name: Name of the model to unload.

        Returns:
            A LifecycleCommandResult describing the unload outcome.
        """
        msg = "ModelMemoryLifecycle.unload_model is a contract stub"
        raise NotImplementedError(msg)

    def reload_model(
        self, model_name: str
    ) -> LifecycleCommandResult:
        """Reload a model into GPU memory.

        Args:
            model_name: Name of the model to reload.

        Returns:
            A LifecycleCommandResult describing the reload outcome.
        """
        msg = "ModelMemoryLifecycle.reload_model is a contract stub"
        raise NotImplementedError(msg)

    def model_status(
        self, model_name: str
    ) -> LifecycleCommandResult:
        """Report the memory residency status of a model.

        Args:
            model_name: Name of the model to inspect.

        Returns:
            A LifecycleCommandResult carrying the model's residency state.
        """
        msg = "ModelMemoryLifecycle.model_status is a contract stub"
        raise NotImplementedError(msg)

    def model_lifecycle_status(self) -> LifecycleCommandResult:
        """Report the overall model memory lifecycle status.

        Returns:
            A LifecycleCommandResult describing current residency.
        """
        msg = "ModelMemoryLifecycle.model_lifecycle_status is a contract stub"
        raise NotImplementedError(msg)


@dataclass(frozen=True)
class LifecycleCommandResult:
    """Structured result of a model memory lifecycle command.

    Attributes:
        command: Name of the lifecycle command that produced this result.
        model_name: Name of the model the command acted on.
        state: Resulting lifecycle state from LifecycleState.
        success: Whether the command completed successfully.
        reason_code: Stable machine-readable reason for the outcome.
        measured_model_static_vram_bytes: Measured static VRAM fact after a
            successful transition, if available.
        model_loaded_free_vram_bytes: Measured free VRAM after the transition,
            if available.
        metadata: Arbitrary metadata about the command execution.
    """

    command: str
    model_name: str
    state: str
    success: bool
    reason_code: str | None = None
    measured_model_static_vram_bytes: int | None = None
    model_loaded_free_vram_bytes: int | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
