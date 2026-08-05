"""Adapter command registration and thin command wrappers for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""
from __future__ import annotations  # noqa: I001 (project-wide false positive)

from collections.abc import Callable, Iterable, Mapping, MutableSequence
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from typing import Any, ClassVar, cast
from uuid import uuid4

from lmrs.admission import CapacitySnapshot
from lmrs.commands import CanonicalChatHandler, CommandName, ErrorCode
from lmrs.configuration import KVCacheProfile, derive_kv_cache_profile
from lmrs.estimation import TokenBreakdown, calculate_required_tokens
from lmrs.lmcache import LMCacheStoragePolicy, get_lmcache_status, observations_from_vllm_metrics, purge_lmcache
from lmrs.model_cache import DiskModelCache
from lmrs.model_lifecycle import ModelMemoryLifecycle, model_switch_in_progress, switch_model
from lmrs.queue import RequestQueue
from lmrs.runtime_client import RuntimeBackend, RuntimeClient, VLLMOpenAIClient
from lmrs.vram import (
    DynamicVramState,
    VramFactsStore,
    calculate_max_dynamic_pool,
    calculate_usable_dynamic_vram,
    measure_gpu_memory,
)
from mcp_proxy_adapter.commands.base import Command, CommandResult
from mcp_proxy_adapter.commands.hooks import (
    register_custom_commands_hook as _adapter_register_custom_commands_hook,
)
from mcp_proxy_adapter.commands.result import ErrorResult, SuccessResult


class ThinAdapterCommand(Command):
    """Base contract for adapter-facing LMRS command wrappers.

    Concrete subclasses expose a stable ``name``, may set ``use_queue`` for
    long-running work, validate adapter parameters, delegate to a domain
    executor or service, and return an adapter success or error result. This
    class owns only adapter translation; admission, tokenizer, VRAM, and model
    lifecycle decisions remain in LMRS domain services.
    """

    name: ClassVar[str] = ""
    descr: ClassVar[str] = ""
    use_queue: ClassVar[bool] = False
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "additionalProperties": True,
    }
    executor: ClassVar[staticmethod[[Mapping[str, Any]], Any] | None] = None

    @classmethod
    def get_schema(cls) -> dict[str, Any]:
        """Return adapter-visible JSON schema for command parameters."""
        return deepcopy(cls.schema)

    @classmethod
    def metadata(cls) -> dict[str, Any]:
        """Return full help metadata in the fleet documentation paradigm.

        The shape mirrors planmgr and the code-analysis server: summary,
        detailed description, per-parameter docs, structured return value,
        usage examples, error cases keyed by stable reason codes, and best
        practices. The content lives in ``lmrs.adapter.command_docs`` so the
        documentation has one source.
        """
        from lmrs.adapter.command_docs import DOC_AUTHOR, DOC_EMAIL, DOC_VERSION, command_documentation

        documentation = dict(command_documentation(cls.name))
        return {
            "name": cls.name,
            "summary": cls.descr,
            "type": "custom",
            "version": DOC_VERSION,
            "author": DOC_AUTHOR,
            "email": DOC_EMAIL,
            "category": documentation.get("category", "uncategorized"),
            "detailed_description": documentation.get("detailed_description", cls.descr),
            "parameters": documentation.get("parameters", {}),
            "return_value": documentation.get("return_value", {}),
            "usage_examples": documentation.get("usage_examples", []),
            "error_cases": documentation.get("error_cases", {}),
            "best_practices": documentation.get("best_practices", []),
        }

    def validate(self, params: Mapping[str, Any]) -> None:
        """Validate adapter request parameters before delegation."""
        if not isinstance(params, Mapping):
            raise TypeError("adapter command parameters must be a mapping")

    def delegate(self, params: Mapping[str, Any]) -> Any:
        """Call the configured domain executor or service."""
        executor = self.executor
        if executor is None:
            raise RuntimeError(f"{type(self).__name__} has no domain executor")
        return executor(params)

    def _result_payload(self, payload: Any) -> dict[str, Any]:
        """Normalize domain return values into adapter result data."""
        if is_dataclass(payload) and not isinstance(payload, type):
            payload = asdict(payload)
        elif hasattr(payload, "to_dict") and callable(payload.to_dict):
            payload = payload.to_dict()
        elif isinstance(payload, Mapping):
            payload = dict(payload)
        return {"command": self.name, "payload": payload}

    def success_result(self, payload: Any) -> CommandResult:
        """Return a native adapter success result for a successful delegate call."""
        result = SuccessResult(data=self._result_payload(payload))
        return cast(CommandResult, result)

    def error_result(self, code: str, message: str) -> CommandResult:
        """Return a native adapter error result for a failed delegate call."""
        details = {"code": code, "command": self.name}
        result = ErrorResult(message=message, details=details)
        return cast(CommandResult, result)

    async def execute(self, **kwargs: Any) -> CommandResult:
        """Validate, delegate to the domain service, and return a result."""
        params: Mapping[str, Any] = kwargs
        try:
            self.validate(params)
            return self.success_result(self.delegate(params))
        except Exception as exc:  # noqa: BLE001
            return self.error_result(type(exc).__name__, str(exc))


def _model_cache_root() -> str:
    """Return the directory the disk model cache owns.

    The runtime downloads weights into the hub cache named by its own
    environment, so that directory is preferred over any default: a cache
    pointing somewhere else would report models the runtime cannot load and miss
    the ones it can.

    Returns:
        The configured cache root.
    """
    for variable in ("LMRS_MODEL_CACHE_ROOT", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE"):
        configured = os.environ.get(variable)
        if configured:
            return configured
    return "/var/lmrs/hf-cache"


def _int_env(name: str, default: int = 0) -> int:
    """Return a non-negative integer setting from the environment.

    Args:
        name: Environment variable to read.
        default: Value used when it is unset or unusable.

    Returns:
        The configured value, or the default.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


