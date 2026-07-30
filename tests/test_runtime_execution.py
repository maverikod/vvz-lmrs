"""Tests for real runtime execution against the local vLLM server.

The runtime client is the only place an admitted request may reach a backend, so
these tests pin both directions: a successful completion is normalized into
backend-independent facts, and every failure becomes a structured signal instead
of an exception crossing the boundary.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any, Mapping

from lmrs.runtime_client import (
    NormalizedRuntimeResult,
    RuntimeBackend,
    RuntimeClient,
    RuntimeFailureSignal,
    VLLMOpenAIClient,
)

COMPLETION: Mapping[str, Any] = {
    "id": "cmpl-1",
    "model": "acme/tiny-model",
    "choices": [{"message": {"role": "assistant", "content": "ready"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 1, "total_tokens": 12},
}

REQUEST: Mapping[str, Any] = {
    "request_id": "req-1",
    "model_name": "acme/tiny-model",
    "messages": [{"role": "user", "content": "say ready"}],
    "options": {"max_tokens": 16},
}


class RecordingVllmClient(VLLMOpenAIClient):
    """A vLLM client that answers from a script and records what it was asked."""

    def __init__(self, payload: Mapping[str, Any] | None = None, error: Exception | None = None) -> None:
        """Build the double.

        Args:
            payload: Response body to return from a chat completion.
            error: Exception to raise instead of answering.
        """
        super().__init__(base_url="http://127.0.0.1:8000")
        object.__setattr__(self, "payload", payload)
        object.__setattr__(self, "error", error)
        object.__setattr__(self, "calls", [])

    def chat_completion(self, model_name: str, messages: object, **options: object) -> Mapping[str, object]:
        """Record the call and answer from the script."""
        self.calls.append({"model_name": model_name, "messages": messages, "options": options})  # type: ignore[attr-defined]
        if self.error is not None:  # type: ignore[attr-defined]
            raise self.error  # type: ignore[attr-defined]
        return dict(self.payload or {})  # type: ignore[attr-defined]


def test_vllm_backend_executes_and_normalizes_the_result() -> None:
    """A completion becomes assistant message, usage, metadata and telemetry."""
    client = RecordingVllmClient(COMPLETION)

    result = RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=client).execute(REQUEST, None)

    assert isinstance(result, NormalizedRuntimeResult)
    assert result.assistant_message == "ready"
    assert result.usage["total_tokens"] == 12
    assert result.runtime_metadata["finish_reason"] == "stop"
    assert result.runtime_metadata["model"] == "acme/tiny-model"
    assert "latency_ms" in result.telemetry
    assert client.calls[0]["options"] == {"max_tokens": 16}  # type: ignore[attr-defined]


def test_vllm_backend_reports_an_unreachable_runtime() -> None:
    """An unreachable runtime is a retriable structured failure."""
    client = RecordingVllmClient(error=RuntimeError("vLLM unavailable: connection refused"))

    result = RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=client).execute(REQUEST, None)

    assert isinstance(result, RuntimeFailureSignal)
    assert result.reason_code == "VLLM_UNAVAILABLE"
    assert result.retriable is True
    assert result.request_id == "req-1"


def test_vllm_backend_reports_a_rejected_call() -> None:
    """A runtime that answers with an error is a non-retriable failure."""
    client = RecordingVllmClient(error=RuntimeError("vLLM HTTP 400: unknown model"))

    result = RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=client).execute(REQUEST, None)

    assert isinstance(result, RuntimeFailureSignal)
    assert result.reason_code == "RUNTIME_CALL_FAILED"
    assert result.retriable is False


def test_a_request_without_messages_never_reaches_the_runtime() -> None:
    """A request carrying no messages is refused before the backend call."""
    client = RecordingVllmClient(COMPLETION)

    result = RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=client).execute(
        {"request_id": "req-2", "model_name": "acme/tiny-model"}, None
    )

    assert isinstance(result, RuntimeFailureSignal)
    assert result.reason_code == "RUNTIME_CALL_FAILED"
    assert client.calls == []  # type: ignore[attr-defined]


def test_the_profile_executor_still_wins_over_the_backend() -> None:
    """A profile-supplied executor is used instead of the configured backend."""
    client = RecordingVllmClient(COMPLETION)
    profile = {"executor": lambda request: {"assistant_message": "from-profile"}}

    result = RuntimeClient(backend=RuntimeBackend.VLLM, vllm_client=client).execute(REQUEST, profile)

    assert isinstance(result, NormalizedRuntimeResult)
    assert result.assistant_message == "from-profile"
    assert client.calls == []  # type: ignore[attr-defined]


def test_without_an_executor_or_a_backend_client_the_client_says_so() -> None:
    """No execution path at all is reported, not simulated."""
    result = RuntimeClient(backend=RuntimeBackend.VLLM).execute(REQUEST, None)

    assert isinstance(result, RuntimeFailureSignal)
    assert result.reason_code == "RUNTIME_EXECUTOR_UNAVAILABLE"


def test_prompt_token_count_uses_the_runtime_tokenizer(monkeypatch) -> None:
    """The token count comes from the runtime that will execute the request."""
    client = VLLMOpenAIClient(base_url="http://127.0.0.1:8000")
    monkeypatch.setattr(
        VLLMOpenAIClient,
        "_json_request",
        lambda self, method, path, payload=None: {"count": 7},
    )

    assert client.count_prompt_tokens("acme/tiny-model", [{"role": "user", "content": "hi"}]) == 7


def test_prompt_token_count_reports_no_answer(monkeypatch) -> None:
    """A runtime that cannot tokenize yields None, so the caller can fall back."""
    client = VLLMOpenAIClient(base_url="http://127.0.0.1:8000")

    def unreachable(self: Any, method: str, path: str, payload: Any = None) -> Mapping[str, Any]:
        raise RuntimeError("vLLM unavailable: connection refused")

    monkeypatch.setattr(VLLMOpenAIClient, "_json_request", unreachable)

    assert client.count_prompt_tokens("acme/tiny-model", []) is None
