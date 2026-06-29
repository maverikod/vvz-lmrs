"""Request queue contracts and scheduling functions for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class QueueEntry:
    """Queued request entry with precomputed estimates and queue state.

    Attributes:
        request_id: Unique identifier for the queued request.
        session_id: Gateway session identifier for this request.
        model_name: Name of the model for this request.
        required_tokens: Total required tokens for this request.
        required_dynamic_vram_bytes: Dynamic VRAM required in bytes.
        admitted_at: ISO timestamp when this entry was admitted to the queue.
        expires_at: ISO timestamp when this entry expires from the queue.
        priority: Scheduling priority (higher value means higher priority).
        status: Current queue status of this entry.
        metadata: Arbitrary metadata for this queue entry.
    """

    request_id: str
    session_id: str
    model_name: str
    required_tokens: int
    required_dynamic_vram_bytes: int
    admitted_at: str
    expires_at: str
    priority: int = 0
    status: str = "queued"
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestQueue:
    """Immutable-style queue owning a set of queued request entries.

    Attributes:
        entries: Tuple of currently queued request entries.
    """

    entries: tuple[QueueEntry, ...] = ()

    def add(self, entry: QueueEntry) -> RequestQueue:
        """Return a new RequestQueue with the entry added.

        Args:
            entry: The QueueEntry to add to the queue.

        Returns:
            A new RequestQueue with the entry appended.
        """
        return RequestQueue(entries=self.entries + (entry,))

    def cancel(self, request_id: str) -> RequestQueue:
        """Return a new RequestQueue with the specified entry removed.

        Args:
            request_id: Identifier of the request to cancel.

        Returns:
            A new RequestQueue with the matching entry removed.
        """
        return RequestQueue(
            entries=tuple(e for e in self.entries if e.request_id != request_id)
        )

    def snapshot(self) -> list[dict[str, object]]:
        """Return a serializable snapshot of all queued entries.

        Returns:
            A list of dictionaries representing each queued entry.
        """
        return [
            {
                "request_id": e.request_id,
                "session_id": e.session_id,
                "model_name": e.model_name,
                "required_tokens": e.required_tokens,
                "required_dynamic_vram_bytes": e.required_dynamic_vram_bytes,
                "admitted_at": e.admitted_at,
                "expires_at": e.expires_at,
                "priority": e.priority,
                "status": e.status,
            }
            for e in self.entries
        ]


def largest_fit_scheduler(
    entries: tuple[QueueEntry, ...],
    usable_dynamic_vram_bytes: int,
) -> QueueEntry | None:
    """Select the largest queued entry that fits current usable VRAM.

    Args:
        entries: Tuple of queued entries to consider for scheduling.
        usable_dynamic_vram_bytes: Current usable dynamic VRAM in bytes.

    Returns:
        The largest fitting QueueEntry by VRAM, or None if no entry fits.
    """
    candidates = [
        e
        for e in entries
        if e.status == "queued"
        and e.required_dynamic_vram_bytes <= usable_dynamic_vram_bytes
    ]
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda e: (e.required_dynamic_vram_bytes, e.priority, e.admitted_at),
    )


def launch_recheck(entry: QueueEntry, usable_dynamic_vram_bytes: int) -> bool:
    """Re-check current usable dynamic VRAM immediately before launch.

    Args:
        entry: The queued entry to check for launch eligibility.
        usable_dynamic_vram_bytes: Current usable dynamic VRAM in bytes.

    Returns:
        True if the entry still fits current usable VRAM, False otherwise.
    """
    return entry.required_dynamic_vram_bytes <= usable_dynamic_vram_bytes
