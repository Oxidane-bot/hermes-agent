"""Regression tests for voice replies to pending gateway clarify prompts."""

from __future__ import annotations

from types import MethodType

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


def _clear_clarify_state():
    from tools import clarify_gateway as cm

    with cm._lock:
        cm._entries.clear()
        cm._session_index.clear()
        cm._notify_cbs.clear()


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        chat_id="12345",
        chat_type="dm",
        user_id="777",
        user_name="Tester",
    )


def _make_runner():
    from gateway.run import GatewayRunner

    return object.__new__(GatewayRunner)


@pytest.fixture(autouse=True)
def clear_clarify_state():
    _clear_clarify_state()
    yield
    _clear_clarify_state()


@pytest.mark.asyncio
async def test_pending_clarify_resolves_text_reply_without_preprocessing():
    from tools import clarify_gateway as cm

    runner = _make_runner()
    session_key = "agent:main:telegram:dm:12345"
    source = _make_source()
    cm.register("cid-text", session_key, "Answer?", None)

    event = MessageEvent(
        text="plain answer",
        message_type=MessageType.TEXT,
        source=source,
    )

    resolved = await runner._maybe_resolve_pending_clarify(
        session_key=session_key,
        event=event,
        source=source,
    )

    assert resolved is True
    assert cm.wait_for_response("cid-text", timeout=0.1) == "plain answer"


@pytest.mark.asyncio
async def test_pending_clarify_voice_reply_is_transcribed_before_resolve():
    from tools import clarify_gateway as cm

    runner = _make_runner()
    session_key = "agent:main:telegram:dm:12345"
    source = _make_source()
    cm.register("cid-voice", session_key, "Say the answer", None)

    calls = []

    async def fake_prepare(self, *, event, source, history):
        calls.append((event, source, history))
        return "transcribed answer"

    runner._prepare_inbound_message_text = MethodType(fake_prepare, runner)
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/voice.ogg"],
        media_types=["audio/ogg"],
    )

    resolved = await runner._maybe_resolve_pending_clarify(
        session_key=session_key,
        event=event,
        source=source,
    )

    assert resolved is True
    assert len(calls) == 1
    assert calls[0][2] == []
    assert cm.wait_for_response("cid-voice", timeout=0.1) == "transcribed answer"


@pytest.mark.asyncio
async def test_pending_clarify_empty_voice_transcript_stays_pending():
    from tools import clarify_gateway as cm

    runner = _make_runner()
    session_key = "agent:main:telegram:dm:12345"
    source = _make_source()
    cm.register("cid-empty", session_key, "Say the answer", None)

    async def fake_prepare(self, *, event, source, history):
        return ""

    runner._prepare_inbound_message_text = MethodType(fake_prepare, runner)
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/voice.ogg"],
        media_types=["audio/ogg"],
    )

    resolved = await runner._maybe_resolve_pending_clarify(
        session_key=session_key,
        event=event,
        source=source,
    )

    assert resolved is False
    assert cm.get_pending_for_session(session_key) is not None
