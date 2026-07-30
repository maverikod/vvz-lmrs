"""Tests for the acceptance profile of the all-commands live check.

Two things are pinned here. First the verdict logic, because this check once
reported eighteen green commands while chat was answering "vLLM unavailable".
Second the model separation: the disk-cache commands now delete real weights, so
the acceptance run must never point them at the model the server is serving.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import pytest

from pipeline.checks_live import _ORDER, _acceptance_models, _arguments, _verdict

SERVED = "acme/served-model"


def test_the_served_model_must_be_configured(monkeypatch) -> None:
    """Without a served model name the run proves nothing and says so."""
    monkeypatch.delenv("LMRS_LIVE_MODEL", raising=False)

    with pytest.raises(RuntimeError, match="LMRS_LIVE_MODEL"):
        _acceptance_models()


def test_the_scratch_model_may_not_be_the_served_model(monkeypatch) -> None:
    """Pointing the cache commands at the served model is refused."""
    monkeypatch.setenv("LMRS_LIVE_MODEL", SERVED)
    monkeypatch.setenv("LMRS_LIVE_CACHE_MODEL", SERVED)

    with pytest.raises(RuntimeError, match="delete"):
        _acceptance_models()


def test_cache_commands_act_on_the_scratch_model(monkeypatch) -> None:
    """Destructive cache commands never name the served model."""
    monkeypatch.setenv("LMRS_LIVE_MODEL", SERVED)
    monkeypatch.delenv("LMRS_LIVE_CACHE_MODEL", raising=False)

    _served, scratch = _acceptance_models()

    assert _arguments("local_model_cache_delete") == {"model_name": scratch}
    assert _arguments("local_model_cache_preload") == {"model_name": scratch}
    assert _arguments("local_model_cache_status") == {"model_name": scratch}
    assert scratch != SERVED


def test_runtime_commands_act_on_the_served_model(monkeypatch) -> None:
    """Lifecycle and generation commands drive the deployed model."""
    monkeypatch.setenv("LMRS_LIVE_MODEL", SERVED)

    assert _arguments("chat")["model_name"] == SERVED
    assert _arguments("local_model_load") == {"model_name": SERVED}
    assert _arguments("local_model_switch") == {"model_name": SERVED}


def test_the_scratch_model_is_preloaded_before_it_is_inspected_and_deleted() -> None:
    """The order fetches the scratch model, reads it, then removes it."""
    order = list(_ORDER)

    assert order.index("local_model_cache_preload") < order.index("local_model_cache_status")
    assert order.index("local_model_cache_status") < order.index("local_model_cache_delete")


def test_a_transport_level_failure_is_a_failure() -> None:
    """An envelope reporting success=false fails with its stable code."""
    succeeded, code = _verdict({"result": {"success": False, "error": {"data": {"code": "COMMAND_FAILED"}}}})

    assert succeeded is False
    assert code == "COMMAND_FAILED"


def test_a_negative_domain_outcome_is_a_failure() -> None:
    """A command that ran but reports a negative outcome fails."""
    succeeded, code = _verdict(
        {"result": {"success": True, "data": {"payload": {"success": False, "reason_code": "MODEL_NOT_CACHED"}}}}
    )

    assert succeeded is False
    assert code == "MODEL_NOT_CACHED"


def test_a_queued_job_envelope_is_unwrapped() -> None:
    """A queued command is judged by the command result inside its job."""
    succeeded, code = _verdict(
        {"result": {"job_id": "job-1", "result": {"success": True, "data": {"payload": {"status": "failed", "reason_code": "MODEL_UNLOAD_FAILED"}}}}}
    )

    assert succeeded is False
    assert code == "MODEL_UNLOAD_FAILED"


def test_a_healthy_result_passes() -> None:
    """A command that succeeded at both layers passes."""
    succeeded, code = _verdict({"result": {"success": True, "data": {"payload": {"success": True}}}})

    assert succeeded is True
    assert code == ""
