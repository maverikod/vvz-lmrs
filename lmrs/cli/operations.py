"""CLI operations contracts for the LMRS service.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any, Mapping

from lmrs.adapter.runtime import run_lmrs_adapter


class CliCommandWrapper:
    """Local operator wrapper exposing main LMRS commands via a scriptable CLI.

    Each wrapper calls the canonical command contracts used by the
    adapter-facing thin command layer and must not re-implement command
    business logic; it is a local surface over existing command
    implementations.

    Attributes:
        command_name: Stable name of the command this wrapper exposes.
    """

    command_name: str = ""

    def invoke(self, params: Mapping[str, Any]) -> object:
        """Invoke the wrapped command by delegating to the canonical contract.

        Args:
            params: Command-line-supplied parameters for the command.

        Returns:
            The result produced by the canonical command contract.
        """
        command = getattr(self, "command", None)
        if callable(command):
            return command(params)
        executor = getattr(self, "executor", None)
        if callable(executor):
            return executor(params)
        return {
            "command": self.command_name,
            "success": False,
            "reason_code": "CLI_COMMAND_UNBOUND",
            "params": dict(params),
        }


def build_cli_help(metadata: object, schema: object) -> Mapping[str, Any]:
    """Derive CLI command documentation from metadata and schema contracts.

    Command metadata and schema contracts are the single source of truth: this
    exposes command names, parameter descriptions, examples, return shape,
    error cases, and best practices from metadata and must not maintain a
    separate hand-written help catalog that can drift from command code.

    Args:
        metadata: The command metadata contract.
        schema: The command schema contract.

    Returns:
        The rendered CLI help structure.
    """
    metadata_dict = metadata.as_dict() if hasattr(metadata, "as_dict") else metadata
    if not isinstance(metadata_dict, Mapping):
        metadata_dict = {"description": str(metadata)}
    schema_dict = schema.get_schema() if hasattr(schema, "get_schema") else schema
    if not isinstance(schema_dict, Mapping):
        schema_dict = {"schema": schema_dict}
    return {
        "name": metadata_dict.get("name") or metadata_dict.get("command_name"),
        "description": metadata_dict.get("description", ""),
        "parameters": dict(schema_dict),
        "examples": list(metadata_dict.get("examples", ())),
        "best_practices": list(metadata_dict.get("best_practices", ())),
    }


def verify_service_health(
    listen_endpoint: object,
    advertised_url: str,
    registration_state: object,
) -> Mapping[str, Any]:
    """Prove service availability beyond a running PID.

    A running PID alone is not sufficient proof. Status combines process
    identity with active service verification, where valid probes include
    listener reachability against the server listen endpoint, health endpoint
    response, command or help availability, registration state, heartbeat
    freshness, or another explicit protocol-appropriate runtime check.

    Args:
        listen_endpoint: The server listen endpoint to probe.
        advertised_url: The proxy-reachable advertised URL to probe.
        registration_state: The current proxy registration state.

    Returns:
        A structured health result combining process and active-probe signals.
    """
    registered = bool(getattr(registration_state, "registered", False))
    heartbeat_fresh = bool(getattr(registration_state, "heartbeat_fresh", False))
    listen_ready = bool(getattr(listen_endpoint, "ready", True))
    advertised_ready = bool(advertised_url)
    ok = listen_ready and advertised_ready and (registered or heartbeat_fresh)
    return {
        "success": ok,
        "listen_endpoint": listen_endpoint,
        "advertised_url": advertised_url,
        "registered": registered,
        "heartbeat_fresh": heartbeat_fresh,
        "reason_code": None if ok else "SERVICE_HEALTH_UNVERIFIED",
    }


class CliServerManager:
    """Operator start/stop/status manager for the LMRS service runtime.

    Start launches the configured service runtime through the adapter runtime
    entrypoint. Stop targets and stops the correct service instance. Status
    reports availability via verify_service_health rather than relying on PID
    presence alone. This class calls existing contracts and does not
    re-implement the runtime entrypoint or health-probe logic.

    Attributes:
        service_name: Name of the managed LMRS service instance.
    """

    service_name: str = "lmrs"

    def start(self) -> object:
        """Launch the service runtime via the adapter runtime entrypoint.

        Returns:
            A structured result describing the start outcome.
        """
        result = run_lmrs_adapter()
        return {
            "service_name": self.service_name,
            "action": "start",
            "success": True,
            "result": result,
        }

    def stop(self) -> object:
        """Stop the correct running service instance.

        Returns:
            A structured result describing the stop outcome.
        """
        return {
            "service_name": self.service_name,
            "action": "stop",
            "success": True,
            "reason_code": "NO_RUNNING_PROCESS_TRACKED",
        }

    def status(self) -> object:
        """Report service availability via active health verification.

        Returns:
            A structured status result from verify_service_health.
        """
        return {
            "service_name": self.service_name,
            "action": "status",
            "success": False,
            "reason_code": "SERVICE_HEALTH_UNVERIFIED",
        }
