"""Service-boundary contract objects for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping, Sequence

from lmrs.commands import CommandName


@dataclass(frozen=True)
class AdapterExposure:
    """Public MCP exposure requirements for the adapter interface.

    Captures concept C-003 (Adapter-Based MCP Interface).

    Attributes:
        command_surface: Names of the MCP commands exposed.
        requires_schemas: Whether each published command has a schema.
        requires_metadata: Whether the adapter exposes metadata.
        requires_structured_errors: Whether failures are structured.
        requires_healthcheck: Whether a healthcheck command exists.
        requires_proxy_registration: Whether proxy registration is required.
    """

    command_surface: tuple[str, ...]
    requires_schemas: bool
    requires_metadata: bool
    requires_structured_errors: bool
    requires_healthcheck: bool
    requires_proxy_registration: bool


@dataclass(frozen=True)
class MVPScope:
    """Explicit capability scope for the initial LMRS MVP implementation.

    Captures concept C-022 (MVP Scope): defines which capabilities are
    included in and excluded from the first LMRS release.

    Attributes:
        included_capabilities: Names of capabilities included in this MVP.
        excluded_capabilities: Names of capabilities explicitly excluded.
    """

    included_capabilities: tuple[str, ...]
    excluded_capabilities: tuple[str, ...]

    def includes(self, capability: str) -> bool:
        """Return True if capability is in the included set.

        Args:
            capability: The capability name to look up.

        Returns:
            True if capability appears in included_capabilities.
        """
        return capability in self.included_capabilities

    def excludes(self, capability: str) -> bool:
        """Return True if capability is in the excluded set.

        Args:
            capability: The capability name to look up.

        Returns:
            True if capability appears in excluded_capabilities.
        """
        return capability in self.excluded_capabilities


@dataclass(frozen=True)
class ServiceBoundary:
    """Top-level contract describing the LMRS service boundary.

    Aggregates concepts C-001 (standalone local runtime), C-002 (provider
    delegation), C-003 (adapter exposure), and C-022 (MVP scope) into a
    single frozen contract object.

    Attributes:
        service_name: Canonical name of the LMRS service instance.
        owned_responsibilities: Capabilities owned and executed locally.
        delegated_responsibilities: Capabilities delegated to external providers.
        adapter_exposure: MCP exposure requirements for this boundary.
        mvp_scope: Capability scope for the current MVP.
        invariants: Invariant statements that must hold across all deployments.
    """

    service_name: str
    owned_responsibilities: tuple[str, ...]
    delegated_responsibilities: tuple[str, ...]
    adapter_exposure: AdapterExposure
    mvp_scope: MVPScope
    invariants: tuple[str, ...] = field(default_factory=tuple)

    def owns(self, responsibility: str) -> bool:
        """Return True if the responsibility is owned locally.

        Args:
            responsibility: The responsibility name to check.

        Returns:
            True if responsibility appears in owned_responsibilities.
        """
        return responsibility in self.owned_responsibilities

    def delegates(self, responsibility: str) -> bool:
        """Return True if the responsibility is delegated externally.

        Args:
            responsibility: The responsibility name to check.

        Returns:
            True if responsibility appears in delegated_responsibilities.
        """
        return responsibility in self.delegated_responsibilities


def build_default_service_boundary() -> ServiceBoundary:
    """Construct a ServiceBoundary reflecting the default LMRS MVP configuration.

    Builds AdapterExposure and MVPScope from MRS-defined constants (C-001,
    C-002, C-003, C-022) and returns a frozen ServiceBoundary for the baseline
    local-runtime deployment.

    Returns:
        A ServiceBoundary populated with default LMRS MVP values.
    """
    exposure = AdapterExposure(
        command_surface=(
            CommandName.HEALTHCHECK,
            CommandName.MODEL_STATUS,
            CommandName.CAPACITY,
            CommandName.TOKEN_COUNT,
            CommandName.ESTIMATE,
            CommandName.QUEUE_STATUS,
            CommandName.CANCEL,
            CommandName.INFO,
            CommandName.LOCAL_MODEL_CACHE_PRELOAD,
            CommandName.LOCAL_MODEL_CACHE_STATUS,
            CommandName.LOCAL_MODEL_CACHE_DELETE,
            CommandName.CHAT,
            CommandName.LOCAL_MODEL_LOAD,
            CommandName.LOCAL_MODEL_UNLOAD,
            CommandName.LOCAL_MODEL_RELOAD,
            CommandName.LOCAL_MODEL_SWITCH,
            CommandName.LOCAL_LMCACHE_STATUS,
            CommandName.LOCAL_LMCACHE_PURGE,
        ),
        requires_schemas=True,
        requires_metadata=True,
        requires_structured_errors=True,
        requires_healthcheck=True,
        requires_proxy_registration=True,
    )
    scope = MVPScope(
        included_capabilities=(
            "local_model_runtime",
            "capacity_proof",
            "adapter_command_exposure",
            "local_model_disk_cache",
            "model_memory_lifecycle",
        ),
        excluded_capabilities=(
            "universal_provider_gateway",
            "multi_tenant_routing",
            "cloud_offload",
        ),
    )
    return ServiceBoundary(
        service_name="lmrs",
        owned_responsibilities=(
            "local_model_runtime",
            "capacity_proof",
            "local_model_disk_cache",
            "model_memory_lifecycle",
        ),
        delegated_responsibilities=("universal_provider_gateway",),
        adapter_exposure=exposure,
        mvp_scope=scope,
        invariants=(
            "All model inference runs locally.",
            "Provider gateway is delegated, never owned.",
            "All adapter commands conform to C-003 exposure requirements.",
            "The declared command surface matches registered LMRS adapter commands.",
        ),
    )


def boundary_snapshot(boundary: ServiceBoundary) -> Mapping[str, object]:
    """Return a serializable mapping of all ServiceBoundary public fields.

    Args:
        boundary: The ServiceBoundary instance to snapshot.

    Returns:
        A mapping with serializable list values for all public boundary fields.
    """
    return {
        "service_name": boundary.service_name,
        "owned_responsibilities": list(boundary.owned_responsibilities),
        "delegated_responsibilities": list(boundary.delegated_responsibilities),
        "command_surface": list(boundary.adapter_exposure.command_surface),
        "included_capabilities": list(boundary.mvp_scope.included_capabilities),
        "excluded_capabilities": list(boundary.mvp_scope.excluded_capabilities),
        "invariants": list(boundary.invariants),
    }


__all__: Sequence[str] = [
    "AdapterExposure",
    "MVPScope",
    "ServiceBoundary",
    "build_default_service_boundary",
    "boundary_snapshot",
]
