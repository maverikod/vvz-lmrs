"""Tests pinning the client and server command surfaces to each other (C-052).

The client must have exactly one method per public server command. This test
fails with an explicit list of the offenders whenever a command gains no client
method, or a client method survives a command's removal, so the two surfaces
cannot drift apart silently.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import sys
from pathlib import Path

_CLIENT_ROOT = Path(__file__).resolve().parent.parent / "client"
if str(_CLIENT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CLIENT_ROOT))

from lmrs_client.client import LmrsClient  # noqa: E402  (path set up above)

from lmrs.adapter.registration import register_lmrs_commands  # noqa: E402


class _NameCollectingRegistry:
    """Registry double that only records the names offered to it."""

    def __init__(self) -> None:
        """Start with nothing registered."""
        self.names: set[str] = set()

    def register(self, command_class: type, category: str) -> None:
        """Record one command name.

        Args:
            command_class: The command class being registered.
            category: Adapter category, always "custom" for LMRS.
        """
        self.names.add(str(getattr(command_class, "name", "")))


# The mapping is intentionally an identity table: client method names mirror
# server command names exactly, so a reader can map either direction without
# consulting a translation layer. The table exists so a deliberate rename is a
# visible edit here rather than a silent divergence.
SERVER_COMMAND_TO_CLIENT_METHOD: dict[str, str] = {
    "healthcheck": "healthcheck",
    "model_status": "model_status",
    "capacity": "capacity",
    "token_count": "token_count",
    "estimate": "estimate",
    "chat": "chat",
    "queue_status": "queue_status",
    "cancel": "cancel",
    "local_model_cache_preload": "local_model_cache_preload",
    "local_model_cache_status": "local_model_cache_status",
    "local_model_cache_delete": "local_model_cache_delete",
    "local_model_load": "local_model_load",
    "local_model_unload": "local_model_unload",
    "local_model_reload": "local_model_reload",
    "info": "info",
    "local_lmcache_status": "local_lmcache_status",
    "local_lmcache_purge": "local_lmcache_purge",
    "local_model_switch": "local_model_switch",
}


def _server_command_names() -> set[str]:
    """Return the command names the live registration hook offers.

    Returns:
        Every name register_lmrs_commands registers.
    """
    registry = _NameCollectingRegistry()
    register_lmrs_commands(registry)
    return registry.names


def _client_method_names() -> set[str]:
    """Return the public method names LmrsClient exposes.

    Returns:
        Public callables of LmrsClient, excluding dunder and private helpers.
    """
    return {
        name
        for name in dir(LmrsClient)
        if not name.startswith("_") and callable(getattr(LmrsClient, name))
    }


def test_every_server_command_has_exactly_one_client_method() -> None:
    """No server command may lack a client method."""
    server = _server_command_names()
    client = _client_method_names()

    missing = sorted(
        command
        for command in server
        if SERVER_COMMAND_TO_CLIENT_METHOD.get(command) not in client
    )

    assert not missing, f"server commands with no client method: {missing}"


def test_no_client_method_lacks_a_server_command() -> None:
    """No client method may exist without a matching server command."""
    server = _server_command_names()
    client = _client_method_names()
    mapped = {
        method
        for command, method in SERVER_COMMAND_TO_CLIENT_METHOD.items()
        if command in server
    }

    orphans = sorted(client - mapped)

    assert not orphans, f"client methods with no server command: {orphans}"


def test_the_mapping_table_matches_the_live_server_surface() -> None:
    """The table itself may not drift from the commands the server registers."""
    server = _server_command_names()
    tabled = set(SERVER_COMMAND_TO_CLIENT_METHOD)

    assert tabled == server, {
        "in table but not registered": sorted(tabled - server),
        "registered but not in table": sorted(server - tabled),
    }


def test_the_surface_is_eighteen_commands_on_both_sides() -> None:
    """Both surfaces carry the same eighteen commands."""
    assert len(_server_command_names()) == 18
    assert len(_client_method_names()) == 18
