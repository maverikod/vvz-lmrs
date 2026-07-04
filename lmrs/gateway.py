"""Gateway contract objects for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


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


@dataclass(frozen=True)
class LocalProviderResponse:
    """Normalized LMRS response shape returned to the provider gateway.

    Attributes:
        request_id: Unique identifier of the originating request.
        status: Outcome status string (e.g. ``ok``, ``error``).
        message: Optional assistant reply as a ChatMessage.
        usage: Token usage counters returned by the model runtime.
        reason_code: Optional machine-readable code qualifying the status.
        runtime_metadata: Arbitrary metadata emitted by the model runtime.
        telemetry: Timing and instrumentation data for this response.
    """

    request_id: str
    status: str
    message: ChatMessage | None = None
    usage: Mapping[str, Any] = field(default_factory=dict)
    reason_code: str | None = None
    runtime_metadata: Mapping[str, Any] = field(default_factory=dict)
    telemetry: Mapping[str, Any] = field(default_factory=dict)


def normalize_messages(
    raw_messages: Sequence[Mapping[str, Any] | ChatMessage],
) -> tuple[ChatMessage, ...]:
    """Convert canonical gateway message payloads into immutable ChatMessage objects.

    Args:
        raw_messages: Sequence of raw message dicts or ChatMessage instances.
            Each dict must contain ``role`` (str) and ``content`` (str or list).
            Optional keys: ``name`` (str) and ``metadata`` (Mapping).

    Returns:
        Immutable tuple of ChatMessage objects in the same order as the input.

    Raises:
        ValueError: If any message dict has an invalid role, content, name, or
            metadata field.
    """

    messages: list[ChatMessage] = []
    for raw_message in raw_messages:
        if isinstance(raw_message, ChatMessage):
            messages.append(raw_message)
            continue
        role = raw_message.get("role")
        content = raw_message.get("content")
        if not isinstance(role, str) or not role:
            raise ValueError("message role must be a non-empty string")
        if not isinstance(content, (str, list)):
            raise ValueError("message content must be a string or list payload")
        name = raw_message.get("name")
        if name is not None and not isinstance(name, str):
            raise ValueError("message name must be a string when provided")
        metadata = raw_message.get("metadata", {})
        if not isinstance(metadata, Mapping):
            raise ValueError("message metadata must be a mapping")
        messages.append(
            ChatMessage(role=role, content=content, name=name, metadata=metadata)
        )
    return tuple(messages)


def validate_gateway_request(payload: Mapping[str, Any]) -> CanonicalChatRequest:
    """Validate and normalize a canonical gateway request payload for LMRS.

    Args:
        payload: Raw request mapping that must contain ``session_id``,
            ``request_id``, ``model_name`` (all non-empty strings) and
            ``messages`` (a non-empty sequence). Optional keys:
            ``tools`` (sequence of Mapping), ``reserved_output_tokens``
            (non-negative int), and ``options`` (Mapping).

    Returns:
        A fully validated and normalized CanonicalChatRequest instance.

    Raises:
        ValueError: If any required field is missing or of an incorrect type,
            if ``messages`` is empty or invalid, or if ``tools`` contains
            non-Mapping entries.
    """

    session_id = payload.get("session_id")
    request_id = payload.get("request_id")
    model_name = payload.get("model_name")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(request_id, str) or not request_id:
        raise ValueError("request_id must be a non-empty string")
    if not isinstance(model_name, str) or not model_name:
        raise ValueError("model_name must be a non-empty string")

    raw_messages = payload.get("messages")
    if not isinstance(raw_messages, Sequence) or isinstance(raw_messages, (str, bytes)):
        raise ValueError("messages must be a sequence")
    messages = normalize_messages(raw_messages)
    if not messages:
        raise ValueError("messages must not be empty")

    raw_tools = payload.get("tools", ())
    if not isinstance(raw_tools, Sequence) or isinstance(raw_tools, (str, bytes)):
        raise ValueError("tools must be a sequence when provided")
    tools = tuple(raw_tools)
    if any(not isinstance(tool, Mapping) for tool in tools):
        raise ValueError("tools entries must be mappings")

    reserved_output_tokens = payload.get("reserved_output_tokens", 0)
    if not isinstance(reserved_output_tokens, int) or reserved_output_tokens < 0:
        raise ValueError("reserved_output_tokens must be a non-negative integer")

    options = payload.get("options", {})
    if not isinstance(options, Mapping):
        raise ValueError("options must be a mapping")

    return CanonicalChatRequest(
        session_id=session_id,
        request_id=request_id,
        model_name=model_name,
        messages=messages,
        tools=tools,
        reserved_output_tokens=reserved_output_tokens,
        options=options,
    )
