"""Adapter command registration and thin command wrappers for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableSequence
from typing import Any, ClassVar

try:  # Optional server extra; base installs keep this module importable.
    from mcp_proxy_adapter.commands.command import Command as _AdapterCommand
except Exception:  # pragma: no cover - depends on optional adapter package layout.
    _AdapterCommand = object


class AdapterResultEnvelope(dict):
    """Minimal adapter-shaped result used when no adapter result class is present."""


class ThinAdapterCommand(_AdapterCommand):  # type: ignore[misc, valid-type]
    """Base contract for adapter-facing LMRS command wrappers.

    Concrete subclasses expose a stable ``name``, may set ``use_queue`` for
    long-running work, validate adapter parameters, delegate to a domain
    executor or service, and return an adapter success or error result. This
    class owns only adapter translation; admission, tokenizer, VRAM, and model
    lifecycle decisions remain in LMRS domain services.
    """

    name: ClassVar[str] = ""
    use_queue: ClassVar[bool] = False
    executor: ClassVar[Callable[[Mapping[str, Any]], Any] | None] = None

    def validate(self, params: Mapping[str, Any]) -> None:
        """Validate adapter request parameters before delegation."""
        if not isinstance(params, Mapping):
            raise TypeError("adapter command parameters must be a mapping")

    def delegate(self, params: Mapping[str, Any]) -> Any:
        """Call the configured domain executor or service."""
        if self.executor is None:
            raise RuntimeError(f"{type(self).__name__} has no domain executor")
        return self.executor(params)

    def success_result(self, payload: Any) -> object:
        """Return an adapter success result or a compatible structured envelope."""
        return AdapterResultEnvelope(success=True, payload=payload, command=self.name)

    def error_result(self, code: str, message: str) -> object:
        """Return an adapter error result or a compatible structured envelope."""
        return AdapterResultEnvelope(
            success=False,
            error={"code": code, "message": message},
            command=self.name,
        )

    def execute(self, params: Mapping[str, Any]) -> object:
        """Validate, delegate to the domain service, and return a result."""
        try:
            self.validate(params)
            return self.success_result(self.delegate(params))
        except Exception as exc:  # Adapter wrappers translate exceptions only.
            return self.error_result(type(exc).__name__, str(exc))


LMRS_PUBLIC_COMMAND_CLASSES: tuple[type[ThinAdapterCommand], ...] = ()
_REGISTERED_HOOKS: list[Callable[[object], None]] = []


def _command_name(command_class: type[Any]) -> str:
    return str(
        getattr(command_class, "name", "")
        or getattr(command_class, "command_name", "")
    )


def _iter_lmrs_command_classes() -> Iterable[type[ThinAdapterCommand]]:
    seen: set[type[ThinAdapterCommand]] = set()
    for command_class in (*LMRS_PUBLIC_COMMAND_CLASSES, *ThinAdapterCommand.__subclasses__()):
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


def _is_registered(registry: object, command_class: type[Any]) -> bool:
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