_CACHE = DiskModelCache(cache_root=_model_cache_root())
_VLLM_CLIENT = VLLMOpenAIClient(
    base_url=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000"),
    timeout_seconds=float(_int_env("LMRS_RUNTIME_TIMEOUT_SECONDS", 180)),
)
_VRAM_STORE = VramFactsStore(path=os.environ.get("LMRS_VRAM_FACTS_PATH", "/var/lmrs/vram-facts.json"))


def record_model_load_measurement(model_name: str) -> Mapping[str, Any]:
    """Measure and persist the VRAM facts of a model that just became resident.

    Args:
        model_name: The model the runtime reports as served.

    Returns:
        The stored VRAM facts, empty when the GPU could not be read.
    """
    measurement = measure_gpu_memory()
    if not measurement.ok:
        return {"measurement_error": measurement.error}
    record = _CACHE.status(model_name).record
    return _VRAM_STORE.record_model_loaded(
        model_name,
        measurement,
        runtime_backend=RuntimeBackend.VLLM,
        quantization_profile=record.quantization_profile if record is not None else "",
    )


_LIFECYCLE = ModelMemoryLifecycle(
    runtime_backend=RuntimeBackend.VLLM,
    model_probe=_VLLM_CLIENT.is_model_served,
    disk_cache=_CACHE,
    vram_recorder=record_model_load_measurement,
)
_RUNTIME_CLIENT = RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=_VLLM_CLIENT)
_LMCACHE_POLICY = LMCacheStoragePolicy(
    enabled=os.environ.get("LMRS_LMCACHE_ENABLED", "").lower() in {"1", "true", "yes"},
    cache_storage_path=os.environ.get("LMRS_LMCACHE_PATH", "/var/lmrs/lmcache"),
)


# The switch predicate is wired in on purpose: without it a request arriving
# mid-switch would be admitted against a runtime that is being torn down.
_CHAT_HANDLER = CanonicalChatHandler(model_switch_in_progress=model_switch_in_progress)


class _QueueHolder:
    """Owns the single mutable reference to the process request queue.

    RequestQueue is a persistent structure: add and cancel return a new queue
    rather than mutating in place. The holder keeps one reference so the queue
    a command reports is the queue another command mutated, instead of each
    command carrying its own copy.

    Attributes:
        queue: The current immutable RequestQueue value.
    """

    def __init__(self) -> None:
        """Start with an empty queue."""
        self.queue = RequestQueue()

    def snapshot(self) -> list[dict[str, object]]:
        """Return the serializable state of the current queue.

        Returns:
            One dict per queued entry.
        """
        return self.queue.snapshot()

    def replace(self, queue: RequestQueue) -> None:
        """Adopt the queue value an admission produced.

        Args:
            queue: The queue returned by adding an admitted entry.
        """
        self.queue = queue

    def reserved_bytes(self) -> int:
        """Return the dynamic VRAM currently held by queued requests.

        Returns:
            The sum of the reservations of every queued entry.
        """
        return sum(entry.required_dynamic_vram_bytes for entry in self.queue.entries)

    def cancel(self, request_id: str) -> list[dict[str, object]]:
        """Remove one request and return the resulting queue state.

        Args:
            request_id: Identifier of the request to cancel.

        Returns:
            The queue snapshot the cancellation produced.
        """
        self.queue = self.queue.cancel(request_id)
        return self.queue.snapshot()


_QUEUE = _QueueHolder()


def _resident_services() -> tuple[str, ...]:
    """Return the always-on GPU services declared for this host.

    Returns:
        Service names from ``LMRS_RESIDENT_SERVICES``, empty when unset.
    """
    raw = os.environ.get("LMRS_RESIDENT_SERVICES", "")
    return tuple(name.strip() for name in raw.split(",") if name.strip())


