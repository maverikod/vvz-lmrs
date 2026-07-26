"""Model memory lifecycle contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable, Mapping


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
    runtime_backend: str = "vllm"
    model_probe: Callable[[str], object] | None = None

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
        if self.current_residency and self.current_residency.model_name == model_name:
            return LifecycleCommandResult(
                command="load_model",
                model_name=model_name,
                state=self.current_residency.state,
                success=True,
                reason_code="MODEL_ALREADY_LOADED",
                measured_model_static_vram_bytes=self.current_residency.measured_model_static_vram_bytes,
                model_loaded_free_vram_bytes=self.current_residency.model_loaded_free_vram_bytes,
                metadata=dict(self.current_residency.metadata),
            )
        if self.current_residency and not allow_preload:
            return LifecycleCommandResult(
                command="load_model",
                model_name=model_name,
                state=self.current_residency.state,
                success=False,
                reason_code="MODEL_ALREADY_LOADED",
                metadata={"resident_model": self.current_residency.model_name},
            )
        probe_metadata: dict[str, object] = {"allow_preload": allow_preload}
        if self.model_probe is not None:
            probe_result = self.model_probe(model_name)
            ok = bool(getattr(probe_result, "ok", False))
            served_models = tuple(getattr(probe_result, "served_models", ()) or ())
            probe_metadata.update({
                "runtime_probe": "vllm_openai_models",
                "served_models": list(served_models),
            })
            metadata = getattr(probe_result, "metadata", None)
            if isinstance(metadata, Mapping):
                probe_metadata.update(dict(metadata))
            if not ok:
                error = getattr(probe_result, "error", None)
                return LifecycleCommandResult(
                    command="load_model",
                    model_name=model_name,
                    state=LifecycleState.FAILED,
                    success=False,
                    reason_code="MODEL_NOT_SERVED_BY_VLLM",
                    metadata={**probe_metadata, "error": str(error or "model is not served by vLLM")},
                )
        self.current_residency = ModelResidency(
            model_name=model_name,
            runtime_backend=self.runtime_backend,
            state=LifecycleState.LOADED,
            keep_loaded=True,
            loaded_at=datetime.now(UTC).isoformat(),
            metadata=probe_metadata,
        )
        return LifecycleCommandResult(
            command="load_model",
            model_name=model_name,
            state=LifecycleState.LOADED,
            success=True,
            metadata=probe_metadata,
        )

    def unload_model(
        self, model_name: str
    ) -> LifecycleCommandResult:
        """Unload a model from GPU memory without deleting its disk cache.

        Args:
            model_name: Name of the model to unload.

        Returns:
            A LifecycleCommandResult describing the unload outcome.
        """
        if not self.current_residency or self.current_residency.model_name != model_name:
            return LifecycleCommandResult(
                command="unload_model",
                model_name=model_name,
                state=LifecycleState.NOT_LOADED,
                success=False,
                reason_code="MODEL_NOT_LOADED",
            )
        previous = self.current_residency
        if self.model_probe is not None:
            return LifecycleCommandResult(
                command="unload_model",
                model_name=model_name,
                state=previous.state,
                success=False,
                reason_code="VLLM_DYNAMIC_UNLOAD_UNSUPPORTED",
                measured_model_static_vram_bytes=previous.measured_model_static_vram_bytes,
                model_loaded_free_vram_bytes=previous.model_loaded_free_vram_bytes,
                metadata={"resident_model": previous.model_name, "runtime_backend": previous.runtime_backend},
            )
        self.current_residency = None
        return LifecycleCommandResult(
            command="unload_model",
            model_name=model_name,
            state=LifecycleState.NOT_LOADED,
            success=True,
            measured_model_static_vram_bytes=previous.measured_model_static_vram_bytes,
            model_loaded_free_vram_bytes=previous.model_loaded_free_vram_bytes,
        )

    def reload_model(
        self, model_name: str
    ) -> LifecycleCommandResult:
        """Reload a model into GPU memory.

        Args:
            model_name: Name of the model to reload.

        Returns:
            A LifecycleCommandResult describing the reload outcome.
        """
        if not self.current_residency or self.current_residency.model_name != model_name:
            return self.load_model(model_name, allow_preload=True)
        previous = self.current_residency
        self.current_residency = ModelResidency(
            model_name=model_name,
            runtime_backend=previous.runtime_backend,
            state=LifecycleState.LOADED,
            keep_loaded=previous.keep_loaded,
            measured_model_static_vram_bytes=previous.measured_model_static_vram_bytes,
            model_loaded_free_vram_bytes=previous.model_loaded_free_vram_bytes,
            loaded_at=previous.loaded_at,
            metadata=dict(previous.metadata),
        )
        return LifecycleCommandResult(
            command="reload_model",
            model_name=model_name,
            state=LifecycleState.LOADED,
            success=True,
            measured_model_static_vram_bytes=previous.measured_model_static_vram_bytes,
            model_loaded_free_vram_bytes=previous.model_loaded_free_vram_bytes,
            metadata={"reloaded": True},
        )

    def model_status(
        self, model_name: str
    ) -> LifecycleCommandResult:
        """Report the memory residency status of a model.

        Args:
            model_name: Name of the model to inspect.

        Returns:
            A LifecycleCommandResult carrying the model's residency state.
        """
        if not self.current_residency or self.current_residency.model_name != model_name:
            return LifecycleCommandResult(
                command="model_status",
                model_name=model_name,
                state=LifecycleState.NOT_LOADED,
                success=False,
                reason_code="MODEL_NOT_LOADED",
            )
        return LifecycleCommandResult(
            command="model_status",
            model_name=model_name,
            state=self.current_residency.state,
            success=True,
            measured_model_static_vram_bytes=self.current_residency.measured_model_static_vram_bytes,
            model_loaded_free_vram_bytes=self.current_residency.model_loaded_free_vram_bytes,
            metadata=dict(self.current_residency.metadata),
        )

    def model_lifecycle_status(self) -> LifecycleCommandResult:
        """Report the overall model memory lifecycle status.

        Returns:
            A LifecycleCommandResult describing current residency.
        """
        if self.current_residency is None:
            return LifecycleCommandResult(
                command="model_lifecycle_status",
                model_name="",
                state=LifecycleState.NOT_LOADED,
                success=True,
                metadata={"resident_model": None},
            )
        return LifecycleCommandResult(
            command="model_lifecycle_status",
            model_name=self.current_residency.model_name,
            state=self.current_residency.state,
            success=True,
            measured_model_static_vram_bytes=self.current_residency.measured_model_static_vram_bytes,
            model_loaded_free_vram_bytes=self.current_residency.model_loaded_free_vram_bytes,
            metadata={"resident_model": self.current_residency.model_name},
        )


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
