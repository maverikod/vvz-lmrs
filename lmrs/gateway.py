"""Gateway contract objects for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class ChatMessage:
    """One role-tagged message from a canonical gateway chat request.

    Attributes:
        role: Role of the message sender (e.g., "user", "assistant", "system").
        content: Text content or list of structured content blocks.
        name: Optional display name for the message sender.
        metadata: Arbitrary metadata attached to this message.
    """

    role: str
    content: str | list[Mapping[str, Any]]
    name: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalChatRequest:
    """Gateway-owned request accepted by LMRS for local model routing.

    Attributes:
        session_id: Gateway session identifier for this request.
        request_id: Unique identifier for this specific request.
        model_name: Name of the local model to route the request to.
        messages: Ordered sequence of chat messages in this request.
        tools: Tool definitions available for this request.
        reserved_output_tokens: Number of output tokens to reserve.
        options: Additional routing or inference options.
    """

    session_id: str
    request_id: str
    model_name: str
    messages: tuple[ChatMessage, ...]
    tools: tuple[Mapping[str, Any], ...] = ()
    reserved_output_tokens: int = 0
    options: Mapping[str, Any] = field(default_factory=dict)
