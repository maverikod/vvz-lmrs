"""LMRS server configuration validator.

Validates an LMRS adapter server configuration using the
``mcp_proxy_adapter`` configuration toolkit (``SimpleConfig`` and
``SimpleConfigValidator``) and adds LMRS-specific checks.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from mcp_proxy_adapter.core.config.config_loader import ConfigLoader
from mcp_proxy_adapter.core.config.simple_config import SimpleConfig
from mcp_proxy_adapter.core.config.simple_config_validator import (
    SimpleConfigValidator,
)


class LmrsConfigValidator:
    """Validate an LMRS adapter server configuration file.

    Combines the adapter's :class:`SimpleConfigValidator` with
    LMRS-specific checks (presence of TLS material for secure protocols).
    """

    def validate(self, config_path: str) -> List[str]:
        """Validate the configuration at ``config_path``.

        Args:
            config_path: Path to the JSON configuration file.

        Returns:
            A list of human-readable error messages; empty when valid.
        """
        errors: List[str] = []
        model = SimpleConfig(config_path=config_path).load()
        base = SimpleConfigValidator(config_path=config_path)
        errors.extend(error.message for error in base.validate(model))
        errors.extend(_check_tls_material(config_path))
        return errors


def _check_tls_material(config_path: str) -> List[str]:
    """Check that TLS certificate files referenced by the config exist.

    Args:
        config_path: Path to the JSON configuration file.

    Returns:
        A list of error messages for missing or undefined certificate
        files; empty when the protocol is plain HTTP or all files exist.
    """
    document: Dict[str, Any] = ConfigLoader().load_from_file(config_path)
    server = document.get("server", {})
    protocol = str(server.get("protocol", "http"))
    if protocol not in ("https", "mtls"):
        return []
    ssl_section = server.get("ssl") or {}
    errors: List[str] = []
    for field_name in ("cert", "key"):
        value = ssl_section.get(field_name)
        if not value:
            errors.append(
                f"server.ssl.{field_name} is required for protocol {protocol}"
            )
        elif not Path(str(value)).is_file():
            errors.append(f"server.ssl.{field_name} file not found: {value}")
    return errors
