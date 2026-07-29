"""Named-check registry: the contract every pipeline check is built on.

This module holds no check implementations, no argument parsing, and no
subprocess calls; it only defines the ``Check`` record, the ``CheckRegistry``
container, and the single module-level ``REGISTRY`` all checks register into.
Registration order is the execution order of a full run, so it is preserved
rather than sorted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class Check:
    """One named verification check.

    Attributes:
        name: Stable check name used on the command line.
        description: One-line description shown by ``pipeline --list``.
        run: Zero-argument callable returning an integer exit status, 0 for pass.
    """

    name: str
    description: str
    run: Callable[[], int]


class CheckRegistry:
    """Ordered collection of ``Check`` records keyed by their unique names."""

    def __init__(self) -> None:
        """Create an empty registry preserving registration order."""
        self._checks: dict[str, Check] = {}

    def register(self, check: Check) -> None:
        """Add a check, refusing a duplicate name.

        Args:
            check: The check to register.

        Raises:
            ValueError: If a check with the same name is already registered.
        """
        if check.name in self._checks:
            raise ValueError(f"check name already registered: {check.name!r}")
        self._checks[check.name] = check

    def names(self) -> list[str]:
        """Return the registered check names in registration order.

        Returns:
            list[str]: Check names, oldest registration first.
        """
        return list(self._checks)

    def get(self, name: str) -> Check:
        """Return one check by name.

        Args:
            name: Name of the check to look up.

        Returns:
            Check: The registered check.

        Raises:
            KeyError: If no check with that name is registered.
        """
        try:
            return self._checks[name]
        except KeyError:
            raise KeyError(f"unknown check: {name!r}") from None

    def all(self) -> list[Check]:
        """Return every registered check in registration order.

        Returns:
            list[Check]: All checks, oldest registration first.
        """
        return list(self._checks.values())


REGISTRY = CheckRegistry()
