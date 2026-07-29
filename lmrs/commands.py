"""Public command and structured error contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from lmrs.admission import build_queue_entry_request, decide_admission
from lmrs.estimation import estimate_request_capacity
from lmrs.queue import QueueDispatchWorker, QueueEntry, RequestQueue


class CommandName:
    """Stable public command surface identifiers.

    Attributes:
        HEALTHCHECK: Health check command.
        MODEL_STATUS: Model status command.
        CAPACITY: Capacity reporting command.
        TOKEN_COUNT: Token counting command.
        ESTIMATE: Dry-run estimate command.
        CHAT: Chat execution command.
        QUEUE_STATUS: Queue status command.
        CANCEL: Request cancellation command.
        LOCAL_MODEL_CACHE_PRELOAD: Disk cache preload command.
        LOCAL_MODEL_CACHE_STATUS: Disk cache status command.
        LOCAL_MODEL_CACHE_DELETE: Disk cache delete command.
        LOCAL_MODEL_LOAD: Model memory load command.
        LOCAL_MODEL_UNLOAD: Model memory unload command.
        LOCAL_MODEL_RELOAD: Model memory reload command.
        LOCAL_LMCACHE_STATUS: LMCache status command.
        LOCAL_LMCACHE_PURGE: LMCache purge command.
    """

    HEALTHCHECK: str = "healthcheck"
    MODEL_STATUS: str = "model_status"
    CAPACITY: str = "capacity"
    TOKEN_COUNT: str = "token_count"
    ESTIMATE: str = "estimate"
    CHAT: str = "chat"
    QUEUE_STATUS: str = "queue_status"
    CANCEL: str = "cancel"
    LOCAL_MODEL_CACHE_PRELOAD: str = "local_model_cache_preload"
    LOCAL_MODEL_CACHE_STATUS: str = "local_model_cache_status"
    LOCAL_MODEL_CACHE_DELETE: str = "local_model_cache_delete"
    LOCAL_MODEL_LOAD: str = "local_model_load"
    LOCAL_MODEL_UNLOAD: str = "local_model_unload"
    LOCAL_MODEL_RELOAD: str = "local_model_reload"
    LOCAL_LMCACHE_STATUS: str = "local_lmcache_status"
    LOCAL_LMCACHE_PURGE: str = "local_lmcache_purge"


class ErrorCode:
    """Stable machine-readable error catalog.

    Attributes:
        CONTEXT_OVERFLOW: Request exceeds the model context window.
        REQUEST_TOO_LARGE: Request is too large to admit.
        HARDWARE_VRAM_INSUFFICIENT: Insufficient VRAM for the request.
        HARDWARE_CAPACITY_UNKNOWN: Hardware capacity could not be determined.
        TOKENIZER_UNAVAILABLE: Tokenizer is unavailable.
        MODEL_BUSY_AND_QUEUE_FULL: Model busy and the queue is full.
        MODEL_NOT_LOADED: Target model is not loaded in memory.
        MODEL_LOAD_FAILED: Model failed to load into memory.
        RUNTIME_CALL_FAILED: Runtime backend call failed.
        REQUEST_CANCELLED: Request was cancelled.
        MODEL_NOT_CACHED: Model is not present in the disk cache.
        MODEL_CACHE_PRELOAD_FAILED: Disk cache preload failed.
        MODEL_CACHE_CORRUPTED: Disk cache contents are corrupted.
        MODEL_ALREADY_LOADED: Model is already loaded in memory.
        MODEL_UNLOAD_FAILED: Model failed to unload from memory.
        LMCACHE_UNAVAILABLE: LMCache backend is unavailable.
        LMCACHE_LOOKUP_FAILED: LMCache lookup failed.
        LMCACHE_WRITE_FAILED: LMCache write failed.
    """

    CONTEXT_OVERFLOW: str = "CONTEXT_OVERFLOW"
    REQUEST_TOO_LARGE: str = "REQUEST_TOO_LARGE"
    HARDWARE_VRAM_INSUFFICIENT: str = "HARDWARE_VRAM_INSUFFICIENT"
    HARDWARE_CAPACITY_UNKNOWN: str = "HARDWARE_CAPACITY_UNKNOWN"
    TOKENIZER_UNAVAILABLE: str = "TOKENIZER_UNAVAILABLE"
    MODEL_BUSY_AND_QUEUE_FULL: str = "MODEL_BUSY_AND_QUEUE_FULL"
    MODEL_NOT_LOADED: str = "MODEL_NOT_LOADED"
    MODEL_LOAD_FAILED: str = "MODEL_LOAD_FAILED"
    RUNTIME_CALL_FAILED: str = "RUNTIME_CALL_FAILED"
    REQUEST_CANCELLED: str = "REQUEST_CANCELLED"
    MODEL_NOT_CACHED: str = "MODEL_NOT_CACHED"
    MODEL_CACHE_PRELOAD_FAILED: str = "MODEL_CACHE_PRELOAD_FAILED"
    MODEL_CACHE_CORRUPTED: str = "MODEL_CACHE_CORRUPTED"
    MODEL_ALREADY_LOADED: str = "MODEL_ALREADY_LOADED"
    MODEL_UNLOAD_FAILED: str = "MODEL_UNLOAD_FAILED"
    LMCACHE_UNAVAILABLE: str = "LMCACHE_UNAVAILABLE"
    LMCACHE_LOOKUP_FAILED: str = "LMCACHE_LOOKUP_FAILED"
    LMCACHE_WRITE_FAILED: str = "LMCACHE_WRITE_FAILED"


class CommandOutcome:
    """Stable result classification for chat and estimate commands.

    Attributes:
        EXECUTED: Command executed immediately.
        QUEUED: Command was queued for later execution.
        REJECTED: Command was rejected.
        WOULD_EXECUTE: Dry-run estimate: would execute immediately.
        WOULD_QUEUE: Dry-run estimate: would be queued.
        WOULD_REJECT: Dry-run estimate: would be rejected.
    """

    EXECUTED: str = "executed"
    QUEUED: str = "queued"
    REJECTED: str = "rejected"
    WOULD_EXECUTE: str = "would_execute"
    WOULD_QUEUE: str = "would_queue"
    WOULD_REJECT: str = "would_reject"


@dataclass(frozen=True)
class CommandResult:
    """Unified public-command result for success, queue, and rejection.

    Attributes:
        command: The command that produced this result.
        outcome: The result classification for this command.
        success: Whether the command succeeded.
        reason_code: Stable error code on rejection, if any.
        token_breakdown: Optional token accounting breakdown.
        capacity_snapshot: Optional capacity snapshot.
        queue_state: Optional request queue state.
        payload: Optional command-specific payload.
        metadata: Arbitrary metadata about the result.
    """

    command: str
    outcome: str
    success: bool
    reason_code: str | None = None
    token_breakdown: object | None = None
    capacity_snapshot: object | None = None
    queue_state: object | None = None
    payload: object | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass
class PublicCommandContract:
    """Declarative contract mapping each command to category and result shape.

    For each CommandName it declares the accepted operation category and the
    CommandResult shape returned, as contract stubs. The estimate command
    performs dry-run token accounting and admission and returns a would_*
    outcome; the chat command performs token accounting and admission and
    returns executed, queued, or rejected; queue_status reports request queue
    state. This object only declares the contract and performs no runtime
    execution or admission logic itself.

    Attributes:
        command_categories: Mapping of command name to operation category.
        result_shapes: Mapping of command name to CommandResult shape label.
    """

    command_categories: Mapping[str, str] = field(
        default_factory=lambda: {
            CommandName.HEALTHCHECK: "health",
            CommandName.MODEL_STATUS: "model_status",
            CommandName.CAPACITY: "capacity",
            CommandName.TOKEN_COUNT: "token_accounting",
            CommandName.ESTIMATE: "token_accounting_admission_dry_run",
            CommandName.CHAT: "token_accounting_admission_chat",
            CommandName.QUEUE_STATUS: "request_queue_state",
            CommandName.CANCEL: "request_cancellation",
            CommandName.LOCAL_MODEL_CACHE_PRELOAD: "disk_cache",
            CommandName.LOCAL_MODEL_CACHE_STATUS: "disk_cache",
            CommandName.LOCAL_MODEL_CACHE_DELETE: "disk_cache",
            CommandName.LOCAL_MODEL_LOAD: "model_lifecycle",
            CommandName.LOCAL_MODEL_UNLOAD: "model_lifecycle",
            CommandName.LOCAL_MODEL_RELOAD: "model_lifecycle",
        }
    )
    result_shapes: Mapping[str, str] = field(
        default_factory=lambda: {
            CommandName.HEALTHCHECK: "payload",
            CommandName.MODEL_STATUS: "payload",
            CommandName.CAPACITY: "capacity_snapshot",
            CommandName.TOKEN_COUNT: "token_breakdown",
            CommandName.ESTIMATE: "would_outcome_with_reason",
            CommandName.CHAT: "execution_queue_or_rejection",
            CommandName.QUEUE_STATUS: "queue_state",
            CommandName.CANCEL: "cancellation_result",
            CommandName.LOCAL_MODEL_CACHE_PRELOAD: "cache_result",
            CommandName.LOCAL_MODEL_CACHE_STATUS: "cache_result",
            CommandName.LOCAL_MODEL_CACHE_DELETE: "cache_result",
            CommandName.LOCAL_MODEL_LOAD: "lifecycle_result",
            CommandName.LOCAL_MODEL_UNLOAD: "lifecycle_result",
            CommandName.LOCAL_MODEL_RELOAD: "lifecycle_result",
        }
    )

    def operation_category(self, command: str) -> str:
        """Return the declared operation category for a command.

        Args:
            command: The command name to look up.

        Returns:
            The declared operation category for the command.
        """
        return self.command_categories[command]

    def result_shape(self, command: str) -> str:
        """Return the declared CommandResult shape for a command.

        Args:
            command: The command name to look up.

        Returns:
            A description of the CommandResult shape for the command.
        """
        return self.result_shapes[command]


_ADMISSION_DECISIONS: tuple[str, ...] = ("execute", "queue", "reject")

_DRY_RUN_OUTCOMES: Mapping[str, str] = {
    "execute": CommandOutcome.WOULD_EXECUTE,
    "queue": CommandOutcome.WOULD_QUEUE,
    "reject": CommandOutcome.WOULD_REJECT,
}

_EXECUTION_OUTCOMES: Mapping[str, str] = {
    "execute": CommandOutcome.EXECUTED,
    "queue": CommandOutcome.QUEUED,
    "reject": CommandOutcome.REJECTED,
}


def _canonical_decision(decision: str) -> str:
    """Return the canonical admission decision behind a verdict value.

    The canonical admission vocabulary is ``execute``, ``queue`` and
    ``reject``. A ``would_``-prefixed spelling is accepted and normalized so a
    verdict produced by the current admission implementation, which emits the
    dry-run spelling for a live decision, still classifies correctly; that
    spelling is a recorded defect of the admission layer and is tolerated here
    only so the two contracts can be corrected independently.

    Args:
        decision: Decision value carried by an admission verdict.

    Returns:
        One of ``execute``, ``queue`` or ``reject``.

    Raises:
        ValueError: If the value is not a recognized admission decision.
    """
    canonical = decision[6:] if decision.startswith("would_") else decision
    if canonical not in _ADMISSION_DECISIONS:
        raise ValueError(f"unknown admission decision: {decision!r}")
    return canonical


@dataclass
class PublicCommandExecutor(PublicCommandContract):
    """Deterministic domain-level executor for the public command surface.

    Implements PublicCommandContract by inheriting its declared command
    categories and result shapes, and adds the translation from the typed
    results produced by the canonical domain services into the stable
    CommandResult public outcome shape.

    It is a translator, not a pipeline. Callers supply the already-produced
    artifacts - the capacity estimate from estimate_request_capacity, the
    verdict from decide_admission, the queue-entry request from
    build_queue_entry_request, request-queue state from RequestQueue with
    largest_fit_scheduler and launch_recheck, and the normalized result from
    RuntimeClient.execute - and this object only classifies the outcome and
    carries stable structured reason data. It reimplements no token
    accounting, estimation, admission, queue, scheduler, launch-recheck or
    runtime algorithm, performs no routing, and is not the canonical chat
    handler: composing those services for the estimate and chat commands
    belongs to that handler. Adapter command classes and their registration
    through register_lmrs_commands(registry) belong to the adapter layer and
    are deliberately absent here.
    """

    def dry_run_outcome(self, decision: str) -> str:
        """Return the dry-run outcome for an admission decision.

        Args:
            decision: Decision value carried by an admission verdict.

        Returns:
            One of the ``would_*`` CommandOutcome values.
        """
        return _DRY_RUN_OUTCOMES[_canonical_decision(decision)]

    def execution_outcome(self, decision: str) -> str:
        """Return the execution outcome for an admission decision.

        Args:
            decision: Decision value carried by an admission verdict.

        Returns:
            One of the executed, queued or rejected CommandOutcome values.
        """
        return _EXECUTION_OUTCOMES[_canonical_decision(decision)]

    def estimate_result(
        self,
        decision: str,
        *,
        reason_code: str | None = None,
        token_breakdown: object | None = None,
        capacity_snapshot: object | None = None,
        payload: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> CommandResult:
        """Build the dry-run estimate result for an admission decision.

        Args:
            decision: Decision value carried by an admission verdict.
            reason_code: Stable reason code carried by that verdict, if any.
            token_breakdown: Token accounting artifact to carry, if any.
            capacity_snapshot: Capacity snapshot artifact to carry, if any.
            payload: Command-specific payload to carry, if any.
            metadata: Additional result metadata.

        Returns:
            A CommandResult for the estimate command with a ``would_*``
            outcome; success is False only for a would_reject decision.
        """
        outcome = self.dry_run_outcome(decision)
        return CommandResult(
            command=CommandName.ESTIMATE,
            outcome=outcome,
            success=outcome != CommandOutcome.WOULD_REJECT,
            reason_code=reason_code,
            token_breakdown=token_breakdown,
            capacity_snapshot=capacity_snapshot,
            payload=payload,
            metadata=dict(metadata or {}),
        )

    def chat_result(
        self,
        decision: str,
        *,
        reason_code: str | None = None,
        token_breakdown: object | None = None,
        capacity_snapshot: object | None = None,
        queue_state: object | None = None,
        payload: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> CommandResult:
        """Build the chat execution result for an admission decision.

        Args:
            decision: Decision value carried by an admission verdict.
            reason_code: Stable reason code carried by that verdict, if any.
            token_breakdown: Token accounting artifact to carry, if any.
            capacity_snapshot: Capacity snapshot artifact to carry, if any.
            queue_state: Request-queue state artifact to carry, if any.
            payload: Runtime result payload to carry, if any.
            metadata: Additional result metadata.

        Returns:
            A CommandResult for the chat command with an executed, queued or
            rejected outcome; success is False only for a rejected decision.
        """
        outcome = self.execution_outcome(decision)
        return CommandResult(
            command=CommandName.CHAT,
            outcome=outcome,
            success=outcome != CommandOutcome.REJECTED,
            reason_code=reason_code,
            token_breakdown=token_breakdown,
            capacity_snapshot=capacity_snapshot,
            queue_state=queue_state,
            payload=payload,
            metadata=dict(metadata or {}),
        )

    def failure_result(
        self,
        command: str,
        reason_code: str,
        *,
        payload: object | None = None,
        metadata: Mapping[str, object] | None = None,
    ) -> CommandResult:
        """Build a rejection result for a domain failure signal.

        Args:
            command: The command the failure belongs to.
            reason_code: Stable ErrorCode value describing the failure.
            payload: Command-specific payload to carry, if any.
            metadata: Additional result metadata.

        Returns:
            A CommandResult carrying the rejected outcome and reason code.
        """
        return CommandResult(
            command=command,
            outcome=CommandOutcome.REJECTED,
            success=False,
            reason_code=reason_code,
            payload=payload,
            metadata=dict(metadata or {}),
        )


def _queue_entry_from_record(record: Mapping[str, object]) -> QueueEntry:
    """Build a QueueEntry from an admitted queue record.

    Args:
        record: Record produced by build_queue_entry_request.

    Returns:
        The corresponding QueueEntry.
    """
    return QueueEntry(
        request_id=str(record["request_id"]),
        session_id=str(record["session_id"]),
        model_name=str(record["model_name"]),
        required_tokens=int(record["required_tokens"]),  # type: ignore[call-overload]
        required_dynamic_vram_bytes=int(
            record["required_dynamic_vram_bytes"]  # type: ignore[call-overload]
        ),
        admitted_at=str(record["admitted_at"]),
        expires_at=str(record["expires_at"]),
        priority=int(record["priority"]),  # type: ignore[call-overload]
        status=str(record["status"]),
    )


@dataclass
class CanonicalChatHandler:
    """Canonical handler composing the estimate and chat command paths.

    Composes the canonical producers and owns none of their algorithms. The
    capacity estimate comes only from estimate_request_capacity, the verdict
    only from decide_admission, and the admitted queue record only from
    build_queue_entry_request; queue state is held by RequestQueue.

    The dry-run path neither queues nor executes and yields would_execute,
    would_queue or would_reject. The chat path yields executed, queued or
    rejected: a rejected verdict carries the stable admission reason code and
    never reaches the runtime, a queued verdict records the entry and returns
    queue state without reaching the runtime, and only an admitted execute
    verdict invokes the runtime.

    Launching a request that is already queued is delegated to
    QueueDispatchWorker, which owns the largest_fit_scheduler then
    launch_recheck then executor ordering and the capacity recheck required
    before a queued launch. That ordering is deliberately not reimplemented
    here: a second dispatch path in this module would duplicate the worker.

    Registering adapter command classes belongs to the adapter layer and is
    absent from this module by design.

    Attributes:
        executor: Translator producing the stable CommandResult outcomes.
    """

    executor: PublicCommandExecutor = field(default_factory=PublicCommandExecutor)

    def _estimate_and_admit(
        self,
        *,
        request_id: str,
        model_name: str,
        token_breakdown: Any,
        declared_context_window: int,
        capacity: Any,
        kv_bytes_per_token: int,
        per_request_overhead_bytes: int,
        runtime_batch_overhead_bytes: int,
        cache_hit_confirmed: bool = False,
        cache_adjusted_dynamic_vram_bytes: int | None = None,
    ) -> tuple[Any, Any]:
        """Produce the capacity estimate and admission verdict for a request.

        Returns:
            The RequestCapacityEstimate and the AdmissionVerdict.
        """
        estimate = estimate_request_capacity(
            request_id=request_id,
            model_name=model_name,
            token_breakdown=token_breakdown,
            declared_context_window=declared_context_window,
            usable_dynamic_vram_bytes=capacity.usable_dynamic_vram_bytes,
            kv_bytes_per_token=kv_bytes_per_token,
            per_request_overhead_bytes=per_request_overhead_bytes,
            runtime_batch_overhead_bytes=runtime_batch_overhead_bytes,
        )
        verdict = decide_admission(
            estimate,
            capacity,
            cache_hit_confirmed=cache_hit_confirmed,
            cache_adjusted_dynamic_vram_bytes=cache_adjusted_dynamic_vram_bytes,
        )
        return estimate, verdict

    def estimate(self, **request: Any) -> CommandResult:
        """Run the dry-run estimate path without queueing or executing.

        Args:
            **request: The estimate inputs accepted by _estimate_and_admit.

        Returns:
            A CommandResult with a would_execute, would_queue or would_reject
            outcome carrying the reason code, token breakdown and capacity
            snapshot.
        """
        estimate, verdict = self._estimate_and_admit(**request)
        return self.executor.estimate_result(
            verdict.decision,
            reason_code=verdict.reason_code,
            token_breakdown=estimate.token_breakdown,
            capacity_snapshot=request["capacity"],
        )

    def chat(
        self,
        *,
        queue: RequestQueue,
        request_metadata: Mapping[str, object],
        queue_metadata: Mapping[str, object],
        runtime_client: Any = None,
        runtime_profile: Any = None,
        admitted_request: Any = None,
        **request: Any,
    ) -> CommandResult:
        """Run the chat path, executing, queueing or rejecting the request.

        Args:
            queue: Current request queue holding admitted-but-waiting entries.
            request_metadata: Request-side facts for the queue record.
            queue_metadata: Queue-side facts for the queue record.
            runtime_client: Client whose execute runs an admitted request.
            runtime_profile: Runtime profile passed to the runtime client.
            admitted_request: Payload handed to the runtime on execution.
            **request: The estimate inputs accepted by _estimate_and_admit.

        Returns:
            A CommandResult with an executed, queued or rejected outcome.
        """
        estimate, verdict = self._estimate_and_admit(**request)
        decision = _canonical_decision(verdict.decision)
        capacity = request["capacity"]
        if decision == "reject":
            return self.executor.chat_result(
                verdict.decision,
                reason_code=verdict.reason_code,
                token_breakdown=estimate.token_breakdown,
                capacity_snapshot=capacity,
            )
        if decision == "queue":
            record = build_queue_entry_request(
                verdict, estimate, request_metadata, queue_metadata
            )
            updated = queue.add(_queue_entry_from_record(record))
            return self.executor.chat_result(
                verdict.decision,
                reason_code=verdict.reason_code,
                token_breakdown=estimate.token_breakdown,
                capacity_snapshot=capacity,
                queue_state=updated.snapshot(),
                payload=record,
            )
        runtime_result = runtime_client.execute(admitted_request, runtime_profile)
        return self.executor.chat_result(
            verdict.decision,
            reason_code=verdict.reason_code,
            token_breakdown=estimate.token_breakdown,
            capacity_snapshot=capacity,
            payload=runtime_result,
        )

    def launch_queued(
        self,
        queue: RequestQueue,
        capacity_provider: Callable[[], int],
        executor: Callable[[QueueEntry], object],
    ) -> QueueEntry | None:
        """Launch one queued request through the canonical dispatch worker.

        The worker owns the largest_fit_scheduler then launch_recheck then
        executor ordering, including the capacity recheck required before a
        queued launch.

        Args:
            queue: Queue to select a waiting request from.
            capacity_provider: Returns current usable dynamic VRAM in bytes.
            executor: Receives the selected request on a successful recheck.

        Returns:
            The dispatched QueueEntry, or None when nothing was dispatched.
        """
        return QueueDispatchWorker(executor=executor).dispatch(
            queue, capacity_provider
        )
