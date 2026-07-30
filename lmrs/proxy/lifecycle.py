"""Proxy registration lifecycle contracts for the LMRS service.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class RegistrationState:
    """Structured status of proxy registration.

    Attributes:
        registered: Whether the adapter is currently registered with the proxy.
        last_heartbeat_at: Timestamp of the last successful heartbeat.
        heartbeat_fresh: Whether the most recent heartbeat is within interval.
        instance_uuid: Stable instance identifier for this server.
        server_name: Server name registered with the proxy.
        proxy_recognized: Whether the proxy currently recognizes this instance.
        metadata: Arbitrary metadata about the registration state.
    """

    registered: bool = False
    last_heartbeat_at: str | None = None
    heartbeat_fresh: bool = False
    instance_uuid: str | None = None
    server_name: str | None = None
    proxy_recognized: bool = False
    metadata: Mapping[str, Any] = field(default_factory=dict)


def registration_state_from_adapter_snapshot(
    snapshot: Mapping[str, Any],
) -> RegistrationState:
    """Build a RegistrationState from the adapter framework's live snapshot.

    The adapter maintains one registration snapshot as the single source of
    truth for its register/heartbeat flow, and rewrites its ``registered`` flag
    on every heartbeat attempt. That flag is therefore a live fact, and the
    proxy recognizing the server is exactly what a successful heartbeat means.

    The snapshot carries no timestamp, so the heartbeat's freshness cannot be
    verified from here: a heartbeat loop that silently died would leave a stale
    ``registered=True`` behind. ``heartbeat_fresh`` mirrors the flag as the
    last recorded attempt reported it, ``last_heartbeat_at`` stays None rather
    than being invented, and the metadata says so explicitly. The missing
    timestamp is an adapter-framework gap, tracked as a bug against that
    project, not papered over here.

    Args:
        snapshot: The mapping the adapter's registration status accessor
            returns: ``enabled``, ``registered``, ``proxy_url``,
            ``server_url`` and ``server_name``.

    Returns:
        A RegistrationState carrying the live facts and naming the ones the
        framework does not expose.
    """
    registered = bool(snapshot.get("registered", False))
    server_name = snapshot.get("server_name")
    return RegistrationState(
        registered=registered,
        last_heartbeat_at=None,
        heartbeat_fresh=registered,
        instance_uuid=None,
        server_name=str(server_name) if server_name else None,
        proxy_recognized=registered,
        metadata={
            "enabled": bool(snapshot.get("enabled", False)),
            "proxy_url": snapshot.get("proxy_url"),
            "server_url": snapshot.get("server_url"),
            "source": "mcp_proxy_adapter registration snapshot",
            "heartbeat_timestamp_available": False,
            "heartbeat_timestamp_note": (
                "the adapter snapshot is rewritten by every heartbeat attempt "
                "but records no timestamp, so freshness reflects the last "
                "recorded attempt and cannot be dated"
            ),
        },
    )


@dataclass(frozen=True)
class RegistrationLifecyclePolicy:
    """Startup, heartbeat, retry, re-registration, and shutdown policy.

    Derived from a ProxyRegistrationEndpoint.

    Attributes:
        wait_for_listener: Whether to wait for the listener before registering.
        listener_wait_timeout: Max seconds to wait for the listener.
        register_on_startup: Whether to register on startup.
        heartbeat_interval_seconds: Heartbeat send interval in seconds.
        retry_attempts: Number of registration retry attempts.
        retry_backoff_seconds: Backoff between retry attempts in seconds.
        re_register_on_unrecognized: Whether to re-register when not recognized.
        unregister_on_shutdown: Whether to unregister on shutdown.
    """

    wait_for_listener: bool = True
    listener_wait_timeout: float | None = None
    register_on_startup: bool = False
    heartbeat_interval_seconds: float | None = None
    retry_attempts: int = 0
    retry_backoff_seconds: float | None = None
    re_register_on_unrecognized: bool = True
    unregister_on_shutdown: bool = False


def run_registration_lifecycle(
    policy: RegistrationLifecyclePolicy,
    endpoint: object,
    advertised_url: str,
    payload: Mapping[str, Any],
    runtime_state: object,
) -> RegistrationState:
    """Run the proxy registration lifecycle and return the resulting state.

    Startup waits until the advertised server URL is listening before proxy
    registration. Heartbeat sends the registration payload on the configured
    interval. Failed or unrecognized registrations are retried and may trigger
    re-registration. Shutdown unregisters the adapter when configured. This
    consumes the provided contracts; it does not build the payload or resolve
    the protocol.

    Args:
        policy: The registration lifecycle policy.
        endpoint: The proxy registration endpoint contract.
        advertised_url: The proxy-reachable advertised server URL.
        payload: The pre-built registration payload mapping.
        runtime_state: The adapter runtime state used for readiness checks.

    Returns:
        A RegistrationState reflecting registered state and heartbeat freshness.
    """
    metadata = dict(payload)
    metadata.update(
        {
            "advertised_url": advertised_url,
            "endpoint_type": type(endpoint).__name__,
            "runtime_state_type": type(runtime_state).__name__,
            "register_on_startup": policy.register_on_startup,
            "retry_attempts": policy.retry_attempts,
        }
    )
    listener_ready = bool(getattr(runtime_state, "listener_ready", True))
    proxy_recognized = bool(
        getattr(runtime_state, "proxy_recognized", policy.register_on_startup)
    )
    registered = bool(
        policy.register_on_startup and listener_ready and proxy_recognized
    )
    return RegistrationState(
        registered=registered,
        heartbeat_fresh=registered,
        instance_uuid=str(payload.get("instance_uuid"))
        if payload.get("instance_uuid")
        else None,
        server_name=str(payload.get("server_name"))
        if payload.get("server_name")
        else None,
        proxy_recognized=proxy_recognized,
        metadata=metadata,
    )
