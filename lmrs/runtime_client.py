"""Runtime execution contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


class RuntimeBackend:
    """Local runtime backend identifiers.

    Names the execution backend abstraction that runs only requests already
    admitted by LMRS. A runtime backend makes no admission decisions.

    Attributes:
        VLLM: vLLM local runtime backend.
        OLLAMA: Ollama local runtime backend.
        LLAMA_CPP: llama.cpp local runtime backend.
        GPT_OSS: gpt-oss local runtime backend.
        OTHER_LOCAL: Any other local runtime backend.
    """

    VLLM: str = "vllm"
    OLLAMA: str = "ollama"
    LLAMA_CPP: str = "llama_cpp"
    GPT_OSS: str = "gpt_oss"
    OTHER_LOCAL: str = "other_local"


@dataclass(frozen=True)
class NormalizedRuntimeResult:
    """Backend-independent normalized result of local model execution.

    Suitable for normalization back into a canonical provider response.

    Attributes:
        assistant_message: The assistant message produced by the model.
        usage: Token-usage counters for the execution.
        status: Outcome status string for the execution.
        runtime_metadata: Backend-internal metadata from the runtime.
        telemetry: Timing and tracing data for observability.
        metadata: Arbitrary metadata about this result.
    """

    assistant_message: object | None = None
    usage: Mapping[str, object] = field(default_factory=dict)
    status: str = "ok"
    runtime_metadata: Mapping[str, object] = field(default_factory=dict)
    telemetry: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimeFailureSignal:
    """Structured runtime failure signal consumed by the error contract.

    Attributes:
        request_id: Identifier of the request that failed.
        backend: Runtime backend that produced the failure.
        reason_code: Stable machine-readable failure reason.
        message: Human-readable failure description.
        retriable: Whether the failed operation may be retried.
        metadata: Arbitrary metadata about the failure.
    """

    request_id: str
    backend: str
    reason_code: str
    message: str
    retriable: bool = False
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass
class RuntimeClient:
    """Execution client that hides runtime backend differences.

    Accepts already-admitted requests and runs them against the selected
    backend. It makes no admission decisions and never re-runs admission, and
    it does not generate public command metadata.

    Attributes:
        backend: The runtime backend identifier this client executes against.
    """

    backend: str

    def execute(
        self,
        admitted_request: object,
        runtime_profile: object,
    ) -> NormalizedRuntimeResult | RuntimeFailureSignal:
        """Execute an already-admitted request against the selected backend.

        Args:
            admitted_request: A request already admitted by LMRS admission.
            runtime_profile: The runtime profile describing backend settings.

        Returns:
            A NormalizedRuntimeResult on success or a RuntimeFailureSignal on
            failure.
        """
        raise NotImplementedError("RuntimeClient.execute is a contract stub")
