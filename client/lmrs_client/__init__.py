"""LMRS client library.

Gives external consumers full access to the LMRS public command surface with
transport, protocol selection, connection management, serialization and error
mapping encapsulated by the ``mcp_proxy_adapter`` framework client.

``LmrsClient`` is exported lazily (PEP 562): importing the framework prints a
banner and configures stdout logging as a side effect, and the package must be
importable - by the CLI, whose stdout is machine-readable JSON - without that
side effect firing before the CLI can contain it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["LmrsClient"]


def __getattr__(name: str) -> Any:
    """Resolve the lazy export.

    Args:
        name: Attribute requested from the package.

    Returns:
        The resolved attribute.

    Raises:
        AttributeError: For any name this package does not export.
    """
    if name == "LmrsClient":
        from lmrs_client.client import LmrsClient

        return LmrsClient
    raise AttributeError(f"module 'lmrs_client' has no attribute {name!r}")
