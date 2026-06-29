"""Token breakdown and capacity estimation for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class TokenBreakdown:
    """Tokenizer-aware preflight token accounting for a gateway request.

    Attributes:
        input_tokens: Number of tokens in the input prompt.
        tool_tokens: Number of tokens consumed by tool definitions.
        service_tokens: Number of tokens consumed by system service instructions.
        reserved_output_tokens: Number of output tokens reserved for generation.
        tokenizer_name: Name of the tokenizer used for counting.
        tokenizer_accuracy: Accuracy descriptor for the tokenizer estimate.
        rough_estimate: Whether this is a rough estimate rather than exact count.
        metadata: Arbitrary metadata for this breakdown.
    """

    input_tokens: int
    tool_tokens: int
    service_tokens: int
    reserved_output_tokens: int
    tokenizer_name: str
    tokenizer_accuracy: str
    rough_estimate: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RequestCapacityEstimate:
    """Preflight capacity estimate for a gateway request.

    Attributes:
        request_id: Unique identifier for the request.
        model_name: Name of the model this estimate is for.
        token_breakdown: Detailed token accounting for this request.
        required_tokens: Total required tokens computed from breakdown.
        required_dynamic_vram_bytes: Dynamic VRAM required for this request.
        usable_dynamic_vram_bytes: Currently usable dynamic VRAM at estimation time.
        declared_context_window: Declared context window size for the model.
        kv_bytes_per_token: KV-cache bytes per token used in VRAM estimate.
        per_request_overhead_bytes: Per-request memory overhead in bytes.
        runtime_batch_overhead_bytes: Batch-level runtime memory overhead in bytes.
        metadata: Arbitrary metadata for this estimate.
    """

    request_id: str
    model_name: str
    token_breakdown: TokenBreakdown
    required_tokens: int
    required_dynamic_vram_bytes: int
    usable_dynamic_vram_bytes: int
    declared_context_window: int
    kv_bytes_per_token: int
    per_request_overhead_bytes: int
    runtime_batch_overhead_bytes: int
    metadata: Mapping[str, object] = field(default_factory=dict)


def calculate_required_tokens(breakdown: TokenBreakdown) -> int:
    """Sum all token components and return total required tokens.

    Args:
        breakdown: Tokenizer-aware token breakdown for the request.

    Returns:
        Total required tokens as the sum of all breakdown components.
    """
    if breakdown.input_tokens < 0:
        raise ValueError("input_tokens must be non-negative")
    if breakdown.tool_tokens < 0:
        raise ValueError("tool_tokens must be non-negative")
    if breakdown.service_tokens < 0:
        raise ValueError("service_tokens must be non-negative")
    if breakdown.reserved_output_tokens < 0:
        raise ValueError("reserved_output_tokens must be non-negative")
    return (
        breakdown.input_tokens
        + breakdown.tool_tokens
        + breakdown.service_tokens
        + breakdown.reserved_output_tokens
    )


def calculate_dynamic_vram_requirement(
    required_tokens: int,
    kv_bytes_per_token: int,
    per_request_overhead_bytes: int,
    runtime_batch_overhead_bytes: int,
) -> int:
    """Compute dynamic VRAM requirement for a request.

    Args:
        required_tokens: Total required tokens for the request.
        kv_bytes_per_token: KV-cache bytes per token.
        per_request_overhead_bytes: Per-request memory overhead.
        runtime_batch_overhead_bytes: Batch-level runtime memory overhead.

    Returns:
        Total dynamic VRAM bytes required for this request.
    """
    if required_tokens < 0:
        raise ValueError("required_tokens must be non-negative")
    if kv_bytes_per_token < 0:
        raise ValueError("kv_bytes_per_token must be non-negative")
    if per_request_overhead_bytes < 0:
        raise ValueError("per_request_overhead_bytes must be non-negative")
    if runtime_batch_overhead_bytes < 0:
        raise ValueError("runtime_batch_overhead_bytes must be non-negative")
    return (
        required_tokens * kv_bytes_per_token
        + per_request_overhead_bytes
        + runtime_batch_overhead_bytes
    )


def estimate_request_capacity(
    request_id: str,
    model_name: str,
    token_breakdown: TokenBreakdown,
    declared_context_window: int,
    usable_dynamic_vram_bytes: int,
    kv_bytes_per_token: int,
    per_request_overhead_bytes: int,
    runtime_batch_overhead_bytes: int,
) -> RequestCapacityEstimate:
    """Build a RequestCapacityEstimate from token breakdown and memory facts.

    Args:
        request_id: Unique identifier for the request.
        model_name: Name of the model being estimated.
        token_breakdown: Detailed token accounting for the request.
        declared_context_window: Declared context window size for the model.
        usable_dynamic_vram_bytes: Currently usable dynamic VRAM at estimation time.
        kv_bytes_per_token: KV-cache bytes per token.
        per_request_overhead_bytes: Per-request memory overhead in bytes.
        runtime_batch_overhead_bytes: Batch-level runtime overhead in bytes.

    Returns:
        A RequestCapacityEstimate with computed token and VRAM requirements.
    """
    required_tokens = calculate_required_tokens(token_breakdown)
    required_dynamic_vram_bytes = calculate_dynamic_vram_requirement(
        required_tokens=required_tokens,
        kv_bytes_per_token=kv_bytes_per_token,
        per_request_overhead_bytes=per_request_overhead_bytes,
        runtime_batch_overhead_bytes=runtime_batch_overhead_bytes,
    )
    return RequestCapacityEstimate(
        request_id=request_id,
        model_name=model_name,
        token_breakdown=token_breakdown,
        required_tokens=required_tokens,
        required_dynamic_vram_bytes=required_dynamic_vram_bytes,
        usable_dynamic_vram_bytes=usable_dynamic_vram_bytes,
        declared_context_window=declared_context_window,
        kv_bytes_per_token=kv_bytes_per_token,
        per_request_overhead_bytes=per_request_overhead_bytes,
        runtime_batch_overhead_bytes=runtime_batch_overhead_bytes,
    )
