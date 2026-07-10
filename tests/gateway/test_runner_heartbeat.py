"""Tests for gateway long-running heartbeat replacement-send decisions."""

from types import SimpleNamespace

import pytest

from gateway.platforms.base import SendResult
from gateway.run import _heartbeat_should_send_replacement


@pytest.mark.parametrize(
    ("edit_result", "expected"),
    [
        (None, True),
        (SendResult(success=True, message_id="heartbeat-1"), False),
        (SendResult(success=False, error="timeout", retryable=True), False),
        (SendResult(success=False, error="not found", retryable=False), True),
        (SimpleNamespace(success=False), True),
    ],
)
def test_heartbeat_replacement_send_decision(edit_result, expected):
    assert _heartbeat_should_send_replacement(edit_result) is expected
