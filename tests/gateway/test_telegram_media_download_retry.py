from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.error import TimedOut

from gateway.platforms.base import MessageType
from plugins.platforms.telegram import adapter as telegram_adapter_module
from plugins.platforms.telegram.adapter import TelegramAdapter


class _DownloadedFile:
    def __init__(self, payload: bytes = b"voice", *, error: Exception | None = None):
        self.file_path = "voice.ogg"
        self._payload = payload
        self._error = error
        self.download_calls = 0

    async def download_as_bytearray(self):
        self.download_calls += 1
        if self._error is not None:
            raise self._error
        return bytearray(self._payload)


class _MediaSource:
    file_size = 5

    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)
        self.get_file_calls = 0

    async def get_file(self):
        self.get_file_calls += 1
        outcome = next(self._outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.asyncio
async def test_media_download_retries_transient_get_file_failure(monkeypatch):
    adapter: Any = object.__new__(TelegramAdapter)
    downloaded = _DownloadedFile(b"ok")
    source = _MediaSource([TimedOut("temporary"), downloaded])
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_adapter_module.asyncio, "sleep", sleep)

    file_obj, payload = await adapter._download_media_with_retry(source, kind="voice message")

    assert file_obj is downloaded
    assert payload == bytearray(b"ok")
    assert source.get_file_calls == 2
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_download_retries_transient_byte_download_failure(monkeypatch):
    adapter: Any = object.__new__(TelegramAdapter)
    failed_file = _DownloadedFile(error=TimedOut("temporary"))
    downloaded = _DownloadedFile(b"ok")
    source = _MediaSource([failed_file, downloaded])
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_adapter_module.asyncio, "sleep", sleep)

    file_obj, payload = await adapter._download_media_with_retry(source, kind="voice message")

    assert file_obj is downloaded
    assert payload == bytearray(b"ok")
    assert source.get_file_calls == 2
    assert failed_file.download_calls == 1
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_download_does_not_retry_non_network_failure(monkeypatch):
    adapter: Any = object.__new__(TelegramAdapter)
    source = _MediaSource([ValueError("invalid payload")])
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_adapter_module.asyncio, "sleep", sleep)

    with pytest.raises(ValueError, match="invalid payload"):
        await adapter._download_media_with_retry(source, kind="voice message")

    assert source.get_file_calls == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_media_download_stops_after_three_network_failures(monkeypatch):
    adapter: Any = object.__new__(TelegramAdapter)
    source = _MediaSource(
        [TimedOut("first"), TimedOut("second"), TimedOut("third")]
    )
    sleep = AsyncMock()
    monkeypatch.setattr(telegram_adapter_module.asyncio, "sleep", sleep)

    with pytest.raises(TimedOut, match="third"):
        await adapter._download_media_with_retry(source, kind="voice message")

    assert source.get_file_calls == 3
    assert sleep.await_count == 2


@pytest.mark.asyncio
async def test_voice_handler_recovers_from_one_transient_download_failure(monkeypatch):
    adapter: Any = object.__new__(TelegramAdapter)
    adapter._max_doc_bytes = 1024
    adapter._is_user_authorized_from_message = lambda _message: True
    adapter._should_process_message = lambda _message: True
    adapter._apply_telegram_group_observe_attribution = lambda event: event
    adapter._telegram_media_size_allowed = lambda _source, _kind: (True, None)
    adapter._build_message_event = lambda _message, _type, update_id=None: SimpleNamespace(
        text="",
        message_type=_type,
        media_urls=[],
        media_types=[],
    )
    adapter.handle_message = AsyncMock()
    adapter._surface_media_cache_failure = AsyncMock()

    downloaded = _DownloadedFile(b"voice-bytes")
    voice = _MediaSource([TimedOut("temporary"), downloaded])
    msg = SimpleNamespace(
        caption=None,
        sticker=None,
        photo=None,
        voice=voice,
        audio=None,
        video=None,
        document=None,
        media_group_id=None,
    )
    update = SimpleNamespace(message=msg, update_id=7)

    monkeypatch.setattr(telegram_adapter_module.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(
        telegram_adapter_module,
        "cache_audio_from_bytes",
        lambda data, ext: "/tmp/retried-voice.ogg",
    )

    await TelegramAdapter._handle_media_message(adapter, update, SimpleNamespace())

    adapter._surface_media_cache_failure.assert_not_awaited()
    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_type is MessageType.VOICE
    assert event.media_urls == ["/tmp/retried-voice.ogg"]
    assert event.media_types == ["audio/ogg"]
    assert voice.get_file_calls == 2
