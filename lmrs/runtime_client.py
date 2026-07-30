"""Runtime execution contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Mapping


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


@dataclass(frozen=True)
class VLLMModelProbeResult:
    """Result of probing a vLLM OpenAI-compatible model server."""

    ok: bool
    served_models: tuple[str, ...] = ()
    error: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class VLLMOpenAIClient:
    """Small stdlib client for the vLLM OpenAI-compatible HTTP API.

    LMRS uses this client to prove that the configured in-container vLLM server
    is actually reachable and serving the requested model. It is deliberately
    narrow: lifecycle code needs `/v1/models`, and execution can use
    `/v1/chat/completions` without pulling in another HTTP dependency.
    """

    base_url: str = "http://127.0.0.1:8000"
    timeout_seconds: float = 10.0

    def _url(self, path: str) -> str:
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _json_request(self, method: str, path: str, payload: Mapping[str, object] | None = None) -> Mapping[str, object]:
        import json
        import urllib.error
        import urllib.request

        data = None if payload is None else json.dumps(dict(payload)).encode("utf-8")
        request = urllib.request.Request(
            self._url(path),
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"vLLM HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"vLLM unavailable: {exc.reason}") from exc
        loaded = json.loads(body or "{}")
        if not isinstance(loaded, Mapping):
            raise RuntimeError("vLLM returned a non-object JSON response")
        return loaded

    def list_models(self) -> VLLMModelProbeResult:
        """Return models reported by vLLM `/v1/models`."""
        try:
            payload = self._json_request("GET", "/v1/models")
        except Exception as exc:
            return VLLMModelProbeResult(False, error=str(exc), metadata={"base_url": self.base_url})
        data = payload.get("data", ())
        models: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                    models.append(str(item["id"]))
        return VLLMModelProbeResult(True, tuple(models), metadata={"base_url": self.base_url})

    def is_model_served(self, model_name: str) -> VLLMModelProbeResult:
        """Check whether vLLM currently serves `model_name`."""
        result = self.list_models()
        if not result.ok:
            return result
        return VLLMModelProbeResult(
            model_name in result.served_models,
            result.served_models,
            None if model_name in result.served_models else "model is not reported by vLLM /v1/models",
            result.metadata,
        )

    def chat_completion(self, model_name: str, messages: object, **options: object) -> Mapping[str, object]:
        """Call vLLM `/v1/chat/completions` for a real inference request."""
        payload: dict[str, object] = {"model": model_name, "messages": messages}
        payload.update(options)
        return self._json_request("POST", "/v1/chat/completions", payload)

    def count_prompt_tokens(self, model_name: str, messages: object) -> int | None:
        """Return the token count vLLM reports for a chat prompt.

        Uses the runtime's own `/tokenize` endpoint, which applies the model's
        chat template and its real tokenizer. A count produced anywhere else
        would be a different tokenizer than the one that executes the request.

        Args:
            model_name: Model whose tokenizer and chat template apply.
            messages: Chat messages in OpenAI form.

        Returns:
            The prompt token count, or None when the runtime does not answer.
        """
        try:
            payload = self._json_request("POST", "/tokenize", {"model": model_name, "messages": messages})
        except Exception:
            return None
        count = payload.get("count")
        if isinstance(count, int):
            return count
        tokens = payload.get("tokens")
        return len(tokens) if isinstance(tokens, list) else None


def _normalize_vllm_payload(
    payload: Mapping[str, object],
    model_name: str,
    latency_ms: float,
    base_url: str,
) -> NormalizedRuntimeResult:
    """Convert a vLLM chat completion into the backend-independent result.

    Args:
        payload: The raw `/v1/chat/completions` response body.
        model_name: Model the request named.
        latency_ms: Wall-clock duration of the call in milliseconds.
        base_url: Base URL of the runtime that answered.

    Returns:
        A NormalizedRuntimeResult carrying the assistant message, usage
        counters, runtime metadata and call telemetry.
    """
    choices = payload.get("choices")
    first: Mapping[str, Any] = {}
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        first = choices[0]
    message = first.get("message")
    assistant_message: object | None = None
    if isinstance(message, Mapping):
        assistant_message = message.get("content")
    usage = payload.get("usage")
    return NormalizedRuntimeResult(
        assistant_message=assistant_message,
        usage=dict(usage) if isinstance(usage, Mapping) else {},
        status="ok",
        runtime_metadata={
            "model": payload.get("model", model_name),
            "response_id": payload.get("id"),
            "finish_reason": first.get("finish_reason"),
            "base_url": base_url,
        },
        telemetry={"latency_ms": round(latency_ms, 3)},
        metadata={"role": message.get("role") if isinstance(message, Mapping) else None},
    )


def _request_value(admitted_request: object, *names: str) -> object | None:
    """Return the first present field of an admitted request.

    An admitted request reaches the runtime either as an object or as a
    mapping, depending on which caller admitted it; both are read here so the
    runtime does not force one shape on the layers above it.

    Args:
        admitted_request: The already-admitted request.
        *names: Field names to try, in order.

    Returns:
        The first value found, or None.
    """
    for name in names:
        if isinstance(admitted_request, Mapping):
            value = admitted_request.get(name)
        else:
            value = getattr(admitted_request, name, None)
        if value is not None:
            return value
    return None


def _chat_messages(admitted_request: object) -> list[dict[str, object]]:
    """Return the admitted request's messages in OpenAI chat form.

    Args:
        admitted_request: The already-admitted request.

    Returns:
        Role-tagged message dicts; empty when the request carries none.
    """
    raw = _request_value(admitted_request, "messages")
    if raw is None:
        return []
    messages: list[dict[str, object]] = []
    for item in raw if isinstance(raw, (list, tuple)) else [raw]:
        if isinstance(item, Mapping):
            role = item.get("role")
            content = item.get("content")
        else:
            role = getattr(item, "role", None)
            content = getattr(item, "content", None)
        if isinstance(role, str) and content is not None:
            messages.append({"role": role, "content": content})
    return messages


@dataclass
class RuntimeClient:
    """Execution client that hides runtime backend differences.

    Accepts already-admitted requests and runs them against the selected
    backend. It makes no admission decisions and never re-runs admission, and
    it does not generate public command metadata.

    Two execution paths exist and are tried in that order: a runtime profile
    that carries its own executor uses it, which is how a caller injects a
    backend LMRS does not know; otherwise the client executes against the
    backend it was configured with. Without either it reports
    RUNTIME_EXECUTOR_UNAVAILABLE instead of pretending to run.

    Attributes:
        backend: The runtime backend identifier this client executes against.
        vllm_client: Client for the local vLLM OpenAI-compatible API, used when
            the backend is vLLM and the profile supplies no executor.
        default_options: Inference options applied to every backend call unless
            the request overrides them.
    """

    backend: str
    vllm_client: VLLMOpenAIClient | None = None
    default_options: Mapping[str, object] = field(default_factory=dict)

    def _vllm_options(self, admitted_request: object) -> dict[str, object]:
        """Return the inference options for one vLLM call.

        Args:
            admitted_request: The already-admitted request.

        Returns:
            Options merged from the client defaults and the request.
        """
        options: dict[str, object] = dict(self.default_options)
        request_options = _request_value(admitted_request, "options")
        if isinstance(request_options, Mapping):
            options.update({str(key): value for key, value in request_options.items()})
        for name in ("max_tokens", "temperature", "top_p", "stop"):
            value = _request_value(admitted_request, name)
            if value is not None:
                options[name] = value
        return options

    def _execute_vllm(self, request_id: str, admitted_request: object) -> NormalizedRuntimeResult | RuntimeFailureSignal:
        """Run one admitted request against the local vLLM server.

        Args:
            request_id: Identifier of the request being executed.
            admitted_request: The already-admitted request.

        Returns:
            A NormalizedRuntimeResult on success or a RuntimeFailureSignal when
            the runtime is unreachable, rejects the call, or the request does
            not name a model.
        """
        client = self.vllm_client
        if client is None:
            return RuntimeFailureSignal(request_id, self.backend, "RUNTIME_EXECUTOR_UNAVAILABLE", "no vLLM client is configured")
        model_name = _request_value(admitted_request, "model_name", "model")
        if not isinstance(model_name, str) or not model_name:
            return RuntimeFailureSignal(request_id, self.backend, "RUNTIME_CALL_FAILED", "the admitted request does not name a model")
        messages = _chat_messages(admitted_request)
        if not messages:
            return RuntimeFailureSignal(request_id, self.backend, "RUNTIME_CALL_FAILED", "the admitted request carries no messages")
        started = time.monotonic()
        try:
            payload = client.chat_completion(model_name, messages, **self._vllm_options(admitted_request))
        except Exception as error:  # noqa: BLE001 - a runtime failure is signalled, never raised
            text = str(error)
            unavailable = "unavailable" in text
            return RuntimeFailureSignal(
                request_id,
                self.backend,
                "VLLM_UNAVAILABLE" if unavailable else "RUNTIME_CALL_FAILED",
                text,
                unavailable,
                {"exception_type": type(error).__name__, "base_url": client.base_url, "model_name": model_name},
            )
        latency_ms = (time.monotonic() - started) * 1000.0
        return _normalize_vllm_payload(payload, model_name, latency_ms, client.base_url)

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
        request_id = str(_request_value(admitted_request, "request_id", "id") or "unknown")
        executor = None
        for attr_name in ("execute", "run", "generate", "chat", "complete"):
            candidate = getattr(runtime_profile, attr_name, None)
            if callable(candidate):
                executor = candidate
                break
        if executor is None and isinstance(runtime_profile, Mapping):
            candidate = runtime_profile.get("executor")
            if callable(candidate):
                executor = candidate
        if executor is None:
            if self.backend == RuntimeBackend.VLLM and self.vllm_client is not None:
                return self._execute_vllm(request_id, admitted_request)
            return RuntimeFailureSignal(request_id, self.backend, "RUNTIME_EXECUTOR_UNAVAILABLE", "runtime profile does not expose an executor")
        try:
            raw_result = executor(admitted_request)
        except Exception as exc:
            return RuntimeFailureSignal(request_id, self.backend, "RUNTIME_CALL_FAILED", str(exc), False, {"exception_type": type(exc).__name__})
        if isinstance(raw_result, (NormalizedRuntimeResult, RuntimeFailureSignal)):
            return raw_result
        if isinstance(raw_result, Mapping):
            status = str(raw_result.get("status", "ok"))
            if status in {"error", "failed", "failure"} or raw_result.get("error"):
                metadata = raw_result.get("metadata", {})
                return RuntimeFailureSignal(
                    request_id,
                    self.backend,
                    str(raw_result.get("reason_code") or "RUNTIME_CALL_FAILED"),
                    str(raw_result.get("message") or raw_result.get("error") or "runtime call failed"),
                    bool(raw_result.get("retriable", False)),
                    dict(metadata) if isinstance(metadata, Mapping) else {},
                )
            usage = raw_result.get("usage", {})
            runtime_metadata = raw_result.get("runtime_metadata", {})
            telemetry = raw_result.get("telemetry", {})
            metadata = raw_result.get("metadata", {})
            return NormalizedRuntimeResult(
                assistant_message=raw_result.get("assistant_message") or raw_result.get("message") or raw_result.get("content") or raw_result.get("response"),
                usage=dict(usage) if isinstance(usage, Mapping) else {},
                status=status,
                runtime_metadata=dict(runtime_metadata) if isinstance(runtime_metadata, Mapping) else {},
                telemetry=dict(telemetry) if isinstance(telemetry, Mapping) else {},
                metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
            )
        return NormalizedRuntimeResult(assistant_message=raw_result)
