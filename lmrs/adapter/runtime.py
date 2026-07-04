"""Adapter configuration generation, validation, and runtime entrypoint for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from typing import Any

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


def run_lmrs_adapter(
    *,
    config_loader: Callable[[], Mapping[str, Any]] | None = None,
    lifecycle_services: Iterable[object] = (),
    create_and_run_server: Callable[..., Any] | None = None,
    hook_registry: object | None = None,
) -> Any:
    """Register hooks, validate config, start lifecycle services, run adapter."""
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
    return create_and_run_server(
        config=validation["config"],
        custom_commands_hook=hook,
        lifecycle_services_started=lifecycle_count,
    )
