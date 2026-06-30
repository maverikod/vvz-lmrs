"""Public command and structured error contracts for the LMRS package.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


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
    """Declarative contract mapping each command to its category and result.

    For each CommandName it declares the accepted operation category and the
    CommandResult shape returned, as contract stubs. The estimate command
    performs dry-run token accounting and admission and returns a would_*
    outcome; the chat command performs token accounting and admission and
    returns executed, queued, or rejected; queue_status reports request queue
    state. This object only declares the contract and performs no runtime
    execution or admission logic itself.

    Attributes:
        command_categories: Mapping of command name to its operation category.
    """

    command_categories: Mapping[str, str] = field(default_factory=dict)

    def operation_category(self, command: str) -> str:
        """Return the declared operation category for a command.

        Args:
            command: The command name to look up.

        Returns:
            The declared operation category for the command.
        """
        msg = "PublicCommandContract.operation_category is a contract stub"
        raise NotImplementedError(msg)

    def result_shape(self, command: str) -> str:
        """Return the declared CommandResult shape for a command.

        Args:
            command: The command name to look up.

        Returns:
            A description of the CommandResult shape for the command.
        """
        msg = "PublicCommandContract.result_shape is a contract stub"
        raise NotImplementedError(msg)