def vram_payload() -> dict[str, Any]:
    """Return the measured VRAM facts and the derived capacity pools.

    Every figure is a measurement or is derived from one: the model-loaded free
    VRAM is read from the driver now, the service baseline is the reading taken
    before a model was loaded, and the static VRAM is their difference. When a
    figure has no measurement behind it the key is null and ``measured`` is
    false, because a plausible number here would silently become an admission
    decision.

    Returns:
        The capacity payload the ``capacity`` command reports.
    """
    measurement = measure_gpu_memory()
    stored = _VRAM_STORE.read()
    baseline = stored.get("service_baseline_free_vram_bytes")
    baseline_bytes = baseline if isinstance(baseline, int) else None
    if measurement.ok:
        free_bytes: int | None = measurement.free_bytes
    else:
        recorded_free = stored.get("model_loaded_free_vram_bytes")
        free_bytes = recorded_free if isinstance(recorded_free, int) else None
    if measurement.ok and baseline_bytes is None and _LIFECYCLE.current_residency is None:
        # The baseline is only observable while no model holds VRAM, so the
        # runtime is asked as well: a reading taken while vLLM already serves
        # weights would make the model look free.
        probe = _VLLM_CLIENT.list_models()
        model_served = bool(probe.ok and probe.served_models)
        stored = _VRAM_STORE.record_service_baseline(measurement, _resident_services(), model_served=model_served)
        baseline = stored.get("service_baseline_free_vram_bytes")
        baseline_bytes = baseline if isinstance(baseline, int) else None
    static_vram: int | None = None
    if stored.get("baseline_model_served"):
        baseline_bytes = None
    if baseline_bytes is not None and free_bytes is not None and baseline_bytes >= free_bytes:
        static_vram = baseline_bytes - free_bytes
    state = DynamicVramState(
        model_loaded_free_vram_bytes=free_bytes or 0,
        safety_margin_bytes=_int_env("LMRS_SAFETY_MARGIN_BYTES"),
        runtime_reserve_bytes=_int_env("LMRS_RUNTIME_RESERVE_BYTES"),
        active_reservation_bytes=_QUEUE.reserved_bytes(),
    )
    residency = _LIFECYCLE.current_residency
    return {
        "resident_services": list(_resident_services()),
        "service_baseline_free_vram_bytes": baseline_bytes,
        "model_loaded_free_vram_bytes": free_bytes,
        "measured_model_static_vram_bytes": static_vram,
        "max_dynamic_pool_bytes": calculate_max_dynamic_pool(state) if free_bytes is not None else 0,
        "usable_dynamic_vram_bytes": calculate_usable_dynamic_vram(state) if free_bytes is not None else 0,
        "active_reservation_bytes": state.active_reservation_bytes,
        "total_vram_bytes": measurement.total_bytes if measurement.ok else stored.get("total_vram_bytes"),
        "model_name": residency.model_name if residency is not None else stored.get("model_name"),
        "runtime_backend": RuntimeBackend.VLLM,
        "quantization_profile": stored.get("quantization_profile"),
        "hardware_profile_id": os.environ.get("LMRS_HARDWARE_PROFILE_ID"),
        "measurement_metadata": {
            "source": measurement.source,
            "measured_at": measurement.measured_at,
            "devices": [dict(device) for device in measurement.devices],
            "error": measurement.error,
            "baseline_measured_at": stored.get("baseline_measured_at"),
            "baseline_model_served": stored.get("baseline_model_served"),
            "static_vram_unavailable_reason": stored.get("static_vram_unavailable_reason"),
        },
        "measured": measurement.ok,
        "safety_margin_bytes": state.safety_margin_bytes,
        "runtime_reserve_bytes": state.runtime_reserve_bytes,
    }


def capacity_snapshot(model_name: str) -> CapacitySnapshot:
    """Return the capacity snapshot admission decides against.

    Args:
        model_name: Model the request targets.

    Returns:
        A CapacitySnapshot carrying the measured pools and the runtime state of
        that model. An unmeasurable GPU yields zeroed pools, which rejects
        instead of admitting: refusing a request LMRS cannot size is the
        invariant, not a degraded mode.
    """
    payload = vram_payload()
    residency = _LIFECYCLE.current_residency
    probe = _VLLM_CLIENT.is_model_served(model_name)
    return CapacitySnapshot(
        usable_dynamic_vram_bytes=int(payload["usable_dynamic_vram_bytes"]),
        max_dynamic_pool_bytes=int(payload["max_dynamic_pool_bytes"]),
        model_loaded=bool(probe.ok) or (residency is not None and residency.model_name == model_name),
        runtime_ready=bool(probe.ok),
        metadata={
            "measured": payload["measured"],
            "served_models": list(probe.served_models),
            "probe_error": probe.error,
        },
    )


def model_kv_profile(model_name: str) -> KVCacheProfile | None:
    """Return the KV-cache profile of a cached model.

    The parameters come from the ``config.json`` inside the cached snapshot the
    runtime loads, so the KV cost belongs to that exact model revision.

    Args:
        model_name: Model to describe.

    Returns:
        The derived profile, or None when the model is not cached or its config
        does not state the parameters the formula needs.
    """
    record = _CACHE.status(model_name).record
    if record is None or not record.model_path:
        return None
    config_path = Path(record.model_path) / "config.json"
    try:
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    return derive_kv_cache_profile(loaded, os.environ.get("LMRS_KV_CACHE_DTYPE"))


