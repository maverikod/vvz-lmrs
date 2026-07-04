"""Proxy registration network contracts for the LMRS service.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import urlparse


@dataclass(frozen=True)
class ServerListenEndpoint:
    """Adapter server bind endpoint.

    The protocol controls whether the listener is HTTP, HTTPS, or mTLS. Listen
    settings are independent from proxy registration settings.

    Attributes:
        host: Bind host or address for the listener.
        port: Bind port for the listener.
        protocol: Listener protocol; one of "http", "https", or "mtls".
        server_name: Logical server name for the listener.
        ssl_certfile: Path to the server SSL certificate file.
        ssl_keyfile: Path to the server SSL private key file.
        ca_cert: Path to the CA certificate for client verification.
        advertised_host: Optional explicit host to advertise to the proxy.
        metadata: Arbitrary metadata about the listener.
    """

    host: str
    port: int
    protocol: str = "http"
    server_name: str = ""
    ssl_certfile: str | None = None
    ssl_keyfile: str | None = None
    ca_cert: str | None = None
    advertised_host: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


def resolve_advertised_url(endpoint: ServerListenEndpoint) -> str:
    """Derive the proxy-visible URL from a server listen endpoint.

    Wildcard bind addresses are not advertised directly; an explicit advertised
    host is preferred when configured; for local deployments a wildcard bind
    address is normalized to a reachable host such as localhost.

    Args:
        endpoint: The server listen endpoint to derive the URL from.

    Returns:
        The normalized, proxy-reachable advertised URL string.
    """
    scheme = "https" if endpoint.protocol in ("https", "mtls") else "http"
    host = endpoint.advertised_host or endpoint.host
    if host in ("0.0.0.0", "::", "*", ""):
        host = "localhost"
    return f"{scheme}://{host}:{endpoint.port}"


@dataclass(frozen=True)
class ProxyRegistrationEndpoint:
    """Registration-side contract for registering with the MCP proxy.

    Registration settings are separate from listener settings.

    Attributes:
        enabled: Whether proxy registration is enabled.
        register_on_startup: Whether to register on startup.
        unregister_on_shutdown: Whether to unregister on shutdown.
        register_url: Proxy registration URL.
        unregister_url: Proxy unregistration URL.
        heartbeat_url: Proxy heartbeat URL.
        heartbeat_interval_seconds: Heartbeat interval in seconds.
        server_id: Explicit stable server identifier.
        server_name: Explicit server name.
        instance_uuid: Stable instance UUID for this server.
        metadata: Arbitrary registration metadata.
        client_cert: Path to the client certificate for mTLS.
        client_key: Path to the client private key for mTLS.
        ca_cert: Path to the CA certificate for mTLS.
    """

    enabled: bool = False
    register_on_startup: bool = False
    unregister_on_shutdown: bool = False
    register_url: str | None = None
    unregister_url: str | None = None
    heartbeat_url: str | None = None
    heartbeat_interval_seconds: float | None = None
    server_id: str | None = None
    server_name: str | None = None
    instance_uuid: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    client_cert: str | None = None
    client_key: str | None = None
    ca_cert: str | None = None


def resolve_registration_protocol(endpoint: ProxyRegistrationEndpoint) -> str:
    """Resolve the proxy client protocol from the registration URL and creds.

    HTTP registration URLs select HTTP. HTTPS registration URLs select HTTPS
    unless client certificate, client key, and CA material are all present, in
    which case mTLS is selected. A missing or unsupported URL scheme is a
    configuration error.

    Args:
        endpoint: The proxy registration endpoint contract.

    Returns:
        The resolved protocol: "http", "https", or "mtls".
    """
    url = endpoint.register_url
    if not isinstance(url, str) or not url:
        raise ValueError("registration URL is required to resolve protocol")
    if url.startswith("http://"):
        return "http"
    if url.startswith("https://"):
        if endpoint.client_cert and endpoint.client_key and endpoint.ca_cert:
            return "mtls"
        return "https"
    raise ValueError("unsupported registration URL scheme")


def build_registration_payload(
    endpoint: ProxyRegistrationEndpoint,
    advertised_url: str,
) -> Mapping[str, Any]:
    """Assemble the registration payload sent to the MCP proxy.

    Resolves the server name by preferring an explicit server identifier, then
    an explicit server name, then a deterministic default from normalized host
    and port. Includes the reachable advertised URL, capabilities, and metadata
    (server protocol, host, port, stable instance UUID, and additional
    discovery metadata).

    Args:
        endpoint: The proxy registration endpoint contract.
        advertised_url: The proxy-reachable advertised server URL.

    Returns:
        The registration payload mapping.
    """
    parsed = urlparse(advertised_url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 0
    server_name = endpoint.server_id or endpoint.server_name or f"{host}-{port}"
    metadata: dict[str, Any] = {
        "protocol": parsed.scheme or "http",
        "host": host,
        "port": port,
        "instance_uuid": endpoint.instance_uuid,
    }
    metadata.update(dict(endpoint.metadata))
    return {
        "server_id": endpoint.server_id,
        "server_name": server_name,
        "url": advertised_url,
        "capabilities": [],
        "metadata": metadata,
    }


def lmrs_network_config_contract(
    config: Mapping[str, Any],
) -> tuple[ServerListenEndpoint, ProxyRegistrationEndpoint]:
    """Build listen and registration endpoints from validated configuration.

    Reads the listen and registration configuration sections produced by the
    LMRS configuration generator and checked by the LMRS configuration
    validator, and constructs a ServerListenEndpoint and a
    ProxyRegistrationEndpoint, keeping listener and registration settings
    separate. It does not regenerate, revalidate, or perform network calls.

    Args:
        config: The generated and validated runtime configuration mapping.

    Returns:
        A tuple of the listen endpoint and the proxy registration endpoint.
    """

    def _section(label: str, *names: str) -> Mapping[str, Any]:
        for name in names:
            if name in config:
                value = config[name]
                if value is None:
                    break
                return dict(value)
        raise ValueError(f'{label} configuration section is required')

    listen_config = _section('listen', 'listen', 'server_listen', 'server_listen_endpoint')
    registration_config = _section(
        'registration',
        'registration',
        'proxy_registration',
        'registration_endpoint',
    )

    listen_endpoint = ServerListenEndpoint(
        host=str(listen_config.get('host', '')),
        port=int(listen_config.get('port', 0)),
        protocol=str(listen_config.get('protocol', 'http')).lower(),
        server_name=str(listen_config.get('server_name', '') or ''),
        ssl_certfile=listen_config.get('ssl_certfile'),
        ssl_keyfile=listen_config.get('ssl_keyfile'),
        ca_cert=listen_config.get('ca_cert'),
        advertised_host=listen_config.get('advertised_host'),
        metadata=dict(listen_config.get('metadata') or {}),
    )

    registration_endpoint = ProxyRegistrationEndpoint(
        enabled=bool(registration_config.get('enabled', False)),
        register_on_startup=bool(registration_config.get('register_on_startup', False)),
        unregister_on_shutdown=bool(registration_config.get('unregister_on_shutdown', False)),
        register_url=registration_config.get('register_url'),
        unregister_url=registration_config.get('unregister_url'),
        heartbeat_url=registration_config.get('heartbeat_url'),
        heartbeat_interval_seconds=registration_config.get('heartbeat_interval_seconds'),
        server_id=registration_config.get('server_id'),
        server_name=registration_config.get('server_name'),
        instance_uuid=registration_config.get('instance_uuid'),
        metadata=dict(registration_config.get('metadata') or {}),
        client_cert=registration_config.get('client_cert'),
        client_key=registration_config.get('client_key'),
        ca_cert=registration_config.get('ca_cert'),
    )

    return listen_endpoint, registration_endpoint
