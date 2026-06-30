"""CLI operations contracts for the LMRS service.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any, Mapping


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
        raise NotImplementedError("CliCommandWrapper.invoke is a contract stub")


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
    raise NotImplementedError("build_cli_help is a contract stub")


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
    raise NotImplementedError("verify_service_health is a contract stub")


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
        raise NotImplementedError("CliServerManager.start is a contract stub")

    def stop(self) -> object:
        """Stop the correct running service instance.

        Returns:
            A structured result describing the stop outcome.
        """
        raise NotImplementedError("CliServerManager.stop is a contract stub")

    def status(self) -> object:
        """Report service availability via active health verification.

        Returns:
            A structured status result from verify_service_health.
        """
        raise NotImplementedError("CliServerManager.status is a contract stub")