def prompt_token_breakdown(
    model_name: str,
    messages: list[dict[str, str]],
    reserved_output_tokens: int,
) -> TokenBreakdown:
    """Count the tokens of a chat prompt for admission.

    The runtime's own tokenizer is asked first, because it is the tokenizer that
    will execute the request; the character heuristic is used only when the
    runtime does not answer, and the result is then marked as a rough estimate
    so no caller mistakes it for an exact count.

    Args:
        model_name: Model whose tokenizer applies.
        messages: The chat messages to be sent.
        reserved_output_tokens: Output tokens reserved for the reply.

    Returns:
        A TokenBreakdown for this request.
    """
    counted = _VLLM_CLIENT.count_prompt_tokens(model_name, messages)
    if counted is not None:
        return TokenBreakdown(
            input_tokens=counted,
            tool_tokens=0,
            service_tokens=0,
            reserved_output_tokens=reserved_output_tokens,
            tokenizer_name=model_name,
            tokenizer_accuracy="runtime_tokenizer",
            rough_estimate=False,
        )
    characters = sum(len(str(message.get("content", ""))) for message in messages)
    return TokenBreakdown(
        input_tokens=-(-characters // 4),
        tool_tokens=0,
        service_tokens=0,
        reserved_output_tokens=reserved_output_tokens,
        tokenizer_name="characters-per-four",
        tokenizer_accuracy="rough",
        rough_estimate=True,
    )


def _lmcache_observations() -> Mapping[str, Any]:
    """Return raw LMCache runtime observations for the status command.

    Two independent sources are read and merged. The disk tier is measured
    directly from the storage path. The hit and miss counters come from the
    runtime's own metrics endpoint, where the external KV-connector lookup
    accounting is published in tokens. A source that does not answer leaves its
    figures absent and is named in the metadata, rather than contributing
    zeros that would read as measurements.

    Returns:
        A mapping of raw observations consumed by ``build_lmcache_telemetry``.
    """
    observations: dict[str, Any] = {}
    metadata: dict[str, Any] = {}

    storage_path = _LMCACHE_POLICY.cache_storage_path
    if not storage_path:
        metadata.update({"storage_path": None, "disk_tier_observed": False})
    else:
        root = Path(storage_path)
        if not root.is_dir():
            metadata.update({"storage_path": str(root), "disk_tier_observed": False, "reason": "storage path does not exist"})
        else:
            usage = 0
            entries = 0
            for path in root.rglob("*"):
                try:
                    if path.is_file():
                        usage += path.stat().st_size
                        entries += 1
                except OSError:
                    continue
            observations["disk_cache_usage"] = usage
            metadata.update({
                "storage_path": str(root),
                "disk_tier_observed": True,
                "entry_count": entries,
                "disk_tier_source": "filesystem",
            })

    metrics_text = _VLLM_CLIENT.fetch_metrics()
    if metrics_text is None:
        metadata["runtime_counters_available"] = False
        metadata["runtime_counters_reason"] = "the runtime metrics endpoint did not answer"
    else:
        runtime_observations = observations_from_vllm_metrics(metrics_text)
        for key in ("hit_tokens", "miss_tokens"):
            if key in runtime_observations:
                observations[key] = runtime_observations[key]
        metadata["runtime_counters_available"] = "hit_tokens" in runtime_observations
        runtime_metadata = runtime_observations.get("metadata")
        if isinstance(runtime_metadata, Mapping):
            metadata.update(runtime_metadata)

    observations["metadata"] = metadata
    return observations


def _param(params: Mapping[str, Any], name: str) -> str:
    value = params.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


MODEL_NAME_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model_name": {"type": "string", "minLength": 1},
    },
    "required": ["model_name"],
    "additionalProperties": True,
}

MODEL_LOAD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "model_name": {"type": "string", "minLength": 1},
        "allow_preload": {"type": "boolean", "default": False},
    },
    "required": ["model_name"],
    "additionalProperties": True,
}

CHAT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "minLength": 1},
        "model_name": {"type": "string", "minLength": 1},
        "system": {"type": "string"},
        "temperature": {"type": "number", "default": 0},
        "max_tokens": {"type": "integer", "default": 128},
        "request_id": {"type": "string", "minLength": 1},
        "session_id": {"type": "string", "minLength": 1},
    },
    "required": ["message", "model_name"],
    "additionalProperties": True,
}


class HealthcheckCommand(ThinAdapterCommand):
    """Adapter health command for the LMRS command surface."""

    name: ClassVar[str] = CommandName.HEALTHCHECK
    descr: ClassVar[str] = "LMRS adapter healthcheck"

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return {"status": "ok", "service": "lmrs", "params": dict(params)}


class LocalModelCachePreloadCommand(ThinAdapterCommand):
    """Adapter wrapper for disk-cache preload."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_CACHE_PRELOAD
    descr: ClassVar[str] = "Prepare a model in the local disk cache"
    # Queued: a preload downloads model weights, which outlives any request
    # timeout. Running it inline made the command a coin flip on model size.
    use_queue: ClassVar[bool] = True
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _CACHE.preload(_param(params, "model_name"))


class LocalModelCacheStatusCommand(ThinAdapterCommand):
    """Adapter wrapper for disk-cache status."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_CACHE_STATUS
    descr: ClassVar[str] = "Report local disk-cache status for a model"
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _CACHE.status(_param(params, "model_name"))


