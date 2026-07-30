"""Tests for the registration state reported by the info command.

The adapter framework maintains one live registration snapshot rewritten by
every heartbeat attempt; the info command reads it through a builder. The rules
under test: live facts come through unchanged, the missing heartbeat timestamp
is declared rather than invented, and a failing probe reports itself
unavailable with the failure named.

Author: Vasiliy Zdanovskiy
email: vasilyvz@gmail.com
"""

from __future__ import annotations

from lmrs.adapter import info as info_module
from lmrs.proxy.lifecycle import registration_state_from_adapter_snapshot

REGISTERED_SNAPSHOT = {
    "enabled": True,
    "registered": True,
    "proxy_url": "https://192.168.254.26:3005/proxy/heartbeat",
    "server_url": "https://192.168.254.26:8012",
    "server_name": "lmrs",
}


def test_a_registered_snapshot_maps_to_a_registered_state() -> None:
    """The live flags and identifiers come through unchanged."""
    state = registration_state_from_adapter_snapshot(REGISTERED_SNAPSHOT)

    assert state.registered is True
    assert state.proxy_recognized is True
    assert state.heartbeat_fresh is True
    assert state.server_name == "lmrs"
    assert state.metadata["enabled"] is True
    assert state.metadata["proxy_url"] == "https://192.168.254.26:3005/proxy/heartbeat"


def test_the_missing_heartbeat_timestamp_is_declared_not_invented() -> None:
    """No timestamp exists in the snapshot, so none is reported."""
    state = registration_state_from_adapter_snapshot(REGISTERED_SNAPSHOT)

    assert state.last_heartbeat_at is None
    assert state.metadata["heartbeat_timestamp_available"] is False
    assert "no timestamp" in state.metadata["heartbeat_timestamp_note"] or "records no timestamp" in state.metadata["heartbeat_timestamp_note"]


def test_an_unregistered_snapshot_maps_to_an_unregistered_state() -> None:
    """A failed or disabled registration is reported as exactly that."""
    state = registration_state_from_adapter_snapshot({"enabled": True, "registered": False})

    assert state.registered is False
    assert state.proxy_recognized is False
    assert state.heartbeat_fresh is False
    assert state.server_name is None


def test_an_empty_snapshot_defaults_to_disabled() -> None:
    """A snapshot with nothing recorded reports a disabled registration."""
    state = registration_state_from_adapter_snapshot({})

    assert state.registered is False
    assert state.metadata["enabled"] is False


def test_info_reports_the_live_registration_state(monkeypatch) -> None:
    """The info payload carries the built state with available=True."""
    monkeypatch.setattr(info_module, "_registration_snapshot", lambda: dict(REGISTERED_SNAPSHOT))

    payload = info_module.build_info_payload(None)
    registration = payload["runtime_summary"]["registration"]

    assert registration["available"] is True
    assert registration["registered"] is True
    assert registration["server_name"] == "lmrs"
    assert registration["metadata"]["source"] == "mcp_proxy_adapter registration snapshot"


def test_info_names_a_failing_registration_probe(monkeypatch) -> None:
    """A probe failure is reported as unavailable with the failure named."""
    def broken() -> dict[str, object]:
        raise RuntimeError("snapshot backend gone")

    monkeypatch.setattr(info_module, "_registration_snapshot", broken)

    payload = info_module.build_info_payload(None)
    registration = payload["runtime_summary"]["registration"]

    assert registration["available"] is False
    assert "snapshot backend gone" in registration["reason"]
