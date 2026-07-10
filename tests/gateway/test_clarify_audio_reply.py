"""Clarify text interception for Telegram voice replies."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


SESSION_KEY = "agent:main:telegram:dm:chat-1"


def _clear_clarify_state() -> None:
    from tools import clarify_gateway as cm

    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()


def _source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="chat-1",
        chat_type="dm",
        user_id="user-1",
        user_name="Tester",
    )


def _event(
    text: str = "",
    *,
    message_type: MessageType = MessageType.TEXT,
    media_urls: list[str] | None = None,
    media_types: list[str] | None = None,
) -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=message_type,
        source=_source(),
        message_id="msg-1",
        media_urls=media_urls or [],
        media_types=media_types or [],
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig()
    runner.adapters = {}
    runner.hooks = SimpleNamespace(emit_collect=AsyncMock(return_value=[]))
    runner._voice_mode = {}
    runner._update_prompt_pending = {}
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_key_for_source = lambda _source: SESSION_KEY
    runner._prepare_inbound_message_text = AsyncMock(
        side_effect=AssertionError("inbound prep should not run")
    )
    return runner


@pytest.fixture(autouse=True)
def clear_clarify_state():
    _clear_clarify_state()
    yield
    _clear_clarify_state()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("2", "Beta"),
        ("custom answer", "custom answer"),
    ],
)
async def test_text_reply_resolves_pending_choice_clarify(reply: str, expected: str):
    from tools import clarify_gateway as cm

    runner = _make_runner()
    event = _event(reply)
    cm.register("clarify-text", SESSION_KEY, "Pick one", ["Alpha", "Beta"])

    resolved = await runner._maybe_intercept_clarify_reply(
        event,
        event.source,
        SESSION_KEY,
    )

    assert resolved is True
    assert cm._entries["clarify-text"].response == expected
    assert cm._entries["clarify-text"].event.is_set()
    runner._prepare_inbound_message_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_voice_reply_uses_transcript_to_resolve_pending_clarify():
    from tools import clarify_gateway as cm

    runner = _make_runner()
    runner._prepare_inbound_message_text = AsyncMock(return_value="  spoken answer  ")
    event = _event(
        message_type=MessageType.VOICE,
        media_urls=["/tmp/voice.ogg"],
        media_types=["audio/ogg"],
    )
    cm.register("clarify-voice", SESSION_KEY, "Say anything", None)

    resolved = await runner._maybe_intercept_clarify_reply(
        event,
        event.source,
        SESSION_KEY,
    )

    assert resolved is True
    runner._prepare_inbound_message_text.assert_awaited_once_with(
        event=event,
        source=event.source,
        history=[],
        session_key=SESSION_KEY,
    )
    assert cm._entries["clarify-voice"].response == "spoken answer"
    assert cm._entries["clarify-voice"].event.is_set()


@pytest.mark.asyncio
async def test_voice_reply_empty_transcript_leaves_clarify_pending():
    from tools import clarify_gateway as cm

    runner = _make_runner()
    runner._prepare_inbound_message_text = AsyncMock(return_value="  ")
    event = _event(
        message_type=MessageType.VOICE,
        media_urls=["/tmp/voice.ogg"],
        media_types=["audio/ogg"],
    )
    cm.register("clarify-empty", SESSION_KEY, "Say anything", None)

    resolved = await runner._maybe_intercept_clarify_reply(
        event,
        event.source,
        SESSION_KEY,
    )

    assert resolved is False
    runner._prepare_inbound_message_text.assert_awaited_once()
    pending = cm.get_pending_for_session(SESSION_KEY, include_choice_prompts=True)
    assert pending is not None
    assert pending.clarify_id == "clarify-empty"


@pytest.mark.asyncio
async def test_audio_reply_does_not_invoke_stt_and_leaves_clarify_pending():
    from tools import clarify_gateway as cm

    runner = _make_runner()
    event = _event(
        message_type=MessageType.AUDIO,
        media_urls=["/tmp/song.mp3"],
        media_types=["audio/mpeg"],
    )
    cm.register("clarify-audio", SESSION_KEY, "Say anything", None)

    resolved = await runner._maybe_intercept_clarify_reply(
        event,
        event.source,
        SESSION_KEY,
    )

    assert resolved is False
    runner._prepare_inbound_message_text.assert_not_awaited()
    pending = cm.get_pending_for_session(SESSION_KEY, include_choice_prompts=True)
    assert pending is not None
    assert pending.clarify_id == "clarify-audio"