class LocalModelCacheDeleteCommand(ThinAdapterCommand):
    """Adapter wrapper for disk-cache removal from the tracked cache."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_CACHE_DELETE
    descr: ClassVar[str] = "Remove a model from the tracked local disk cache"
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _CACHE.delete(_param(params, "model_name"))


class ChatCommand(ThinAdapterCommand):
    """Adapter wrapper for an admitted chat completion.

    The request is counted, sized against measured capacity and admitted before
    anything reaches the runtime. That order is the service's reason to exist:
    a prompt that does not fit must be refused with a stable reason code rather
    than handed to vLLM to fail there, so this command never calls the runtime
    itself and never bypasses the canonical handler.
    """

    name: ClassVar[str] = CommandName.CHAT
    descr: ClassVar[str] = "Admit a chat request against measured capacity and run it on the local model"
    schema: ClassVar[dict[str, Any]] = CHAT_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        message = _param(params, "message")
        model_name = _param(params, "model_name")
        messages: list[dict[str, str]] = []
        system = params.get("system")
        if isinstance(system, str) and system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})
        max_tokens = int(params.get("max_tokens", 128))
        kv_profile = model_kv_profile(model_name)
        if kv_profile is None:
            return _CHAT_HANDLER.executor.failure_result(
                CommandName.CHAT,
                ErrorCode.HARDWARE_CAPACITY_UNKNOWN,
                metadata={
                    "model_name": model_name,
                    "detail": "the model is not cached on disk or its config does not state the KV parameters, so the request cannot be sized",
                },
            )
        request_id = str(params.get("request_id") or f"chat-{uuid4().hex}")
        admitted_at = datetime.now(UTC)
        options: dict[str, object] = {"max_tokens": max_tokens}
        if "temperature" in params:
            options["temperature"] = params["temperature"]
        return _CHAT_HANDLER.chat(
            queue=_QUEUE.queue,
            request_metadata={"session_id": str(params.get("session_id") or "adapter")},
            queue_metadata={
                "admitted_at": admitted_at.isoformat(),
                "expires_at": (admitted_at + timedelta(seconds=_int_env("LMRS_QUEUE_TTL_SECONDS", 300))).isoformat(),
            },
            runtime_client=_RUNTIME_CLIENT,
            runtime_profile=None,
            admitted_request={"request_id": request_id, "model_name": model_name, "messages": messages, "options": options},
            queue_sink=_QUEUE.replace,
            request_id=request_id,
            model_name=model_name,
            token_breakdown=prompt_token_breakdown(model_name, messages, max_tokens),
            declared_context_window=kv_profile.declared_context_window,
            capacity=capacity_snapshot(model_name),
            kv_bytes_per_token=kv_profile.kv_bytes_per_token(),
            per_request_overhead_bytes=_int_env("LMRS_PER_REQUEST_OVERHEAD_BYTES"),
            runtime_batch_overhead_bytes=_int_env("LMRS_RUNTIME_BATCH_OVERHEAD_BYTES"),
        )


class LocalModelLoadCommand(ThinAdapterCommand):
    """Adapter wrapper for model memory load."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_LOAD
    descr: ClassVar[str] = "Verify that vLLM is serving a model and mark it resident"
    schema: ClassVar[dict[str, Any]] = MODEL_LOAD_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _LIFECYCLE.load_model(
            _param(params, "model_name"),
            allow_preload=bool(params.get("allow_preload", False)),
        )


