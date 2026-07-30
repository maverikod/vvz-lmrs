"""LMRS client library.

Gives external consumers full access to the LMRS public command surface with
transport, protocol selection, connection management, serialization and error
mapping encapsulated by the ``mcp_proxy_adapter`` framework client.
"""

from lmrs_client.client import LmrsClient

__all__ = ["LmrsClient"]
