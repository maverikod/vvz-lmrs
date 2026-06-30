"""Adapter command registration and thin command wrappers for LMRS.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any, Mapping


class ThinAdapterCommand:
    """Base contract for adapter-facing LMRS command wrappers.

    A concrete subclass inherits the adapter Command base, exposes a stable
    name, may set use_queue for long-running work, validates incoming
    parameters, delegates to an LMRS domain executor or service, and returns an
    adapter success or error result. Subclasses must not embed admission,
    tokenizer, VRAM, or model-lifecycle business logic; they only translate
    adapter calls into domain-service execution per the Adapter-Based MCP
    Interface.

    Attributes:
        name: Stable public command name exposed through the adapter.
        use_queue: Whether the command runs as long-running queued work.
    """

    name: str = ""
    use_queue: bool = False

    def validate(self, params: Mapping[str, Any]) -> None:
        """Validate adapter request parameters before delegation.

        Args:
            params: Raw adapter request parameters for this command.

        Returns:
            None. A concrete subclass raises on invalid parameters.
        """
        raise NotImplementedError("ThinAdapterCommand.validate is a contract stub")

    def execute(self, params: Mapping[str, Any]) -> object:
        """Delegate to the LMRS domain executor and return an adapter result.

        Args:
            params: Validated adapter request parameters for this command.

        Returns:
            An adapter success or error result from the domain service.
        """
        raise NotImplementedError("ThinAdapterCommand.execute is a contract stub")


def register_lmrs_commands(registry: object) -> None:
    """Idempotently register LMRS public command classes with the adapter.

    Each LMRS public command class is registered as a custom adapter command
    via registry.register(CommandClass, \"custom\"). Before registering a command
    the function checks whether it is already registered and skips it when so.

    Args:
        registry: The adapter command registry to register commands into.

    Returns:
        None.
    """
    raise NotImplementedError("register_lmrs_commands is a contract stub")


def register_custom_commands_hook() -> None:
    """Install LMRS command registration into the adapter startup sequence.

    Wires register_lmrs_commands into adapter startup so all LMRS public command
    classes are registered before the adapter server starts. Registration stays
    idempotent through register_lmrs_commands.

    Returns:
        None.
    """
    raise NotImplementedError("register_custom_commands_hook is a contract stub")