class LocalModelUnloadCommand(ThinAdapterCommand):
    """Adapter wrapper for model memory unload."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_UNLOAD
    descr: ClassVar[str] = "Request model unload from LMRS lifecycle state"
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _LIFECYCLE.unload_model(_param(params, "model_name"))


# Two modes share one schema: text mode (message [+ system, model_name]) counts
# with the runtime tokenizer; numeric mode sums caller-declared components.
# Required is empty because either mode alone is a complete call; the delegate
# enforces one complete mode with an explicit error.
TOKEN_COUNT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "minLength": 1},
        "system": {"type": "string"},
        "model_name": {"type": "string", "minLength": 1},
        "input_tokens": {"type": "integer", "minimum": 0},
        "tool_tokens": {"type": "integer", "minimum": 0, "default": 0},
        "service_tokens": {"type": "integer", "minimum": 0, "default": 0},
        "reserved_output_tokens": {"type": "integer", "minimum": 0, "default": 0},
        "tokenizer_name": {"type": "string", "minLength": 1},
        "tokenizer_accuracy": {"type": "string", "minLength": 1},
        "rough_estimate": {"type": "boolean", "default": False},
    },
    "required": [],
    "additionalProperties": True,
}


def _resolve_target_model(params: Mapping[str, Any]) -> str | None:
    """Return the model a text-mode request targets.

    The explicit parameter wins; otherwise the recorded residency, and as the
    last resort the runtime's own serving list, because on this deployment the
    runtime serves exactly one model.

    Args:
        params: Adapter command parameters.

    Returns:
        The model name, or None when nothing names one.
    """
    explicit = params.get("model_name")
    if isinstance(explicit, str) and explicit:
        return explicit
    residency = _LIFECYCLE.current_residency
    if residency is not None:
        return residency.model_name
    probe = _VLLM_CLIENT.list_models()
    if probe.ok and probe.served_models:
        return probe.served_models[0]
    return None


def _chat_messages_from_params(params: Mapping[str, Any]) -> list[dict[str, str]]:
    """Build the OpenAI-form messages of a text-mode request.

    Args:
        params: Adapter command parameters carrying message and optional system.

    Returns:
        The role-tagged message list.
    """
    messages: list[dict[str, str]] = []
    system = params.get("system")
    if isinstance(system, str) and system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": str(params["message"])})
    return messages


CANCEL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"request_id": {"type": "string", "minLength": 1}},
    "required": ["request_id"],
    "additionalProperties": True,
}

# Two modes share one schema: text mode (message + model_name [+ system,
# max_tokens]) lets the server count, size and measure everything itself; raw
# mode supplies every admission input explicitly. Required is empty because
# either mode alone is complete; the delegate enforces one complete mode.
ESTIMATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {"type": "string", "minLength": 1},
        "system": {"type": "string"},
        "max_tokens": {"type": "integer", "minimum": 0, "default": 128},
        "request_id": {"type": "string", "minLength": 1},
        "model_name": {"type": "string", "minLength": 1},
        "token_breakdown": {"type": "object"},
        "declared_context_window": {"type": "integer", "minimum": 1},
        "capacity": {"type": "object"},
        "kv_bytes_per_token": {"type": "integer", "minimum": 0},
        "per_request_overhead_bytes": {"type": "integer", "minimum": 0},
        "runtime_batch_overhead_bytes": {"type": "integer", "minimum": 0},
    },
    "required": [],
    "additionalProperties": True,
}

_ESTIMATE_RAW_FIELDS: tuple[str, ...] = (
    "request_id",
    "model_name",
    "token_breakdown",
    "declared_context_window",
    "capacity",
    "kv_bytes_per_token",
    "per_request_overhead_bytes",
    "runtime_batch_overhead_bytes",
)


class ModelStatusCommand(ThinAdapterCommand):
    """Adapter wrapper for read-only model residency status."""

    name: ClassVar[str] = CommandName.MODEL_STATUS
    descr: ClassVar[str] = "Report the memory residency status of a model"
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _LIFECYCLE.model_status(_param(params, "model_name"))


class CapacityCommand(ThinAdapterCommand):
    """Adapter wrapper for the read-only VRAM capacity snapshot."""

    name: ClassVar[str] = CommandName.CAPACITY
    descr: ClassVar[str] = "Report measured VRAM facts and the usable dynamic pool"

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return vram_payload()


class QueueStatusCommand(ThinAdapterCommand):
    """Adapter wrapper for the read-only request queue state."""

    name: ClassVar[str] = CommandName.QUEUE_STATUS
    descr: ClassVar[str] = "Report the current request queue state"

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return {"entries": _QUEUE.snapshot()}


class TokenCountCommand(ThinAdapterCommand):
    """Adapter wrapper for tokenizer-aware request accounting."""

    name: ClassVar[str] = CommandName.TOKEN_COUNT
    descr: ClassVar[str] = "Count a prompt with the runtime tokenizer, or sum caller-declared token components"
    schema: ClassVar[dict[str, Any]] = TOKEN_COUNT_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        if isinstance(params.get("message"), str) and params["message"]:
            model_name = _resolve_target_model(params)
            breakdown = prompt_token_breakdown(
                model_name or "",
                _chat_messages_from_params(params),
                int(params.get("reserved_output_tokens", 0)),
            )
        elif params.get("input_tokens") is not None:
            breakdown = TokenBreakdown(
                input_tokens=int(params["input_tokens"]),
                tool_tokens=int(params.get("tool_tokens", 0)),
                service_tokens=int(params.get("service_tokens", 0)),
                reserved_output_tokens=int(params.get("reserved_output_tokens", 0)),
                tokenizer_name=_param(params, "tokenizer_name"),
                tokenizer_accuracy=_param(params, "tokenizer_accuracy"),
                rough_estimate=bool(params.get("rough_estimate", False)),
            )
        else:
            raise ValueError(
                "token_count requires either message (text mode) or "
                "input_tokens with tokenizer_name and tokenizer_accuracy (numeric mode)"
            )
        return {
            "token_breakdown": asdict(breakdown),
            "required_tokens": calculate_required_tokens(breakdown),
        }


class CancelCommand(ThinAdapterCommand):
    """Adapter wrapper for cancelling one queued request."""

    name: ClassVar[str] = CommandName.CANCEL
    descr: ClassVar[str] = "Cancel a queued request and report the resulting queue state"
    schema: ClassVar[dict[str, Any]] = CANCEL_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        request_id = _param(params, "request_id")
        return {"request_id": request_id, "entries": _QUEUE.cancel(request_id)}


class EstimateCommand(ThinAdapterCommand):
    """Adapter wrapper for the dry-run estimate and admission path."""

    name: ClassVar[str] = CommandName.ESTIMATE
    descr: ClassVar[str] = "Report whether a request would execute, queue or be rejected"
    schema: ClassVar[dict[str, Any]] = ESTIMATE_SCHEMA

    @staticmethod
    def _coerce_token_breakdown(value: Any) -> Any:
        """Return the token breakdown as the domain type.

        Args:
            value: A TokenBreakdown or the JSON mapping a remote client sends.

        Returns:
            A TokenBreakdown when the value is a mapping, the value otherwise.
        """
        if not isinstance(value, Mapping):
            return value
        return TokenBreakdown(
            input_tokens=int(value.get("input_tokens", 0)),
            tool_tokens=int(value.get("tool_tokens", 0)),
            service_tokens=int(value.get("service_tokens", 0)),
            reserved_output_tokens=int(value.get("reserved_output_tokens", 0)),
            tokenizer_name=str(value.get("tokenizer_name", "caller_declared")),
            tokenizer_accuracy=str(value.get("tokenizer_accuracy", "caller_declared")),
            rough_estimate=bool(value.get("rough_estimate", True)),
        )

    @staticmethod
    def _coerce_capacity(value: Any) -> Any:
        """Return the capacity input as the domain snapshot type.

        Args:
            value: A CapacitySnapshot or the JSON mapping a remote client sends.

        Returns:
            A CapacitySnapshot when the value is a mapping, the value otherwise.
        """
        if not isinstance(value, Mapping):
            return value
        usable = int(value.get("usable_dynamic_vram_bytes", 0))
        return CapacitySnapshot(
            usable_dynamic_vram_bytes=usable,
            max_dynamic_pool_bytes=int(value.get("max_dynamic_pool_bytes", usable)),
            model_loaded=bool(value.get("model_loaded", False)),
            runtime_ready=bool(value.get("runtime_ready", False)),
            metadata=dict(value.get("metadata", {})) if isinstance(value.get("metadata", {}), Mapping) else {},
        )

    def _text_mode_estimate(self, params: Mapping[str, Any]) -> Any:
        """Run the dry-run admission of a real prompt.

        Everything the verdict needs is produced by the server itself: the
        token count comes from the runtime tokenizer, the KV cost from the
        cached model's config, and the capacity from a live measurement -
        exactly the inputs the chat path would use, without ever touching the
        runtime's generation path.

        Args:
            params: Adapter parameters carrying message, optional system and
                max_tokens, and the target model.

        Returns:
            The dry-run CommandResult.
        """
        model_name = _resolve_target_model(params)
        if not model_name:
            return _CHAT_HANDLER.executor.failure_result(
                CommandName.ESTIMATE,
                ErrorCode.HARDWARE_CAPACITY_UNKNOWN,
                metadata={"detail": "no model_name was given and no model is resident or served, so there is nothing to size against"},
            )
        kv_profile = model_kv_profile(model_name)
        if kv_profile is None:
            return _CHAT_HANDLER.executor.failure_result(
                CommandName.ESTIMATE,
                ErrorCode.HARDWARE_CAPACITY_UNKNOWN,
                metadata={
                    "model_name": model_name,
                    "detail": "the model is not cached on disk or its config does not state the KV parameters, so the request cannot be sized",
                },
            )
        max_tokens = int(params.get("max_tokens", 128))
        return _CHAT_HANDLER.estimate(
            request_id=str(params.get("request_id") or f"estimate-{uuid4().hex}"),
            model_name=model_name,
            token_breakdown=prompt_token_breakdown(model_name, _chat_messages_from_params(params), max_tokens),
            declared_context_window=kv_profile.declared_context_window,
            capacity=capacity_snapshot(model_name),
            kv_bytes_per_token=kv_profile.kv_bytes_per_token(),
            per_request_overhead_bytes=_int_env("LMRS_PER_REQUEST_OVERHEAD_BYTES"),
            runtime_batch_overhead_bytes=_int_env("LMRS_RUNTIME_BATCH_OVERHEAD_BYTES"),
        )

    def delegate(self, params: Mapping[str, Any]) -> Any:
        if isinstance(params.get("message"), str) and params["message"]:
            return self._text_mode_estimate(params)
        missing = [name for name in _ESTIMATE_RAW_FIELDS if params.get(name) is None]
        if missing:
            raise ValueError(
                "estimate requires either message (text mode, optionally with "
                "model_name, system and max_tokens) or the full raw input set; "
                "missing raw fields: " + ", ".join(missing)
            )
        # Only the parameters this command declares may reach the handler. The
        # adapter injects its own keys (context, for one) into execute kwargs,
        # and splatting them through raised TypeError on the deployed server.
        request = {key: params[key] for key in _ESTIMATE_RAW_FIELDS}
        # A remote client sends JSON, so the structured inputs arrive as plain
        # mappings; the handler works on the domain types. Passing the mappings
        # through raised AttributeError on the deployed server.
        request["token_breakdown"] = self._coerce_token_breakdown(request["token_breakdown"])
        request["capacity"] = self._coerce_capacity(request["capacity"])
        return _CHAT_HANDLER.estimate(**request)


class InfoCommand(ThinAdapterCommand):
    """Adapter wrapper for the read-only service self-description."""

    name: ClassVar[str] = CommandName.INFO
    descr: ClassVar[str] = "Describe the service identity, build, runtime and capabilities"

    def delegate(self, params: Mapping[str, Any]) -> Any:
        # Imported here, not at module scope: lmrs.adapter.info reads this
        # module's command inventory, so a module-level import would close an
        # import cycle.
        from lmrs.adapter.info import build_info_payload

        return build_info_payload(params.get("registry"))


LMCACHE_PURGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "namespace": {"type": "string", "minLength": 1},
        "session": {"type": "string", "minLength": 1},
    },
    "additionalProperties": True,
}


class LocalLmcacheStatusCommand(ThinAdapterCommand):
    """Adapter wrapper for read-only LMCache status."""

    name: ClassVar[str] = CommandName.LOCAL_LMCACHE_STATUS
    descr: ClassVar[str] = "Report LMCache enablement, per-tier usage and limits, and hit accounting"

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return get_lmcache_status(_LMCACHE_POLICY, _lmcache_observations)


class LocalLmcachePurgeCommand(ThinAdapterCommand):
    """Adapter wrapper for global or scoped LMCache purge."""

    name: ClassVar[str] = CommandName.LOCAL_LMCACHE_PURGE
    descr: ClassVar[str] = "Remove cached LMCache artifacts globally or for one namespace/session binding"
    schema: ClassVar[dict[str, Any]] = LMCACHE_PURGE_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        namespace = params.get("namespace")
        session = params.get("session")
        return purge_lmcache(
            _LMCACHE_POLICY,
            namespace=namespace if isinstance(namespace, str) and namespace else None,
            session=session if isinstance(session, str) and session else None,
        )


class LocalModelSwitchCommand(ThinAdapterCommand):
    """Adapter wrapper for the queued full model switch."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_SWITCH
    descr: ClassVar[str] = "Switch the resident model to another model in one queued operation"
    use_queue: ClassVar[bool] = True
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        stages: list[str] = []
        result = switch_model(
            _param(params, "model_name"),
            stages.append,
            lifecycle=_LIFECYCLE,
            cache=_CACHE,
        )
        return {**result, "progress": stages}


