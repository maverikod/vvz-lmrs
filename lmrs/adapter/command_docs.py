"""Exhaustive per-command documentation for the LMRS public surface.

One entry per public command, in the metadata paradigm shared across the fleet
(planmgr, code-analysis-server): detailed description, per-parameter docs,
structured return value with an example, usage examples, error cases keyed by
the stable reason codes the code actually returns, and best practices. The
``info`` command and every command's ``metadata()`` assemble their answers from
this module, so the server documents itself from one source instead of three
drifting ones.

Every reason code named here exists in the implementation; a test pins the
entries against the registered command surface so a new command cannot ship
undocumented.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any, Mapping

DOC_VERSION = "1.0.0"
DOC_AUTHOR = "Vasiliy Zdanovskiy"
DOC_EMAIL = "vasilyvz@gmail.com"

# The service guide the info command publishes. This is the whole-server
# documentation: what LMRS exists for, the invariant it enforces, and how the
# command families compose into the admission workflow.
SERVICE_GUIDE: dict[str, Any] = {
    "purpose": (
        "LMRS (Local Model Runtime Service) guards a local model runtime "
        "(vLLM). Its reason to exist: no prompt ever reaches the runtime "
        "until the service has proven the request fits the model's declared "
        "context window AND the measured free dynamic VRAM pool. A request "
        "that does not fit is rejected before execution with a stable, "
        "machine-readable reason code."
    ),
    "invariant": (
        "An oversized prompt is never executed. Admission happens before the "
        "runtime: token accounting (the runtime's own tokenizer), then the "
        "context-window check, then the VRAM check against measured facts. "
        "Rejection carries a reason code (CONTEXT_OVERFLOW, REQUEST_TOO_LARGE, "
        "...) instead of a runtime error."
    ),
    "admission_workflow": [
        "1. Count the prompt with the runtime's tokenizer (rough chars/4 fallback is explicitly marked rough).",
        "2. Derive the KV cost per token from the cached model's own config.json (kv_bytes = layers * 2 * kv_heads * head_dim * element_bytes).",
        "3. Measure current free VRAM (nvidia-smi) and derive the usable dynamic pool.",
        "4. Decide: fits pool and window -> execute; fits pool at full capacity but not right now -> queue; does not fit -> reject with a reason code.",
        "5. Only an admitted request reaches vLLM; a runtime failure after admission is a failed command, never a fake success.",
    ],
    "command_families": {
        "health_and_identity": ["healthcheck", "info"],
        "capacity_and_accounting": ["capacity", "token_count", "estimate"],
        "execution": ["chat", "queue_status", "cancel"],
        "disk_model_cache": ["local_model_cache_preload", "local_model_cache_status", "local_model_cache_delete"],
        "model_lifecycle": ["local_model_load", "local_model_unload", "local_model_reload", "local_model_switch", "model_status"],
        "lmcache": ["local_lmcache_status", "local_lmcache_purge"],
    },
    "conventions": {
        "results": (
            "Success and negative domain outcomes both arrive as a successful "
            "transport envelope; the payload carries success/outcome plus a "
            "stable reason_code. Queued commands (local_model_cache_preload, "
            "local_model_switch) answer through the framework job envelope."
        ),
        "measurements": (
            "Every capacity figure is a measurement or derived from one; a "
            "value nobody measured is null plus measured=false, never zero."
        ),
        "client": "pip install lmrs-client provides the lmrs-client CLI and the pipeline acceptance runner for this surface.",
    },
}


def _error(description: str, message: str, solution: str) -> dict[str, str]:
    """Build one error-case entry.

    Args:
        description: When this error occurs.
        message: The message or reason_code shape the caller sees.
        solution: What the caller should do.

    Returns:
        The error-case mapping.
    """
    return {"description": description, "message": message, "solution": solution}


COMMAND_DOCS: dict[str, dict[str, Any]] = {
    "healthcheck": {
        "category": "health_and_identity",
        "detailed_description": (
            "Report adapter liveness. Answers from the adapter process itself "
            "without touching the runtime, the GPU or the disk cache, so a "
            "healthy answer means exactly: the LMRS adapter is up and "
            "executing commands. Use info for capabilities and runtime state."
        ),
        "parameters": {},
        "return_value": {
            "success": {
                "description": "The adapter is alive.",
                "data": {"status": "Always 'ok' when the command executes.", "service": "Always 'lmrs'."},
                "example": {"status": "ok", "service": "lmrs"},
            },
        },
        "usage_examples": [
            {"description": "Liveness probe", "command": {}, "explanation": "The cheapest possible check; safe at any frequency."},
        ],
        "error_cases": {},
        "best_practices": [
            "A passing healthcheck says nothing about the model runtime; probe capacity or model_status for that.",
        ],
    },
    "info": {
        "category": "health_and_identity",
        "detailed_description": (
            "Describe the whole service: identity (package version from the "
            "installed distribution, never from a config file), the live "
            "runtime summary (model residency, queue, measured VRAM facts, "
            "proxy registration state), the service guide (purpose, "
            "invariant, admission workflow), and exhaustive per-command "
            "documentation - schema plus full metadata - for every registered "
            "command. This is the single self-documentation entrypoint: a "
            "client that has read info needs no external reference to drive "
            "the server."
        ),
        "parameters": {},
        "return_value": {
            "success": {
                "description": "The full self-description.",
                "data": {
                    "identity": "product_name, package_version, adapter_version.",
                    "documentation": "The service guide: purpose, invariant, admission workflow, command families, conventions.",
                    "runtime_summary": "Live facts: model_lifecycle, queue_state, vram (measured), registration.",
                    "capabilities": "Per family: every command with its JSON schema and full metadata (parameters, return value, examples, error cases, best practices).",
                },
            },
        },
        "usage_examples": [
            {"description": "Full self-description", "command": {}, "explanation": "Large answer; cache it client-side rather than polling."},
        ],
        "error_cases": {},
        "best_practices": [
            "A probe inside info that fails reports itself unavailable with the failure named; absence of a figure means it was not measured, not that it is zero.",
        ],
    },
    "capacity": {
        "category": "capacity_and_accounting",
        "detailed_description": (
            "Report the measured VRAM facts and the derived capacity pools "
            "admission decides against. The free VRAM is read from the driver "
            "at call time; the service baseline (free VRAM before any model "
            "was loaded) is persisted, because it is only observable while no "
            "model holds memory; the model's static cost is their difference. "
            "usable_dynamic_vram = free - safety_margin - runtime_reserve - "
            "active queue reservations. A figure nobody measured is null and "
            "measured=false - never an invented zero."
        ),
        "parameters": {},
        "return_value": {
            "success": {
                "description": "The measured facts and derived pools.",
                "data": {
                    "service_baseline_free_vram_bytes": "Free VRAM measured before the model loaded; null when never observed model-free.",
                    "model_loaded_free_vram_bytes": "Free VRAM measured now.",
                    "measured_model_static_vram_bytes": "Baseline minus current free; null without a model-free baseline.",
                    "max_dynamic_pool_bytes": "Free minus safety margin and runtime reserve.",
                    "usable_dynamic_vram_bytes": "Max pool minus active queue reservations.",
                    "measured": "False when the GPU could not be read; the pools are then zero and admission rejects.",
                    "measurement_metadata": "Source, timestamps, per-device readings, and the reason when a figure is unavailable.",
                },
            },
        },
        "usage_examples": [
            {"description": "Current capacity", "command": {}, "explanation": "Zeroed pools with measured=false mean the GPU is unreadable, not empty."},
        ],
        "error_cases": {},
        "best_practices": [
            "Admission uses usable_dynamic_vram_bytes, never total VRAM or disk sizes.",
            "measured=false rejects requests by design: refusing what cannot be sized IS the invariant.",
        ],
    },
    "token_count": {
        "category": "capacity_and_accounting",
        "detailed_description": (
            "Produce the token accounting of a request. Two modes.\n\n"
            "Text mode: pass message (plus optional system and model_name) and "
            "the server counts the prompt with the runtime's own tokenizer - "
            "the same tokenizer that would execute the request "
            "(tokenizer_accuracy=runtime_tokenizer). When the runtime cannot "
            "answer, the chars/4 heuristic applies and the result is "
            "explicitly marked rough_estimate=true.\n\n"
            "Numeric mode: pass already-known component counts (input_tokens, "
            "tool_tokens, service_tokens, reserved_output_tokens) with the "
            "tokenizer identity, and the server sums them into "
            "required_tokens. This mode never invents counts; it is "
            "arithmetic over caller-declared facts."
        ),
        "parameters": {
            "message": {"type": "string", "required": False, "description": "Text mode: the user message to count with the runtime tokenizer."},
            "system": {"type": "string", "required": False, "description": "Text mode: optional system instruction counted with the message."},
            "model_name": {"type": "string", "required": False, "description": "Text mode: model whose tokenizer and chat template apply; the resident model is assumed when omitted."},
            "reserved_output_tokens": {"type": "integer", "required": False, "default": 0, "description": "Output tokens reserved for generation; added to required_tokens in both modes."},
            "input_tokens": {"type": "integer", "required": False, "description": "Numeric mode: prompt tokens. Required in numeric mode."},
            "tool_tokens": {"type": "integer", "required": False, "default": 0, "description": "Numeric mode: tokens consumed by tool definitions."},
            "service_tokens": {"type": "integer", "required": False, "default": 0, "description": "Numeric mode: tokens consumed by system service instructions."},
            "tokenizer_name": {"type": "string", "required": False, "description": "Numeric mode: which tokenizer produced the counts. Required in numeric mode."},
            "tokenizer_accuracy": {"type": "string", "required": False, "description": "Numeric mode: accuracy descriptor of the counts. Required in numeric mode."},
            "rough_estimate": {"type": "boolean", "required": False, "default": False, "description": "Numeric mode: whether the counts are rough."},
        },
        "return_value": {
            "success": {
                "description": "The token breakdown and its total.",
                "data": {
                    "token_breakdown": "input/tool/service/reserved_output tokens plus tokenizer_name, tokenizer_accuracy and rough_estimate.",
                    "required_tokens": "Sum of every component; what admission compares to the context window.",
                },
                "example": {
                    "token_breakdown": {
                        "input_tokens": 36,
                        "tool_tokens": 0,
                        "service_tokens": 0,
                        "reserved_output_tokens": 128,
                        "tokenizer_name": "Qwen/Qwen2.5-Coder-7B-Instruct",
                        "tokenizer_accuracy": "runtime_tokenizer",
                        "rough_estimate": False,
                    },
                    "required_tokens": 164,
                },
            },
        },
        "usage_examples": [
            {"description": "Count a real prompt with the runtime tokenizer", "command": {"message": "Summarize this file", "model_name": "Qwen/Qwen2.5-Coder-7B-Instruct", "reserved_output_tokens": 256}},
            {"description": "Sum caller-declared component counts", "command": {"input_tokens": 1200, "reserved_output_tokens": 256, "tokenizer_name": "qwen2", "tokenizer_accuracy": "exact"}},
        ],
        "error_cases": {
            "ValueError": _error(
                "Neither mode's required inputs were supplied.",
                "token_count requires either message (text mode) or input_tokens with tokenizer_name and tokenizer_accuracy (numeric mode)",
                "Supply message for text mode, or the numeric-mode fields.",
            ),
        },
        "best_practices": [
            "Prefer text mode: the count then comes from the tokenizer that will execute the request.",
            "Always check rough_estimate before treating a count as exact.",
        ],
    },
    "estimate": {
        "category": "capacity_and_accounting",
        "detailed_description": (
            "Dry-run admission: report whether a request WOULD execute, queue "
            "or be rejected, without queueing or executing anything. This is "
            "the prompt-size control the service exists for, usable before "
            "committing to a chat call. Two modes.\n\n"
            "Text mode: pass message (plus optional system, max_tokens, "
            "model_name) and the server does everything itself - counts the "
            "prompt with the runtime tokenizer, derives the KV cost from the "
            "cached model's config.json, measures current capacity - and "
            "returns the verdict with the full token breakdown and capacity "
            "snapshot.\n\n"
            "Raw mode: pass every admission input explicitly (request_id, "
            "token_breakdown, declared_context_window, capacity, "
            "kv_bytes_per_token, overheads) and the server only classifies. "
            "Raw mode exercises the admission algebra with caller-controlled "
            "numbers; text mode answers the practical question 'will this "
            "prompt fit right now'."
        ),
        "parameters": {
            "message": {"type": "string", "required": False, "description": "Text mode: the user message to admit."},
            "system": {"type": "string", "required": False, "description": "Text mode: optional system instruction."},
            "model_name": {"type": "string", "required": False, "description": "Model the request targets. Required in text mode."},
            "max_tokens": {"type": "integer", "required": False, "default": 128, "description": "Text mode: output tokens to reserve; part of required_tokens."},
            "request_id": {"type": "string", "required": False, "description": "Raw mode: request identifier. Generated in text mode."},
            "token_breakdown": {"type": "object", "required": False, "description": "Raw mode: component token counts."},
            "declared_context_window": {"type": "integer", "required": False, "description": "Raw mode: the model's declared window."},
            "capacity": {"type": "object", "required": False, "description": "Raw mode: capacity snapshot (usable_dynamic_vram_bytes, ...)."},
            "kv_bytes_per_token": {"type": "integer", "required": False, "description": "Raw mode: KV-cache bytes one token costs."},
            "per_request_overhead_bytes": {"type": "integer", "required": False, "description": "Raw mode: fixed per-request VRAM overhead."},
            "runtime_batch_overhead_bytes": {"type": "integer", "required": False, "description": "Raw mode: batch-level VRAM overhead."},
        },
        "return_value": {
            "success": {
                "description": "The dry-run verdict.",
                "data": {
                    "outcome": "would_execute | would_queue | would_reject.",
                    "success": "False only for would_reject.",
                    "reason_code": "CAPACITY_AVAILABLE, CAPACITY_CONSTRAINED, CONTEXT_OVERFLOW, REQUEST_TOO_LARGE, ...",
                    "token_breakdown": "The accounting the verdict was made from.",
                    "capacity_snapshot": "The capacity the verdict was made against.",
                },
                "example": {"outcome": "would_reject", "success": False, "reason_code": "CONTEXT_OVERFLOW"},
            },
        },
        "usage_examples": [
            {"description": "Will this prompt fit right now?", "command": {"message": "Refactor this module...", "model_name": "Qwen/Qwen2.5-Coder-7B-Instruct", "max_tokens": 1024}},
            {
                "description": "Exercise the admission algebra with explicit numbers",
                "command": {
                    "request_id": "probe-1",
                    "model_name": "m",
                    "token_breakdown": {"input_tokens": 8, "tool_tokens": 0, "service_tokens": 0, "reserved_output_tokens": 8},
                    "declared_context_window": 4096,
                    "capacity": {"usable_dynamic_vram_bytes": 1073741824},
                    "kv_bytes_per_token": 1024,
                    "per_request_overhead_bytes": 0,
                    "runtime_batch_overhead_bytes": 0,
                },
            },
        ],
        "error_cases": {
            "CONTEXT_OVERFLOW": _error("required_tokens exceeds the declared context window.", "outcome=would_reject, reason_code=CONTEXT_OVERFLOW", "Shorten the prompt or reserve fewer output tokens."),
            "REQUEST_TOO_LARGE": _error(
                "The request's VRAM need exceeds the whole dynamic pool; queueing could never help.",
                "outcome=would_reject, reason_code=REQUEST_TOO_LARGE",
                "Reduce the request or run a smaller model.",
            ),
            "CAPACITY_CONSTRAINED": _error(
                "Fits the pool but not the currently free VRAM.",
                "outcome=would_queue, reason_code=CAPACITY_CONSTRAINED",
                "The request would wait in the queue; retry later or submit and wait.",
            ),
            "HARDWARE_CAPACITY_UNKNOWN": _error((
                "Text mode: the model is not cached on disk or its config lacks the KV parameters, so the request cannot be sized."
            ), "success=false, reason_code=HARDWARE_CAPACITY_UNKNOWN", "Preload the model (local_model_cache_preload) so its config.json is available."),
            "MODEL_SWITCHING": _error("A model switch is in progress for the target model.", "outcome=would_reject, reason_code=MODEL_SWITCHING", "Wait for the switch to finish; poll model_status."),
            "ValueError": _error("Neither text-mode nor raw-mode inputs were supplied completely.", "estimate requires message+model_name (text mode) or the full raw input set", "Supply one complete mode."),
        },
        "best_practices": [
            "Use text mode before every large chat call; it is cheap and never touches the runtime's generation path.",
            "would_queue is not a failure: it means the request fits the hardware but must wait for VRAM.",
        ],
    },
    "chat": {
        "category": "execution",
        "detailed_description": (
            "Admit and execute one chat request. The full admission path runs "
            "first - runtime-tokenizer count, KV cost from the cached model "
            "config, measured capacity - and only an admitted request reaches "
            "vLLM. Outcomes: executed (with the normalized runtime result), "
            "queued (admitted, waiting for VRAM; the entry carries a TTL), or "
            "rejected (with the admission reason code, the runtime never "
            "touched). A runtime failure after admission is reported as a "
            "failed command carrying the runtime's reason code - never as a "
            "success."
        ),
        "parameters": {
            "message": {"type": "string", "required": True, "description": "The user message."},
            "model_name": {"type": "string", "required": True, "description": "Model to serve the request; must be cached on disk and served by the runtime."},
            "system": {"type": "string", "required": False, "description": "Optional system instruction."},
            "temperature": {"type": "number", "required": False, "default": 0, "description": "Sampling temperature."},
            "max_tokens": {"type": "integer", "required": False, "default": 128, "description": "Output token limit; also the reserved output amount during admission."},
            "request_id": {"type": "string", "required": False, "description": "Caller-supplied request identifier; generated when omitted."},
            "session_id": {"type": "string", "required": False, "description": "Session identifier recorded on a queued entry."},
        },
        "return_value": {
            "success": {
                "description": "The admission outcome, and on execution the normalized runtime result.",
                "data": {
                    "outcome": "executed | queued | rejected.",
                    "reason_code": "Admission or runtime reason code.",
                    "token_breakdown": "The runtime-tokenizer accounting.",
                    "capacity_snapshot": "The measured capacity the decision used.",
                    "payload": "On executed: assistant_message, usage, runtime_metadata, telemetry (latency_ms). On queued: the queue record.",
                },
                "example": {"outcome": "executed", "reason_code": "CAPACITY_AVAILABLE", "payload": {"assistant_message": "ready", "usage": {"total_tokens": 38}}},
            },
        },
        "usage_examples": [
            {"description": "A simple admitted completion", "command": {"message": "Reply with the single word: ready", "model_name": "Qwen/Qwen2.5-Coder-7B-Instruct", "max_tokens": 8}},
        ],
        "error_cases": {
            "CONTEXT_OVERFLOW": _error(
                "The prompt plus reserved output exceeds the context window.",
                "outcome=rejected, reason_code=CONTEXT_OVERFLOW; the runtime was never called",
                "Shorten the prompt or lower max_tokens; verify with estimate first.",
            ),
            "REQUEST_TOO_LARGE": _error("VRAM need exceeds the whole dynamic pool.", "outcome=rejected, reason_code=REQUEST_TOO_LARGE", "Reduce the request."),
            "HARDWARE_CAPACITY_UNKNOWN": _error("The model cannot be sized (not cached or config lacks KV parameters).", "success=false, reason_code=HARDWARE_CAPACITY_UNKNOWN", "Preload the model first."),
            "MODEL_SWITCHING": _error("A switch is in progress for this model.", "outcome=rejected, reason_code=MODEL_SWITCHING", "Wait for the switch to finish."),
            "VLLM_UNAVAILABLE": _error("Admitted, but the runtime did not answer.", "success=false, reason_code=VLLM_UNAVAILABLE, metadata.retriable=true", "Check model_status/info; retry after the runtime is back."),
            "RUNTIME_CALL_FAILED": _error(
                "Admitted, but the runtime rejected or failed the call.",
                "success=false, reason_code=RUNTIME_CALL_FAILED",
                "Inspect metadata.message; the request may be malformed for the runtime.",
            ),
        },
        "best_practices": [
            "Run estimate with the same message and max_tokens first when the prompt size is in doubt.",
            "A rejected outcome proves the runtime was never reached; that is the service invariant working, not an error to retry blindly.",
        ],
    },
    "queue_status": {
        "category": "execution",
        "detailed_description": (
            "Report the admitted-but-waiting request queue. Every entry "
            "carries its identity, the token and VRAM requirements admission "
            "computed, the admission timestamp and the TTL expiry. Entries "
            "hold VRAM reservations that reduce usable capacity."
        ),
        "parameters": {},
        "return_value": {
            "success": {
                "description": "The queue entries.",
                "data": {"entries": "List of queued records: request_id, session_id, model_name, required_tokens, required_dynamic_vram_bytes, admitted_at, expires_at, priority, status."},
            },
        },
        "usage_examples": [{"description": "Current queue", "command": {}}],
        "error_cases": {},
        "best_practices": ["An empty queue plus low usable VRAM means the model itself holds the memory, not waiting requests."],
    },
    "cancel": {
        "category": "execution",
        "detailed_description": (
            "Remove one queued request and release its VRAM reservation. "
            "Cancelling an identifier that is not queued is a no-op that "
            "still reports the resulting queue state."
        ),
        "parameters": {
            "request_id": {"type": "string", "required": True, "description": "Identifier of the queued request to remove."},
        },
        "return_value": {
            "success": {
                "description": "The queue after the cancellation.",
                "data": {"request_id": "The cancelled identifier.", "entries": "The remaining queue entries."},
            },
        },
        "usage_examples": [{"description": "Cancel a queued request", "command": {"request_id": "req-42"}}],
        "error_cases": {},
        "best_practices": ["Idempotent: cancelling twice is safe."],
    },
    "model_status": {
        "category": "model_lifecycle",
        "detailed_description": (
            "Report the memory residency of one model as lifecycle state: "
            "whether an operator load has marked it resident, and the "
            "measured VRAM facts recorded at load time. Residency is "
            "operator-declared state; the runtime's own serving list is "
            "consulted at load time, not here."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to inspect."},
        },
        "return_value": {
            "success": {
                "description": "The residency state.",
                "data": {"state": "not_loaded | loading | loaded | unloading | reloading | failed.", "measured_model_static_vram_bytes": "Static VRAM measured when the load happened, when available."},
            },
        },
        "usage_examples": [{"description": "Is the model resident?", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_NOT_LOADED": _error("No load has marked this model resident.", "success=false, reason_code=MODEL_NOT_LOADED", "Run local_model_load after the runtime serves the model."),
        },
        "best_practices": ["MODEL_NOT_LOADED right after a server restart is normal until local_model_load records residency."],
    },
    "local_model_cache_preload": {
        "category": "disk_model_cache",
        "detailed_description": (
            "Download and prepare a model in the local disk cache without "
            "loading it into GPU memory. Runs through the server's job queue "
            "because a weights download outlives any request timeout. The "
            "weights land in the same HuggingFace hub cache the runtime "
            "loads from; after the download the snapshot is verified "
            "(config, weights, every indexed shard, no dangling entries) and "
            "a readiness marker is written. A model already complete on disk "
            "is not downloaded again."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Repository-style model name, e.g. Qwen/Qwen2.5-Coder-7B-Instruct."},
        },
        "return_value": {
            "success": {
                "description": "The preload outcome (inside the job envelope).",
                "data": {
                    "status": "cached_on_disk on success.",
                    "record": "The cache record: path, revision, size, context window, quantization, tokenizer.",
                    "metadata": "already_cached, downloaded_path, readiness_marker_path.",
                },
            },
        },
        "usage_examples": [{"description": "Fetch a model to disk", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_CACHE_PRELOAD_FAILED": _error(
                "The download failed or the landed snapshot is incomplete.",
                "success=false, reason_code=MODEL_CACHE_PRELOAD_FAILED",
                "Inspect metadata.error; check network and HF_TOKEN.",
            ),
            "HF_HUB_UNAVAILABLE": _error("The huggingface_hub client is not installed in the server environment.", "success=false, reason_code=HF_HUB_UNAVAILABLE", "Install the server with its [server] extra."),
        },
        "best_practices": [
            "Preload before the first load of a new model; loading gates on disk presence.",
            "Queued command: the client waits for the job to finish.",
        ],
    },
    "local_model_cache_status": {
        "category": "disk_model_cache",
        "detailed_description": (
            "Report what the disk cache actually holds for one model, read "
            "from the cache directory at call time: state (not_cached, "
            "caching, cached_on_disk, failed), the resolved revision, "
            "measured size, the declared context window and quantization "
            "from the model's own config.json, and the concrete problems "
            "(missing shards, broken entries, in-flight downloads) when the "
            "snapshot is unusable."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to inspect."},
        },
        "return_value": {
            "success": {
                "description": "The cache state; success=true only when cached_on_disk.",
                "data": {"status": "Disk state.", "reason_code": "MODEL_NOT_CACHED or MODEL_CACHE_INCOMPLETE when not usable.", "record": "Full cache record with metadata naming any problems."},
            },
        },
        "usage_examples": [{"description": "Is the model on disk?", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_NOT_CACHED": _error("No repository directory for this model.", "success=false, reason_code=MODEL_NOT_CACHED", "Preload it."),
            "MODEL_CACHE_INCOMPLETE": _error((
                "A repository exists but the snapshot is not complete (download in flight, missing shards, broken links)."
            ), "success=false, reason_code=MODEL_CACHE_INCOMPLETE", "Wait for an in-flight download or re-run preload; record.metadata names the exact problems."),
        },
        "best_practices": ["status=caching with incomplete_blobs means a download is in flight; poll rather than re-preload."],
    },
    "local_model_cache_delete": {
        "category": "disk_model_cache",
        "detailed_description": (
            "Remove a model's weights and LMRS state from the disk cache. "
            "DESTRUCTIVE: the repository directory is deleted from disk and "
            "the freed bytes are reported. Never touches GPU residency; a "
            "resident model keeps serving from memory until restart, but "
            "cannot be reloaded afterwards."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to delete from disk."},
        },
        "return_value": {
            "success": {
                "description": "The deletion outcome.",
                "data": {"status": "not_cached after a successful delete.", "metadata": "removed_paths and freed_bytes."},
            },
        },
        "usage_examples": [{"description": "Free the disk of a scratch model", "command": {"model_name": "hf-internal-testing/tiny-random-gpt2"}}],
        "error_cases": {
            "MODEL_NOT_CACHED": _error("Nothing on disk for this model.", "success=false, reason_code=MODEL_NOT_CACHED", "Nothing to do."),
            "MODEL_CACHE_DELETE_FAILED": _error("The filesystem removal failed partway.", "success=false, reason_code=MODEL_CACHE_DELETE_FAILED", "Inspect metadata.error and removed_paths; fix permissions and retry."),
        },
        "best_practices": ["Never point this at the model the server is serving; deleting it removes the weights a restart needs."],
    },
    "local_model_load": {
        "category": "model_lifecycle",
        "detailed_description": (
            "Mark a model resident after proving it can serve: the disk "
            "cache must hold a complete snapshot (unless allow_preload "
            "explicitly waives the gate) and the runtime must report the "
            "model in its serving list. A successful load measures and "
            "records the model's static VRAM cost when a model-free "
            "baseline exists."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to load."},
            "allow_preload": {"type": "boolean", "required": False, "default": False, "description": "Skip the disk-cache gate; the caller owns the cache decision."},
        },
        "return_value": {
            "success": {
                "description": "The load outcome.",
                "data": {"state": "loaded on success.", "measured_model_static_vram_bytes": "Measured at load when a baseline exists.", "metadata": "The runtime probe result and VRAM facts."},
            },
        },
        "usage_examples": [{"description": "Record residency of the served model", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_NOT_CACHED": _error("The disk cache does not hold a complete snapshot.", "success=false, reason_code=MODEL_NOT_CACHED", "Preload first, or pass allow_preload deliberately."),
            "MODEL_NOT_SERVED_BY_VLLM": _error(
                "The runtime does not list the model as served.",
                "success=false, reason_code=MODEL_NOT_SERVED_BY_VLLM",
                "The runtime serves what it was started with; check info/vLLM configuration.",
            ),
            "MODEL_ALREADY_LOADED": _error(
                "Another model is resident (or the same model already is - then success=true).",
                "reason_code=MODEL_ALREADY_LOADED",
                "Unload or switch instead of loading over a resident model.",
            ),
        },
        "best_practices": ["vLLM serves one model per process: load records residency of the served model rather than starting a new one."],
    },
    "local_model_unload": {
        "category": "model_lifecycle",
        "detailed_description": (
            "Request removal of a model from GPU memory without touching its "
            "disk cache. On the vLLM backend dynamic unload is NOT supported: "
            "the honest answer for a resident model is "
            "VLLM_DYNAMIC_UNLOAD_UNSUPPORTED, and that answer is the expected "
            "behavior, not a defect. Freeing the GPU requires stopping or "
            "switching the runtime."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to unload."},
        },
        "return_value": {
            "success": {
                "description": "The unload outcome.",
                "data": {"state": "Unchanged for vLLM.", "reason_code": "VLLM_DYNAMIC_UNLOAD_UNSUPPORTED for a resident model on vLLM."},
            },
        },
        "usage_examples": [{"description": "Ask (and get the honest refusal) on vLLM", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_NOT_LOADED": _error("The model is not resident.", "success=false, reason_code=MODEL_NOT_LOADED", "Nothing to unload."),
            "VLLM_DYNAMIC_UNLOAD_UNSUPPORTED": _error(
                "vLLM cannot unload a model at runtime.",
                "success=false, reason_code=VLLM_DYNAMIC_UNLOAD_UNSUPPORTED",
                "Restart the runtime with another model, or use local_model_switch.",
            ),
        },
        "best_practices": ["Acceptance suites should assert the UNSUPPORTED reason code as the expected vLLM behavior."],
    },
    "local_model_reload": {
        "category": "model_lifecycle",
        "detailed_description": (
            "Re-probe the runtime and refresh the residency record of a "
            "model. For a model that is not resident this behaves as a load "
            "with the preload gate waived; for the resident model it "
            "re-verifies and refreshes the record."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to reload."},
        },
        "return_value": {
            "success": {"description": "The refreshed residency.", "data": {"state": "loaded on success.", "metadata": "reloaded=true when the model was already resident."}},
        },
        "usage_examples": [{"description": "Refresh residency after a runtime restart", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_NOT_SERVED_BY_VLLM": _error("The runtime does not list the model.", "success=false, reason_code=MODEL_NOT_SERVED_BY_VLLM", "Check what the runtime was started with."),
        },
        "best_practices": ["Use after a vLLM restart to re-attach the residency record to the live runtime."],
    },
    "local_model_switch": {
        "category": "model_lifecycle",
        "detailed_description": (
            "Switch the resident model to a target model as one queued "
            "operation: preload the target to disk when absent, unload the "
            "current resident, load the target. Progress stages (preloading, "
            "unloading, loading) are reported. While a switch runs, chat and "
            "estimate for the target are rejected with MODEL_SWITCHING - a "
            "model mid-switch is treated as not loaded. On any stage failure "
            "the sequence stops with that stage's reason code and residency "
            "stays consistent."
        ),
        "parameters": {
            "model_name": {"type": "string", "required": True, "description": "Model to switch to."},
        },
        "return_value": {
            "success": {
                "description": "The switch outcome (inside the job envelope).",
                "data": {"status": "loaded_in_memory or failed.", "reason_code": "The failed stage's reason on failure.", "progress": "The stages that ran.", "runtime_facts": "The load's recorded facts."},
            },
        },
        "usage_examples": [{"description": "Switch to (the already-resident) served model", "command": {"model_name": "Qwen/Qwen2.5-Coder-7B-Instruct"}}],
        "error_cases": {
            "MODEL_CACHE_PRELOAD_FAILED": _error("The target could not be fetched to disk.", "status=failed, failed_stage=preloading", "Check network/HF_TOKEN; preload separately to inspect."),
            "VLLM_DYNAMIC_UNLOAD_UNSUPPORTED": _error((
                "Switching to a DIFFERENT model requires unloading the resident one, which vLLM cannot do at runtime."
            ), "status=failed, failed_stage=unloading", "Restart the runtime with the target model instead; switching to the resident model itself succeeds."),
            "MODEL_LOAD_FAILED": _error("The final load failed.", "status=failed, failed_stage=loading", "Inspect the load reason; the target was never marked resident."),
        },
        "best_practices": ["Queued command: the client waits for the job.", "On single-process vLLM a cross-model switch is bounded by the unload limitation."],
    },
    "local_lmcache_status": {
        "category": "lmcache",
        "detailed_description": (
            "Report LMCache enablement, per-tier usage and hit accounting "
            "from two independent measured sources: the disk tier from the "
            "storage path on disk, and the hit/miss token counters from the "
            "runtime's metrics endpoint (the external KV-connector families, "
            "counted in tokens). A source that does not answer is named in "
            "the metadata instead of contributing zeros that would read as "
            "measurements. The GPU-internal prefix cache is carried as "
            "context only and never mixed into the LMCache figures."
        ),
        "parameters": {},
        "return_value": {
            "success": {
                "description": "The LMCache status.",
                "data": {
                    "enabled": "Whether LMCache is configured on.",
                    "hit_tokens": "Tokens served from the external cache.",
                    "miss_tokens": "Lookup tokens minus hits.",
                    "disk_cache_usage_bytes": "Measured disk-tier usage.",
                    "metadata": "Which sources answered (runtime_counters_available, disk_tier_observed) and the GPU-internal context.",
                },
            },
        },
        "usage_examples": [{"description": "Cache effectiveness", "command": {}}],
        "error_cases": {},
        "best_practices": ["Check metadata before interpreting a zero: a zero counter with runtime_counters_available=false was not measured."],
    },
    "local_lmcache_purge": {
        "category": "lmcache",
        "detailed_description": (
            "Remove cached LMCache disk artifacts, globally or scoped to a "
            "namespace and/or session binding. Touches neither admission nor "
            "the disk model cache; enabling or resizing LMCache remains a "
            "configuration change."
        ),
        "parameters": {
            "namespace": {"type": "string", "required": False, "description": "Namespace binding to scope the purge to."},
            "session": {"type": "string", "required": False, "description": "Session binding to scope the purge to."},
        },
        "return_value": {
            "success": {"description": "The purge summary.", "data": {"scope": "global or the binding descriptor.", "removed_count": "Files removed."}},
        },
        "usage_examples": [
            {"description": "Purge everything", "command": {}},
            {"description": "Purge one session", "command": {"namespace": "alpha", "session": "s1"}},
        ],
        "error_cases": {},
        "best_practices": ["removed_count=0 with an existing storage path simply means nothing matched the scope."],
    },
}


def command_documentation(name: str) -> Mapping[str, Any]:
    """Return the documentation entry of one command.

    Args:
        name: Public command name.

    Returns:
        The documentation mapping; empty for an unknown name, so a caller can
        always merge it.
    """
    return COMMAND_DOCS.get(name, {})
