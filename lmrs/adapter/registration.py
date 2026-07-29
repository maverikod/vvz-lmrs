"""Adapter command registration and thin command wrappers for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""
from __future__ import annotations  # noqa: I001 (project-wide false positive)

from collections.abc import Callable, Iterable, Mapping, MutableSequence
from copy import deepcopy
from dataclasses import asdict, is_dataclass
import os
from typing import Any, ClassVar, cast

from lmrs.commands import CommandName
from lmrs.lmcache import LMCacheStoragePolicy, get_lmcache_status, purge_lmcache
from lmrs.model_cache import DiskModelCache
from lmrs.model_lifecycle import ModelMemoryLifecycle, switch_model
from lmrs.runtime_client import VLLMOpenAIClient
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
        """Return compact help metadata for proxy consumers."""
        return {"name": cls.name, "summary": cls.descr, "type": "custom"}

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


_CACHE = DiskModelCache(
    cache_root=os.environ.get("LMRS_MODEL_CACHE_ROOT", "/var/lmrs/hf-cache"),
)
_VLLM_CLIENT = VLLMOpenAIClient(
    base_url=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000"),
)
_LIFECYCLE = ModelMemoryLifecycle(
    runtime_backend="vllm", model_probe=_VLLM_CLIENT.is_model_served
)
_LMCACHE_POLICY = LMCacheStoragePolicy(
    enabled=os.environ.get("LMRS_LMCACHE_ENABLED", "").lower() in {"1", "true", "yes"},
    cache_storage_path=os.environ.get("LMRS_LMCACHE_PATH", "/var/lmrs/lmcache"),
)


def _lmcache_observations() -> Mapping[str, Any]:
    """Return raw LMCache runtime observations for the status command.

    The LMCache backend publishes its counters through the runtime wiring; until
    that wiring reports them, no observation is available and the status command
    answers with the policy plus zeroed counters rather than inventing figures.

    Returns:
        A mapping of raw observations consumed by ``build_lmcache_telemetry``.
    """
    return {}


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
    """Adapter wrapper for a real vLLM chat completion call."""

    name: ClassVar[str] = CommandName.CHAT
    descr: ClassVar[str] = "Send a chat message to the locally served vLLM model"
    schema: ClassVar[dict[str, Any]] = CHAT_SCHEMA

    def delegate(self, params: Mapping[str, Any]) -> Any:
        message = _param(params, "message")
        model_name = _param(params, "model_name")
        messages: list[dict[str, str]] = []
        system = params.get("system")
        if isinstance(system, str) and system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": message})
        options: dict[str, object] = {}
        if "temperature" in params:
            options["temperature"] = params["temperature"]
        if "max_tokens" in params:
            options["max_tokens"] = params["max_tokens"]
        return _VLLM_CLIENT.chat_completion(model_name, messages, **options)


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
