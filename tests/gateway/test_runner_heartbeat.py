from gateway.platforms.base import SendResult
from gateway.run import _should_send_fresh_heartbeat


def test_heartbeat_sends_fresh_message_before_first_message_exists():
    assert _should_send_fresh_heartbeat(None) is True


def test_heartbeat_does_not_resend_when_edit_succeeds():
    assert _should_send_fresh_heartbeat(
        SendResult(success=True, message_id="123")
    ) is False


def test_heartbeat_does_not_resend_on_retryable_edit_failure():
    assert _should_send_fresh_heartbeat(
        SendResult(success=False, error="httpx.ConnectError", retryable=True)
    ) is False


def test_heartbeat_resends_on_nonretryable_edit_failure():
    assert _should_send_fresh_heartbeat(
        SendResult(success=False, error="message to edit not found", retryable=False)
    ) is True
