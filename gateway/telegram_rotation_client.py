"""Best-effort event client for Telegram proxy rotation helper.

This module is intentionally tiny. It lets Telegram adapter code emit a
fire-and-forget failure event without importing or coupling to the helper's
rotation logic.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

DEFAULT_EVENT_URL = os.getenv(
    "HERMES_TELEGRAM_ROTATOR_EVENT_URL",
    "http://127.0.0.1:17893/v1/telegram-rotation/events",
)
DEFAULT_TIMEOUT = float(os.getenv("HERMES_TELEGRAM_ROTATOR_EVENT_TIMEOUT", "0.35"))
DEFAULT_PROFILE = os.getenv("HERMES_PROFILE", "") or os.getenv("HERMES_ACTIVE_PROFILE", "") or "default"


@dataclass(slots=True)
class TelegramRotationEvent:
    schema_version: int = 1
    selector_name: str = "电报消息"
    profile_id: str = DEFAULT_PROFILE
    adapter: str = "telegram"
    event_type: str = "polling_network_error"
    severity: str = "error"
    observed_at: str = ""
    error_class: str = ""
    error_message: str = ""
    retryable: bool = True
    attempt: int | None = None
    mode: str = "polling"
    source: str = ""
    meta: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "selector_name": self.selector_name,
            "profile_id": self.profile_id,
            "adapter": self.adapter,
            "event_type": self.event_type,
            "severity": self.severity,
            "observed_at": self.observed_at or _utc_now(),
            "error": {
                "class": self.error_class,
                "message": self.error_message,
                "retryable": self.retryable,
            },
            "mode": self.mode,
            "source": self.source,
        }
        if self.attempt is not None:
            payload["attempt"] = int(self.attempt)
        if self.meta:
            payload["meta"] = self.meta
        return payload


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _send_sync(event: TelegramRotationEvent, url: str = DEFAULT_EVENT_URL, timeout: float = DEFAULT_TIMEOUT) -> None:
    data = json.dumps(event.to_payload(), ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            # We don't need the response body; just drain it for a clean close.
            resp.read()
    except (urllib_error.URLError, TimeoutError, OSError) as exc:
        logger.debug("Telegram rotation event drop: %s", exc)


def report_telegram_rotation_event(
    *,
    error: Exception,
    event_type: str,
    attempt: int | None = None,
    retryable: bool = True,
    source: str = "",
    mode: str = "polling",
    selector_name: str = "电报消息",
    profile_id: str = DEFAULT_PROFILE,
    meta: dict[str, Any] | None = None,
    url: str = DEFAULT_EVENT_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> None:
    """Fire-and-forget event submitter.

    The caller should never await this. Failures are swallowed.
    """
    event = TelegramRotationEvent(
        selector_name=selector_name,
        profile_id=profile_id,
        adapter="telegram",
        event_type=event_type,
        observed_at=_utc_now(),
        error_class=error.__class__.__name__,
        error_message=str(error),
        retryable=retryable,
        attempt=attempt,
        mode=mode,
        source=source,
        meta=meta,
    )
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _send_sync(event, url=url, timeout=timeout)
        return

    loop.run_in_executor(None, _send_sync, event, url, timeout)
