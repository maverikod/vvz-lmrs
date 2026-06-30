"""Adapter configuration generation, validation, and runtime entrypoint for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any, Mapping


def generate_lmrs_config() -> Mapping[str, Any]:
    """Produce the starting LMRS runtime configuration mapping.

    First creates the adapter server, registration, and queue-manager
    configuration, then adds LMRS-specific sections for hardware profile,
    resident services, model profiles, runtime backends, tokenizer profiles,
    admission policy, disk model cache, LMCache, telemetry, and lifecycle
    limits. The generated configuration is the starting point for validated
    LMRS runtime configuration and is owned by Runtime Configuration.

    Returns:
        A configuration mapping with adapter base and LMRS-specific sections.
    """
    raise NotImplementedError("generate_lmrs_config is a contract stub")


def validate_lmrs_config(config: Mapping[str, Any]) -> object:
    """Validate adapter base and LMRS-specific configuration before startup.

    First checks base adapter configuration sections, then checks the LMRS
    model, runtime, tokenizer, hardware, resident-service, queue, admission,
    disk-cache, LMCache, error, and telemetry sections. Rejects inconsistent or
    incomplete configuration before LMRS starts or accepts runtime work. The
    validated configuration belongs to Runtime Configuration.

    Args:
        config: The runtime configuration mapping to validate.

    Returns:
        A structured validation result describing acceptance or rejection.
    """
    raise NotImplementedError("validate_lmrs_config is a contract stub")


def run_lmrs_adapter() -> None:
    """Start the LMRS adapter: hooks, configuration, lifecycle, server handoff.

    Registers LMRS command hooks before adapter server startup, loads and
    validates configuration, starts LMRS domain lifecycle services, and hands
    control to the adapter create_and_run_server function. The adapter factory
    code stays free of LMRS admission, tokenizer, VRAM, and model-lifecycle
    logic.

    Returns:
        None.
    """
    raise NotImplementedError("run_lmrs_adapter is a contract stub")


ADAPTER_DOCUMENTATION_BASELINE: str = (
    "Integration reference: the mcp_proxy_adapter README and project rules "
    "define server structure, startup hooks, custom command registration, "
    "configuration generation and validation, and metadata-based command "
    "discovery used by the LMRS adapter layer. This baseline guides "
    "implementation conventions only; it does not replace the LMRS HRS and "
    "MRS product requirements."
)
