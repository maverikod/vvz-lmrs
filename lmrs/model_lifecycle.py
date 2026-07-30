"""Model memory lifecycle contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Protocol

from lmrs.model_cache import CacheState


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
        runtime_backend: Backend that hosts the resident model.
        model_probe: Asks the runtime whether it serves a model; without it no
            runtime verification happens and residency is recorded as asked.
        disk_cache: Disk cache consulted before a load, so a model absent from
            disk is refused with MODEL_NOT_CACHED instead of being reported
            resident. Skipped when the caller explicitly allows preload.
        vram_recorder: Called with the model name right after a load is proven,
            returning the measured VRAM facts of that load. Measurement itself
            lives outside this object: residency owns the state machine, not the
            driver.
    """

    current_residency: ModelResidency | None = None
    runtime_backend: str = "vllm"
    model_probe: Callable[[str], object] | None = None
    disk_cache: DiskCacheLike | None = None
    vram_recorder: Callable[[str], Mapping[str, Any]] | None = None

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
        if self.disk_cache is not None and not allow_preload:
            cache_status = self.disk_cache.status(model_name)
            disk_state = getattr(cache_status, "status", None)
            if disk_state != CacheState.CACHED_ON_DISK:
                return LifecycleCommandResult(
                    command="load_model",
                    model_name=model_name,
                    state=LifecycleState.NOT_LOADED,
                    success=False,
                    reason_code="MODEL_NOT_CACHED",
                    metadata={
                        "cache_status": disk_state,
                        "cache_reason_code": getattr(cache_status, "reason_code", None),
                        "hint": "call this command with allow_preload to fetch the model first",
                    },
                )
            probe_metadata["cache_status"] = disk_state
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
        measured_static_vram: int | None = None
        model_loaded_free_vram: int | None = None
        if self.vram_recorder is not None:
            facts = self.vram_recorder(model_name)
            static_value = facts.get("measured_model_static_vram_bytes")
            free_value = facts.get("model_loaded_free_vram_bytes")
            measured_static_vram = static_value if isinstance(static_value, int) else None
            model_loaded_free_vram = free_value if isinstance(free_value, int) else None
            probe_metadata["vram_facts"] = dict(facts)
        self.current_residency = ModelResidency(
            model_name=model_name,
            runtime_backend=self.runtime_backend,
            state=LifecycleState.LOADED,
            keep_loaded=True,
            measured_model_static_vram_bytes=measured_static_vram,
            model_loaded_free_vram_bytes=model_loaded_free_vram,
            loaded_at=datetime.now(UTC).isoformat(),
            metadata=probe_metadata,
        )
        return LifecycleCommandResult(
            command="load_model",
            model_name=model_name,
            state=LifecycleState.LOADED,
            success=True,
            measured_model_static_vram_bytes=measured_static_vram,
            model_loaded_free_vram_bytes=model_loaded_free_vram,
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


class DiskCacheLike(Protocol):
    """The disk-cache surface a model switch depends on."""

    def status(self, model_name: str) -> Any:
        """Report the disk cache status of a model."""

    def preload(self, model_name: str) -> Any:
        """Download and cache a model on disk without loading it."""


_SWITCHES_IN_PROGRESS: set[str] = set()


def model_switch_in_progress(model_name: str) -> bool:
    """Report whether a switch to the named model is currently running.

    Admission consults this so a model mid-switch is treated as not loaded and
    new requests are rejected with a stable reason code instead of reaching a
    runtime that is being torn down.

    Args:
        model_name: Name of the model to check.

    Returns:
        True while a ``switch_model`` call for that model has not finished.
    """
    return model_name in _SWITCHES_IN_PROGRESS


def _report(progress_callback: Callable[[str], None] | None, stage: str) -> None:
    """Send one progress stage to the caller's callback, if any.

    Args:
        progress_callback: Optional callable receiving the stage name.
        stage: Stage identifier, one of ``preloading``, ``unloading``, ``loading``.
    """
    if progress_callback is not None:
        progress_callback(stage)


def _switch_failure(model_name: str, reason_code: str, stage: str) -> dict[str, Any]:
    """Build the failure result of an aborted switch.

    Args:
        model_name: Name of the target model.
        reason_code: Stable machine-readable reason for the failure.
        stage: Stage the switch stopped at.

    Returns:
        A result dict with status ``failed`` and the stable reason code.
    """
    return {
        "status": LifecycleState.FAILED,
        "model_name": model_name,
        "reason_code": reason_code,
        "failed_stage": stage,
        "runtime_facts": {},
    }


def switch_model(
    target_model_name: str,
    progress_callback: Callable[[str], None] | None = None,
    *,
    lifecycle: ModelMemoryLifecycle | None = None,
    cache: DiskCacheLike | None = None,
) -> dict[str, Any]:
    """Switch the resident model to the target model in one operation.

    Runs, in order: disk-cache preload of the target when it is absent, unload
    of the currently resident model, then load of the target, which is where
    engine parameters, LMCache reconfiguration and VRAM re-measurement happen.
    Each stage is announced through ``progress_callback`` before it runs. On any
    stage failure the sequence stops and a stable reason code is returned rather
    than an unstructured exception, leaving residency consistent: the target is
    never left partially loaded.

    The lifecycle and cache are supplied by the caller on purpose, so a switch
    always acts on the same residency record the rest of the process uses
    instead of a second hidden one.

    Args:
        target_model_name: Name of the model to switch to.
        progress_callback: Optional callable receiving ``preloading``,
            ``unloading`` and ``loading`` as the switch proceeds.
        lifecycle: Owner of the residency state to act on.
        cache: Disk model cache used to preload an absent target.

    Returns:
        A result dict carrying ``status`` (``loaded_in_memory`` or ``failed``),
        ``model_name``, ``reason_code`` when it failed, and ``runtime_facts``
        recorded by the load.

    Raises:
        ValueError: If the lifecycle or cache collaborator is missing.
    """
    if lifecycle is None or cache is None:
        raise ValueError("switch_model requires the lifecycle and disk cache owned by the caller")

    _SWITCHES_IN_PROGRESS.add(target_model_name)
    try:
        try:
            cache_status = cache.status(target_model_name)
            if getattr(cache_status, "status", None) != CacheState.CACHED_ON_DISK:
                _report(progress_callback, "preloading")
                preload_result = cache.preload(target_model_name)
                if not getattr(preload_result, "success", False):
                    reason = getattr(preload_result, "reason_code", None) or "MODEL_CACHE_PRELOAD_FAILED"
                    return _switch_failure(target_model_name, reason, "preloading")

            resident = lifecycle.current_residency
            if resident is not None and resident.model_name != target_model_name:
                _report(progress_callback, "unloading")
                unload_result = lifecycle.unload_model(resident.model_name)
                if not unload_result.success:
                    reason = unload_result.reason_code or "MODEL_UNLOAD_FAILED"
                    return _switch_failure(target_model_name, reason, "unloading")

            _report(progress_callback, "loading")
            load_result = lifecycle.load_model(target_model_name, allow_preload=True)
            if not load_result.success:
                reason = load_result.reason_code or "MODEL_LOAD_FAILED"
                return _switch_failure(target_model_name, reason, "loading")
        except Exception as error:  # noqa: BLE001 - a switch reports, never propagates
            return _switch_failure(target_model_name, type(error).__name__, "loading")

        return {
            "status": CacheState.LOADED_IN_MEMORY,
            "model_name": target_model_name,
            "reason_code": load_result.reason_code,
            "runtime_facts": {
                "state": load_result.state,
                "measured_model_static_vram_bytes": load_result.measured_model_static_vram_bytes,
                "model_loaded_free_vram_bytes": load_result.model_loaded_free_vram_bytes,
                "metadata": dict(load_result.metadata),
            },
        }
    finally:
        _SWITCHES_IN_PROGRESS.discard(target_model_name)
