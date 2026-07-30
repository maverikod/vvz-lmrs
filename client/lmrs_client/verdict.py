"""The three-layer success verdict for LMRS command responses.

The framework client does not raise on a structured error: a failed command
comes back as an envelope carrying ``result.success = false`` and an ``error``
object; a command that ran but reports a negative domain outcome carries
``payload.success = false`` (or ``payload.status = "failed"``) with a stable
``reason_code``; and a queued command wraps either inside a job envelope.
Treating "no exception" as success once made an acceptance run report green
while the runtime was down, so every consumer of a response - the CLI's exit
code, the acceptance checks - judges it through this one function.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from typing import Any


def verdict(response: Any) -> tuple[bool, str]:
    """Decide whether one command call succeeded, and name the failure if not.

    Args:
        response: The value the client returned for one command.

    Returns:
        A pair of (succeeded, code); code is empty on success and otherwise
        carries the stable error or reason code the server reported.
    """
    if not isinstance(response, dict):
        return True, ""

    result = response.get("result", response)
    # A queued command answers with a job envelope whose own "result" holds the
    # command result. Without unwrapping it, every queued command looked like a
    # pass no matter what it reported.
    if isinstance(result, dict) and "job_id" in result and isinstance(result.get("result"), dict):
        result = result["result"]

    if not isinstance(result, dict):
        return True, ""

    if result.get("success") is False:
        error = result.get("error")
        if isinstance(error, dict):
            data = error.get("data")
            code = data.get("code") if isinstance(data, dict) else None
            return False, str(code or error.get("code") or "UNKNOWN_ERROR")
        return False, "UNKNOWN_ERROR"

    data = result.get("data")
    payload = data.get("payload") if isinstance(data, dict) else None
    if isinstance(payload, dict):
        # Domain results report a negative outcome two ways: a success flag
        # (lifecycle and cache commands) or a status string (model switch).
        if payload.get("success") is False or payload.get("status") == "failed":
            return False, str(payload.get("reason_code") or "UNKNOWN_REASON")
    return True, ""


def command_payload(response: Any) -> dict[str, Any]:
    """Extract the command payload from a response envelope.

    Args:
        response: The value the client returned for one command.

    Returns:
        The payload mapping, or an empty dict when the shape does not match.
    """
    if not isinstance(response, dict):
        return {}
    result = response.get("result", response)
    if isinstance(result, dict) and "job_id" in result and isinstance(result.get("result"), dict):
        result = result["result"]
    if not isinstance(result, dict):
        return {}
    data = result.get("data")
    payload = data.get("payload") if isinstance(data, dict) else None
    return payload if isinstance(payload, dict) else {}