class LocalModelReloadCommand(ThinAdapterCommand):
    """Adapter wrapper for model memory reload."""

    name: ClassVar[str] = CommandName.LOCAL_MODEL_RELOAD
    descr: ClassVar[str] = "Re-probe vLLM model residency in LMRS lifecycle state"
    schema: ClassVar[dict[str, Any]] = MODEL_NAME_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        return _LIFECYCLE.reload_model(_param(params, "model_name"))


LMRS_PUBLIC_COMMAND_CLASSES: tuple[type[ThinAdapterCommand], ...] = (
    HealthcheckCommand,
    ModelStatusCommand,
    CapacityCommand,
    TokenCountCommand,
    EstimateCommand,
    QueueStatusCommand,
    CancelCommand,
    InfoCommand,
    LocalModelCachePreloadCommand,
    LocalModelCacheStatusCommand,
    LocalModelCacheDeleteCommand,
    ChatCommand,
    LocalModelLoadCommand,
    LocalModelUnloadCommand,
    LocalModelReloadCommand,
    LocalModelSwitchCommand,
    LocalLmcacheStatusCommand,
    LocalLmcachePurgeCommand,
)
_REGISTERED_HOOKS: list[Callable[[object], None]] = []


def _command_name(command_class: type[ThinAdapterCommand]) -> str:
    return str(
        getattr(command_class, "name", "")
        or getattr(command_class, "command_name", "")
    )


