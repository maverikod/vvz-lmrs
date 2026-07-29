"""Adapter configuration generation, validation, and runtime entrypoint for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Coroutine, Iterable, Mapping
from copy import deepcopy
from typing import Any, cast

from lmrs.adapter.registration import register_custom_commands_hook


ADAPTER_DOCUMENTATION_BASELINE: str = (
    "Integration reference: the mcp_proxy_adapter README and project rules "
    "define server structure, startup hooks, custom command registration, "
    "configuration generation and validation, and metadata-based command "
    "discovery used by the LMRS adapter layer. This baseline guides "
    "implementation conventions only; it does not replace the LMRS HRS and "
    "MRS product requirements."
)


_REQUIRED_ADAPTER_SECTIONS = ("server", "registration", "queue_manager")
_REQUIRED_LMRS_SECTIONS = (
    "hardware_profile",
    "resident_services",
    "model_profiles",
    "runtime_backends",
    "tokenizer_profiles",
    "admission_policy",
    "disk_model_cache",
    "lmcache",
    "telemetry",
    "lifecycle_limits",
    "queue_policy",
    "queue_limits",
)


def generate_lmrs_config() -> dict[str, Any]:
    """Produce adapter base sections plus LMRS runtime/admission sections."""
    return {
        "adapter": {
            "server": {"name": "Local Model Runtime Service", "host": "127.0.0.1", "port": 8012},
            "registration": {"enabled": True, "server_id": "lmrs", "auto_register": True},
            "queue_manager": {"enabled": True, "max_workers": 1},
        },
        "lmrs": {
            "hardware_profile": {"gpu_count": 1, "safety_margin_bytes": 0},
            "resident_services": [],
            "model_profiles": {},
            "runtime_backends": {"default": "vllm"},
            "tokenizer_profiles": {},
            "admission_policy": {"strategy": "largest_fit"},
            "disk_model_cache": {"enabled": True, "path": "/var/cache/lmrs/models"},
            "lmcache": {"enabled": False, "storage_path": "/var/cache/lmrs/lmcache"},
            "telemetry": {"enabled": True},
            "lifecycle_limits": {"max_loaded_models": 1},
            "queue_policy": "largest_fit",
            "queue_limits": {"max_depth": 0},
        },
    }


def _adapter_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    adapter = config.get("adapter", config)
    return adapter if isinstance(adapter, Mapping) else {}


def _lmrs_config(config: Mapping[str, Any]) -> Mapping[str, Any]:
    lmrs = config.get("lmrs", config)
    return lmrs if isinstance(lmrs, Mapping) else {}


def validate_lmrs_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """Validate adapter base and LMRS-specific config before startup."""
    errors: list[str] = []
    adapter = _adapter_config(config)
    lmrs = _lmrs_config(config)
    for section in _REQUIRED_ADAPTER_SECTIONS:
        if not isinstance(adapter.get(section), Mapping):
            errors.append(f"adapter.{section} section is required")
    for section in _REQUIRED_LMRS_SECTIONS:
        if section not in lmrs:
            errors.append(f"lmrs.{section} section is required")
    model_profiles = lmrs.get("model_profiles")
    if model_profiles is not None and not isinstance(model_profiles, Mapping):
        errors.append("lmrs.model_profiles must be a mapping")
    runtime_backends = lmrs.get("runtime_backends")
    if runtime_backends is not None and not isinstance(runtime_backends, Mapping):
        errors.append("lmrs.runtime_backends must be a mapping")
    queue_limits = lmrs.get("queue_limits")
    if queue_limits is not None and not isinstance(queue_limits, Mapping):
        errors.append("lmrs.queue_limits must be a mapping")
    return {"valid": not errors, "errors": tuple(errors), "config": deepcopy(dict(config))}


def _start_lifecycle_services(
    lifecycle_services: Iterable[object],
    config: Mapping[str, Any],
) -> int:
    started = 0
    for service in lifecycle_services:
        start = getattr(service, "start", None)
        if callable(start):
            start(config)
            started += 1
    return started


def _load_runtime_config(loader: Callable[[], Mapping[str, Any]] | None) -> Mapping[str, Any]:
    if loader is None:
        return generate_lmrs_config()
    return loader()


def _default_server_factory() -> Callable[..., Any]:
    """Resolve the adapter's server factory.

    Imported lazily so this module stays importable without the optional
    ``[server]`` extra installed.

    Returns:
        The ``mcp_proxy_adapter`` ``create_and_run_server`` factory.
    """
    from mcp_proxy_adapter.core.app_factory import create_and_run_server

    return create_and_run_server


def _run_awaitable(awaitable: Any) -> Any:
    """Run an awaitable server-factory result on a fresh event loop.

    Args:
        awaitable: The awaitable returned by the server factory.

    Returns:
        Whatever the awaitable resolves to.
    """
    return asyncio.run(cast("Coroutine[Any, Any, Any]", awaitable))


def start_adapter_server(
    config_path: str | None = None,
    *,
    config: Mapping[str, Any] | None = None,
    create_and_run_server: Callable[..., Any] | None = None,
    runner: Callable[[Any], Any] | None = None,
    **factory_kwargs: Any,
) -> Any:
    """Run the single canonical LMRS server startup sequence.

    This is the one place that installs command registration and hands control
    to the adapter server factory. Both ``lmrs.__main__`` and
    ``run_lmrs_adapter`` delegate here rather than repeating the sequence, so
    exactly one startup seam exists (C-050).

    Args:
        config_path: Path to the LMRS configuration file, when starting from disk.
        config: In-memory configuration mapping, when starting from a loaded config.
        create_and_run_server: Server factory override; the adapter factory is
            imported when omitted.
        runner: Callable used to run an awaitable factory result; defaults to
            ``asyncio.run``.
        **factory_kwargs: Extra keyword arguments forwarded to the factory.

    Returns:
        Whatever the server factory returns, after awaiting it when needed.
    """
    # Importing registration installs the adapter command hook; it must happen
    # before control passes to the factory.
    import lmrs.adapter.registration  # noqa: F401

    factory = create_and_run_server if create_and_run_server is not None else _default_server_factory()
    kwargs: dict[str, Any] = dict(factory_kwargs)
    if config_path is not None:
        kwargs["config_path"] = config_path
    if config is not None:
        kwargs["config"] = config
    result = factory(**kwargs)
    if inspect.isawaitable(result):
        run = runner if runner is not None else _run_awaitable
        return run(result)
    return result


def run_lmrs_adapter(
    *,
    config_loader: Callable[[], Mapping[str, Any]] | None = None,
    lifecycle_services: Iterable[object] = (),
    create_and_run_server: Callable[..., Any] | None = None,
    hook_registry: object | None = None,
) -> Any:
    """Register hooks, validate config, start lifecycle services, run adapter.

    Owns configuration loading, validation and lifecycle-service startup, then
    delegates the server start itself to ``start_adapter_server``. Without a
    server factory it performs those preparation steps and reports them without
    starting a server, which is the seam the CLI and tests use.

    Args:
        config_loader: Callable returning the runtime configuration; the
            generated default configuration is used when omitted.
        lifecycle_services: Services whose ``start`` is called with the config.
        create_and_run_server: Server factory override; without it no server is
            started and the prepared state is returned instead.
        hook_registry: Registry receiving the custom-commands hook.

    Returns:
        The prepared startup state when no factory is given, otherwise the
        result of the canonical startup path.

    Raises:
        ValueError: If the loaded configuration fails validation.
    """
    hook = register_custom_commands_hook(hook_registry)
    config = _load_runtime_config(config_loader)
    validation = validate_lmrs_config(config)
    if not validation["valid"]:
        raise ValueError("LMRS configuration validation failed: " + "; ".join(validation["errors"]))
    lifecycle_count = _start_lifecycle_services(lifecycle_services, config)
    if create_and_run_server is None:
        return {
            "config": validation["config"],
            "hook": hook,
            "lifecycle_services_started": lifecycle_count,
            "server_result": None,
        }
    return start_adapter_server(
        config=validation["config"],
        create_and_run_server=create_and_run_server,
        custom_commands_hook=hook,
        lifecycle_services_started=lifecycle_count,
    )
