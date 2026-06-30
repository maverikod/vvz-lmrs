"""LMRS server configuration generator.

Builds a complete LMRS adapter server configuration on top of the
``mcp_proxy_adapter`` configuration toolkit (``SimpleConfigGenerator`` and
``ConfigLoader``), then injects the LMRS-specific sections.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from mcp_proxy_adapter.core.config.config_loader import ConfigLoader
from mcp_proxy_adapter.core.config.simple_config_generator import (
    SimpleConfigGenerator,
)

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8012
DEFAULT_PROTOCOL = "https"
DEFAULT_LOG_DIR = "/var/log/lmrs"
DEFAULT_CERT_DIR = "/etc/lmrs/certs"
COMMANDS_DIRECTORY = "lmrs"
DEFAULT_SERVER_NAME = "Local Model Runtime Service"


class LmrsConfigGenerator:
    """Generate an LMRS adapter server configuration document.

    Thin wrapper over :class:`SimpleConfigGenerator` that applies LMRS
    defaults and merges LMRS-specific sections into the generated JSON.

    Attributes:
        base: Underlying adapter configuration generator.
    """

    def __init__(self) -> None:
        """Initialize the generator with the adapter base generator."""
        self.base = SimpleConfigGenerator()

    def generate(
        self,
        out_path: str,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        protocol: str = DEFAULT_PROTOCOL,
        cert_file: Optional[str] = None,
        key_file: Optional[str] = None,
        ca_cert_file: Optional[str] = None,
        log_dir: str = DEFAULT_LOG_DIR,
        with_proxy: bool = False,
        registration_host: Optional[str] = None,
        registration_port: Optional[int] = None,
        registration_protocol: Optional[str] = None,
        server_id: Optional[str] = None,
        server_name: str = DEFAULT_SERVER_NAME,
    ) -> str:
        """Generate the configuration and write it to ``out_path``.

        Args:
            out_path: Destination path for the JSON configuration file.
            host: Bind address for the server listener.
            port: TCP port for the server listener.
            protocol: Transport protocol (``http``, ``https`` or ``mtls``).
            cert_file: Server certificate path (https/mtls only).
            key_file: Server private key path (https/mtls only).
            ca_cert_file: CA certificate path used to verify peers.
            log_dir: Directory for server log files.
            with_proxy: Whether to enable proxy registration.
            registration_host: Proxy host for registration.
            registration_port: Proxy port for registration.
            registration_protocol: Protocol used to reach the proxy.
            server_id: Stable identifier advertised to the proxy.
            server_name: Human-readable server name.

        Returns:
            The path the configuration was written to.
        """
        certs = _resolve_cert_paths(protocol, cert_file, key_file, ca_cert_file)
        self.base.generate(
            protocol=protocol,
            with_proxy=with_proxy,
            out_path=out_path,
            server_host=host,
            server_port=port,
            server_cert_file=certs["cert"],
            server_key_file=certs["key"],
            server_ca_cert_file=certs["ca"],
            server_log_level="INFO",
            server_log_dir=log_dir,
            registration_host=registration_host,
            registration_port=registration_port,
            registration_protocol=registration_protocol,
            registration_server_id=server_id,
            registration_server_name=server_name if with_proxy else None,
        )
        document: Dict[str, Any] = ConfigLoader().load_from_file(out_path)
        _inject_lmrs_sections(document, log_dir=log_dir)
        Path(out_path).write_text(
            json.dumps(document, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return out_path


def _resolve_cert_paths(
    protocol: str,
    cert_file: Optional[str],
    key_file: Optional[str],
    ca_cert_file: Optional[str],
) -> Dict[str, Optional[str]]:
    """Resolve certificate paths, applying defaults for TLS protocols.

    Args:
        protocol: Transport protocol.
        cert_file: Explicit server certificate path, if any.
        key_file: Explicit server key path, if any.
        ca_cert_file: Explicit CA certificate path, if any.

    Returns:
        Mapping with ``cert``, ``key`` and ``ca`` entries; values may be
        ``None`` for plain HTTP.
    """
    if protocol not in ("https", "mtls"):
        return {"cert": None, "key": None, "ca": None}
    return {
        "cert": cert_file or f"{DEFAULT_CERT_DIR}/lmrs.crt",
        "key": key_file or f"{DEFAULT_CERT_DIR}/lmrs.key",
        "ca": ca_cert_file or f"{DEFAULT_CERT_DIR}/ca.crt",
    }


def _inject_lmrs_sections(document: Dict[str, Any], *, log_dir: str) -> None:
    """Merge LMRS-specific sections into a configuration document.

    Args:
        document: Parsed configuration document to mutate in place.
        log_dir: Directory for server log files.

    Returns:
        None. The document is mutated in place.
    """
    commands = document.setdefault("commands", {})
    commands.update(
        {
            "enabled": True,
            "auto_discover": True,
            "commands_directory": COMMANDS_DIRECTORY,
            "custom_commands_path": COMMANDS_DIRECTORY,
        }
    )
    logging_section = document.setdefault("logging", {})
    logging_section.setdefault("level", "INFO")
    logging_section.setdefault("log_dir", log_dir)
    logging_section.setdefault("file_output", True)