def _iter_lmrs_command_classes() -> Iterable[type[ThinAdapterCommand]]:
    seen: set[type[ThinAdapterCommand]] = set()
    candidates = cast(
        "tuple[type[ThinAdapterCommand], ...]",
        (*LMRS_PUBLIC_COMMAND_CLASSES, *ThinAdapterCommand.__subclasses__()),
    )
    for command_class in candidates:
        if command_class in seen or not _command_name(command_class):
            continue
        seen.add(command_class)
        yield command_class


def _registered_command_names(registry: object) -> set[str]:
    names: set[str] = set()
    for attr_name in ("commands", "_commands", "registered_commands"):
        value = getattr(registry, attr_name, None)
        if isinstance(value, Mapping):
            names.update(str(key) for key in value)
            names.update(_command_name(cls) for cls in value.values())
        elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
            names.update(str(item) for item in value)
    for method_name in ("list_commands", "get_commands"):
        method = getattr(registry, method_name, None)
        if callable(method):
            value = method()
            if isinstance(value, Mapping):
                names.update(str(key) for key in value)
                names.update(_command_name(cls) for cls in value.values())
            elif isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
                names.update(str(item) for item in value)
    return {name for name in names if name}


def _is_registered(
    registry: object, command_class: type[ThinAdapterCommand]
) -> bool:
    name = _command_name(command_class)
    for method_name in ("is_registered", "has_command", "contains"):
        method = getattr(registry, method_name, None)
        if callable(method):
            try:
                if method(name) or method(command_class):
                    return True
            except TypeError:
                continue
    return name in _registered_command_names(registry)


def register_lmrs_commands(registry: object) -> None:
    """Idempotently register LMRS command classes as custom commands."""
    register = getattr(registry, "register", None)
    if not callable(register):
        raise TypeError("adapter registry must expose register(CommandClass, category)")
    for command_class in _iter_lmrs_command_classes():
        if _is_registered(registry, command_class):
            continue
        register(command_class, "custom")


def register_custom_commands_hook(
    hook_registry: object | None = None,
) -> Callable[[object], None]:
    """Install LMRS command registration into adapter startup hooks."""
    hook = register_lmrs_commands
    if hook not in _REGISTERED_HOOKS:
        _REGISTERED_HOOKS.append(hook)
    if hook_registry is None:
        return hook
    for method_name in (
        "register_custom_commands_hook",
        "add_custom_commands_hook",
        "register_hook",
        "append",
    ):
        method = getattr(hook_registry, method_name, None)
        if callable(method):
            method(hook)
            return hook
    if isinstance(hook_registry, MutableSequence):
        hook_registry.append(hook)
        return hook
    raise TypeError("hook registry cannot accept custom command hooks")


register_lmrs_commands.__auto_import_modules__ = [  # type: ignore[attr-defined]
    "lmrs.adapter.registration"
]
_adapter_register_custom_commands_hook(register_lmrs_commands)
