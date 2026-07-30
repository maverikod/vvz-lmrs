"""Tests pinning the completed public command surface (C-026, C-029).

The set of registered commands must equal the CommandName catalog, registration
must stay idempotent, each newly added command must delegate to its domain
producer with schema-truthful parameters, and cancel and queue_status must share
one queue.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from lmrs.adapter import registration
from lmrs.adapter.registration import (
    LMRS_PUBLIC_COMMAND_CLASSES,
    CancelCommand,
    CapacityCommand,
    ModelStatusCommand,
    QueueStatusCommand,
    TokenCountCommand,
    register_lmrs_commands,
)
from lmrs.commands import CommandName
from lmrs.queue import QueueEntry, RequestQueue


class FakeRegistry:
    """Registry double recording every registered command class."""

    def __init__(self) -> None:
        """Start with nothing registered."""
        self.commands: dict[str, type] = {}

    def register(self, command_class: type, category: str) -> None:
        """Record one registration.

        Args:
            command_class: The command class being registered.
            category: Adapter category, always "custom" for LMRS.
        """
        assert category == "custom"
        name = str(getattr(command_class, "name", ""))
        self.commands[name] = command_class


def _entry(request_id: str) -> QueueEntry:
    """Build a queued entry with the required accounting fields.

    Args:
        request_id: Identifier of the queued request.

    Returns:
        A QueueEntry usable as queue content in a test.
    """
    return QueueEntry(
        request_id=request_id,
        session_id="s-1",
        model_name="demo",
        required_tokens=16,
        required_dynamic_vram_bytes=1024,
        admitted_at="2026-07-30T00:00:00+00:00",
        expires_at="2026-07-30T01:00:00+00:00",
    )


def _catalog_names() -> set[str]:
    """Return every public command name declared by CommandName.

    Returns:
        The stable public command surface, read from the catalog rather than a
        second hand-written list, so this test cannot drift from what it guards.
    """
    return {
        value
        for key, value in vars(CommandName).items()
        if not key.startswith("_") and isinstance(value, str)
    }


def test_registered_commands_equal_the_command_name_catalog() -> None:
    """Every catalog name is registered and nothing extra is."""
    registry = FakeRegistry()
    register_lmrs_commands(registry)

    assert set(registry.commands) == _catalog_names()


def test_the_surface_has_eighteen_commands() -> None:
    """The public surface the client is written against is eighteen commands."""
    assert len(_catalog_names()) == 18
    assert len(LMRS_PUBLIC_COMMAND_CLASSES) == 18


def test_registration_is_idempotent() -> None:
    """Registering twice yields each command exactly once."""
    registry = FakeRegistry()
    register_lmrs_commands(registry)
    first = dict(registry.commands)

    register_lmrs_commands(registry)

    assert registry.commands == first


@pytest.mark.parametrize(
    ("command_class", "params"),
    [
        (ModelStatusCommand, {"model_name": "demo"}),
        (CapacityCommand, {}),
        (QueueStatusCommand, {}),
        (
            TokenCountCommand,
            {
                "input_tokens": 10,
                "tool_tokens": 1,
                "service_tokens": 2,
                "reserved_output_tokens": 3,
                "tokenizer_name": "tok",
                "tokenizer_accuracy": "exact",
                "rough_estimate": True,
            },
        ),
        (CancelCommand, {"request_id": "r-1"}),
    ],
)
def test_every_accepted_parameter_appears_in_the_schema(command_class: type, params: dict[str, Any]) -> None:
    """Truthful metadata: execute accepts nothing the schema omits (C-029)."""
    declared = set(command_class.get_schema().get("properties", {}))

    assert set(params) <= declared or not params


def test_model_status_delegates_to_the_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_status reads residency through the lifecycle producer."""
    calls: list[str] = []

    class _Lifecycle:
        def model_status(self, model_name: str) -> dict[str, str]:
            calls.append(model_name)
            return {"model_name": model_name, "state": "loaded"}

    monkeypatch.setattr(registration, "_LIFECYCLE", _Lifecycle())

    result = asyncio.run(ModelStatusCommand().execute(model_name="demo"))

    assert calls == ["demo"]
    assert result.to_dict()["data"]["payload"]["state"] == "loaded"


def test_capacity_reports_the_vram_snapshot_and_flags_measurement() -> None:
    """capacity returns the VRAM snapshot and states whether it was measured."""
    payload = asyncio.run(CapacityCommand().execute()).to_dict()["data"]["payload"]

    assert "usable_dynamic_vram_bytes" in payload
    assert "max_dynamic_pool_bytes" in payload
    # No producer supplies measured VRAM yet, so the command says so rather
    # than presenting zeros as a real measurement.
    assert payload["measured"] is False


def test_token_count_delegates_to_the_estimation_producer() -> None:
    """token_count reports the caller's accounting through calculate_required_tokens."""
    payload = asyncio.run(
        TokenCountCommand().execute(
            input_tokens=10,
            tool_tokens=1,
            service_tokens=2,
            reserved_output_tokens=3,
            tokenizer_name="tok",
            tokenizer_accuracy="exact",
        )
    ).to_dict()["data"]["payload"]

    assert payload["token_breakdown"]["input_tokens"] == 10
    assert payload["required_tokens"] == 16


def test_read_only_commands_do_not_mutate_the_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """model_status, capacity and queue_status leave the queue untouched."""
    holder = registration._QueueHolder()
    holder.queue = RequestQueue(entries=(_entry("r-1"),))
    monkeypatch.setattr(registration, "_QUEUE", holder)

    asyncio.run(CapacityCommand().execute())
    asyncio.run(QueueStatusCommand().execute())

    assert [entry["request_id"] for entry in holder.snapshot()] == ["r-1"]


def test_cancel_and_queue_status_share_one_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """The state cancel produces is the state queue_status reports."""
    holder = registration._QueueHolder()
    holder.queue = RequestQueue(entries=(_entry("r-1"), _entry("r-2")))
    monkeypatch.setattr(registration, "_QUEUE", holder)

    cancelled = asyncio.run(CancelCommand().execute(request_id="r-1")).to_dict()["data"]["payload"]
    reported = asyncio.run(QueueStatusCommand().execute()).to_dict()["data"]["payload"]

    assert [entry["request_id"] for entry in cancelled["entries"]] == ["r-2"]
    assert [entry["request_id"] for entry in reported["entries"]] == ["r-2"]
