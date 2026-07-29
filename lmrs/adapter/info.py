"""Info command payload builder for the LMRS adapter.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations
from dataclasses import asdict, is_dataclass

import importlib.metadata
from typing import Any
from lmrs.adapter.registration import (  # type: ignore[import-not-found]
    LMRS_PUBLIC_COMMAND_CLASSES,
    _LIFECYCLE,
)
from lmrs.queue import RequestQueue  # type: ignore[import-not-found]


def build_info_payload(registry: Any) -> dict[str, Any]:
    """Build the info command response payload.

    Gathers: (1) identity block with product name, package version,
    and adapter version; (2) build metadata (build date/commit if
    available, else omitted); (3) runtime summary reporting live
    model-lifecycle status (via the adapter's module-level
    ``_LIFECYCLE`` singleton) and live queue state (via a fresh
    ``RequestQueue`` snapshot). VRAM facts and registration state are
    reported as explicitly unavailable: no live producer exists
    anywhere in lmrs that supplies real measured VRAM data or a real
    registration state to this function, so no values are fabricated
    for them; (4) capabilities from command registry metadata/schemas.

    Args:
        registry: The adapter command registry.

    Returns:
        A dictionary containing service identity, build metadata,
        runtime summary, and capabilities per command family.
    """
    # (1) Identity block
    try:
        package_version = importlib.metadata.version("lmrs")
    except importlib.metadata.PackageNotFoundError:
        package_version = "unknown"

    adapter_version = getattr(registry, "adapter_version", "unknown")
    identity = {
        "product_name": "Local Model Runtime Service",
        "package_version": package_version,
        "adapter_version": adapter_version,
    }

    # (2) Build metadata (omitted if not available)
    build_metadata: dict[str, Any] = {}

    # (3) Runtime summary: call into existing live accessors
    def _jsonable(value: Any) -> Any:
        """Return a JSON-safe form of a runtime accessor result."""
        for method_name in ("to_dict", "as_dict"):
            method = getattr(value, method_name, None)
            if callable(method):
                return method()
        if is_dataclass(value) and not isinstance(value, type):
            return asdict(value)
        return value

    runtime_summary: dict[str, Any] = {}
    try:
        lifecycle_status = _LIFECYCLE.model_lifecycle_status()
        runtime_summary["model_lifecycle"] = _jsonable(lifecycle_status)
    except Exception:
        pass
    try:
        queue = RequestQueue()
        queue_snapshot = queue.snapshot()
        if queue_snapshot is not None:
            runtime_summary["queue_state"] = _jsonable(queue_snapshot)
    except Exception:
        pass
    runtime_summary["vram"] = {
        "available": False,
        "reason": (
            "no live VRAM measurement producer is wired into the "
            "adapter runtime: VramRuntimeFacts/DynamicVramState "
            "(lmrs/vram.py) are frozen dataclasses with required "
            "fields and have no constructor call site anywhere in "
            "lmrs outside this module, so real facts cannot be "
            "gathered without fabricating them"
        ),
    }
    runtime_summary["registration"] = {
        "available": False,
        "reason": (
            "no live registration-state producer is wired into the "
            "adapter runtime: run_registration_lifecycle "
            "(lmrs/proxy/lifecycle.py) requires a policy, endpoint, "
            "advertised URL, payload, and runtime state this "
            "function has no access to, and it has no caller "
            "anywhere in lmrs, so a real RegistrationState cannot be "
            "gathered without fabricating one"
        ),
    }

    # (4) Capabilities: iterate commands and extract metadata per family
    capabilities: dict[str, list[dict[str, Any]]] = {}
    for cmd_class in LMRS_PUBLIC_COMMAND_CLASSES:
        cmd_name = getattr(cmd_class, "name", None)
        if not cmd_name:
            continue
        family = cmd_name.split("_")[0] if "_" in cmd_name else cmd_name
        if family not in capabilities:
            capabilities[family] = []
        cmd_schema = None
        cmd_metadata = None
        cmd_result_schema = None
        cmd_error_schema = None
        try:
            cmd_schema = cmd_class.get_schema()
        except Exception:
            pass
        try:
            cmd_metadata = cmd_class.metadata()
        except Exception:
            pass
        capability_entry: dict[str, Any] = {
            "name": cmd_name,
            "schema": cmd_schema or None,
            "metadata": cmd_metadata or None,
        }
        if cmd_result_schema is not None:
            capability_entry["result_schema"] = cmd_result_schema
        if cmd_error_schema is not None:
            capability_entry["error_schema"] = cmd_error_schema
        capabilities[family].append(capability_entry)

    return {
        "identity": identity,
        "build_metadata": build_metadata,
        "runtime_summary": runtime_summary,
        "capabilities": capabilities,
    }
