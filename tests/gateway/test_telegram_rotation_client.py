"""Tests for gateway.telegram_rotation_client."""

from __future__ import annotations

from unittest.mock import Mock, patch

from gateway.telegram_rotation_client import TelegramRotationEvent, report_telegram_rotation_event


def test_event_payload_includes_core_fields():
    event = TelegramRotationEvent(
        profile_id="coder",
        error_class="ConnectError",
        error_message="boom",
        event_type="polling_network_error",
        source="gateway.platforms.telegram.TelegramAdapter._handle_polling_network_error",
        attempt=2,
    )
    payload = event.to_payload()
    assert payload["selector_name"] == "电报消息"
    assert payload["profile_id"] == "coder"
    assert payload["error"]["class"] == "ConnectError"
    assert payload["error"]["message"] == "boom"
    assert payload["attempt"] == 2


def test_report_telegram_rotation_event_swallow_errors(monkeypatch):
    with patch("gateway.telegram_rotation_client.urllib_request.urlopen", side_effect=OSError("offline")):
        # Should not raise.
        report_telegram_rotation_event(
            error=RuntimeError("boom"),
            event_type="polling_network_error",
            profile_id="coder",
            source="tests",
        )
