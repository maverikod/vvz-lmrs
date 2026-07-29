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
        request_id = str(getattr(admitted_request, "request_id", "") or getattr(admitted_request, "id", "") or "unknown")
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
