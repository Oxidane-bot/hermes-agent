"""Tavily web search + content extraction — plugin form.

Subclasses :class:`agent.web_search_provider.WebSearchProvider`. Two
capabilities advertised:

- ``supports_search()``  -> True (Tavily ``/search``)
- ``supports_extract()`` -> True (Tavily ``/extract``)

Both are sync — the underlying call is ``httpx.post(...)``.

Config keys this provider responds to::

    web:
      search_backend: "tavily"     # explicit per-capability
      extract_backend: "tavily"    # explicit per-capability
      backend: "tavily"            # shared fallback for both

Env vars::

    TAVILY_API_KEY=...           # https://app.tavily.com/home (required)
    TAVILY_API_KEYS=...          # optional explicit multi-key pool
    TAVILY_API_KEY_1=...         # optional numbered process-env key
    TAVILY_BASE_URL=...          # optional override of https://api.tavily.com
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Any, Dict, List, Mapping, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_DEFAULT_TAVILY_COOLDOWN_SECONDS = 300.0
_MAX_TAVILY_RETRY_AFTER_SECONDS = 86_400.0
_CONFIG_NUMBERED_TAVILY_KEY_LIMIT = 20
_TAVILY_TRANSIENT_HTTP_STATUSES = {408, 409, 425, 500, 502, 503, 504, 529}
_TAVILY_QUOTA_HTTP_STATUSES = {401, 402, 403, 429}
_TAVILY_STATE_LOCK = threading.Lock()


def _split_tavily_api_keys(raw: str) -> List[str]:
    """Split one env var value into one or more Tavily keys."""
    stripped = (raw or "").strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(part).strip() for part in parsed if str(part).strip()]
    return [part.strip() for part in re.split(r"[\s,;]+", stripped) if part.strip()]


def _get_tavily_api_keys() -> List[str]:
    """Return Tavily keys in stable first-seen order with duplicates removed."""
    from agent.web_search_provider import get_provider_env

    raw_values: List[str] = []
    for name in ("TAVILY_API_KEY", "TAVILY_API_KEYS"):
        value = get_provider_env(name)
        if value:
            raw_values.append(value)

    for index in range(1, _CONFIG_NUMBERED_TAVILY_KEY_LIMIT + 1):
        value = get_provider_env(f"TAVILY_API_KEY_{index}")
        if value:
            raw_values.append(value)

    numbered: List[tuple[int, str]] = []
    for name, value in os.environ.items():
        match = re.fullmatch(r"TAVILY_API_KEY_(\d+)", name)
        if match and value.strip():
            numbered.append((int(match.group(1)), value))
    raw_values.extend(value for _, value in sorted(numbered))

    keys: List[str] = []
    seen: set[str] = set()
    for raw in raw_values:
        for key in _split_tavily_api_keys(raw):
            if key not in seen:
                keys.append(key)
                seen.add(key)
    return keys


def _has_tavily_pool_source() -> bool:
    from agent.web_search_provider import get_provider_env

    if get_provider_env("TAVILY_API_KEYS"):
        return True
    for index in range(1, _CONFIG_NUMBERED_TAVILY_KEY_LIMIT + 1):
        if get_provider_env(f"TAVILY_API_KEY_{index}"):
            return True
    return any(re.fullmatch(r"TAVILY_API_KEY_(\d+)", name) for name in os.environ)


def tavily_key_pool_summary() -> Optional[str]:
    """Return a non-secret user-facing summary for Tavily key pools."""
    keys = _get_tavily_api_keys()
    if not keys or not (_has_tavily_pool_source() or len(keys) > 1):
        return None
    suffix = "key" if len(keys) == 1 else "keys"
    return f"set (pool: {len(keys)} {suffix})"


def _tavily_key_fingerprint(api_key: str) -> str:
    """Stable irreversible key identifier used for persistence and logs."""
    return sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _tavily_state_path() -> str:
    from hermes_constants import get_hermes_home

    return str(get_hermes_home() / "rate_limits" / "tavily_keys.json")


def _load_tavily_key_state() -> Dict[str, Any]:
    try:
        with open(_tavily_state_path(), encoding="utf-8") as fh:
            state = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return state if isinstance(state, dict) else {}


def _save_tavily_key_state(state: Dict[str, Any]) -> None:
    from utils import atomic_json_write

    atomic_json_write(_tavily_state_path(), state)


def _cleanup_tavily_key_state(
    state: Dict[str, Any], *, now: Optional[float] = None
) -> Dict[str, Any]:
    """Drop expired or malformed entries and keep only hashed cooldown state."""
    now = time.time() if now is None else now
    entries = state.get("keys")
    if not isinstance(entries, dict):
        return {}

    cleaned: Dict[str, Dict[str, float]] = {}
    for fingerprint, entry in entries.items():
        if not isinstance(fingerprint, str) or not isinstance(entry, dict):
            continue
        try:
            cooldown_until = float(entry.get("cooldown_until") or 0)
            updated_at = float(entry.get("updated_at") or 0)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(cooldown_until) or not math.isfinite(updated_at):
            continue
        if cooldown_until <= now:
            continue
        cleaned[fingerprint] = {
            "cooldown_until": cooldown_until,
            "updated_at": updated_at,
        }
    return {"keys": cleaned} if cleaned else {}


def _parse_retry_after_seconds(headers: Optional[Mapping[str, str]]) -> Optional[float]:
    if not headers:
        return None
    for key, value in headers.items():
        if str(key).lower() != "retry-after":
            continue
        raw = str(value).strip()
        try:
            seconds = float(raw)
        except ValueError:
            try:
                parsed = parsedate_to_datetime(raw)
            except (TypeError, ValueError, IndexError, OverflowError):
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            seconds = parsed.timestamp() - time.time()
        if not math.isfinite(seconds) or seconds <= 0:
            return None
        return min(seconds, _MAX_TAVILY_RETRY_AFTER_SECONDS)
    return None


def _http_status_from_exception(exc: BaseException) -> Optional[int]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _http_headers_from_exception(exc: BaseException) -> Optional[Mapping[str, str]]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    return headers if isinstance(headers, Mapping) else None


def _response_body_indicates_quota_or_auth(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    if response is None:
        return False

    text = ""
    try:
        parsed = response.json()
    except Exception:  # noqa: BLE001
        parsed = None
    if parsed is not None:
        try:
            text = json.dumps(parsed, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(parsed)
    else:
        body = getattr(response, "text", None)
        if isinstance(body, str):
            text = body

    lowered = text.lower()
    return any(
        token in lowered
        for token in (
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "usage limit",
            "credits",
            "unauthorized",
            "forbidden",
            "invalid api key",
        )
    )


def _is_tavily_quota_or_auth_failure(exc: BaseException) -> bool:
    status_code = _http_status_from_exception(exc)
    if status_code in _TAVILY_QUOTA_HTTP_STATUSES:
        return True
    if _response_body_indicates_quota_or_auth(exc):
        return True
    lowered = str(exc).lower()
    return any(
        token in lowered
        for token in (
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "usage limit",
            "credits",
            "unauthorized",
            "forbidden",
        )
    )


def _is_tavily_retryable_without_cooldown(exc: BaseException) -> bool:
    status_code = _http_status_from_exception(exc)
    if status_code is None:
        return True
    return status_code in _TAVILY_TRANSIENT_HTTP_STATUSES


def _cooldown_remaining_for_key(
    api_key: str, *, now: Optional[float] = None
) -> float:
    now = time.time() if now is None else now
    fingerprint = _tavily_key_fingerprint(api_key)
    with _TAVILY_STATE_LOCK:
        state = _cleanup_tavily_key_state(_load_tavily_key_state(), now=now)
    entry = (state.get("keys") or {}).get(fingerprint) or {}
    try:
        cooldown_until = float(entry.get("cooldown_until") or 0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(cooldown_until):
        return 0.0
    return max(0.0, cooldown_until - now)


def _available_tavily_api_keys(keys: List[str]) -> List[str]:
    """Return keys that are not currently cooling down."""
    now = time.time()
    return [key for key in keys if _cooldown_remaining_for_key(key, now=now) <= 0]


def _record_tavily_key_cooldown(
    api_key: str, *, headers: Optional[Mapping[str, str]] = None
) -> float:
    """Persist cooldown state using only a key fingerprint and timestamps."""
    now = time.time()
    cooldown_seconds = _parse_retry_after_seconds(headers)
    if cooldown_seconds is None:
        cooldown_seconds = _DEFAULT_TAVILY_COOLDOWN_SECONDS

    with _TAVILY_STATE_LOCK:
        state = _cleanup_tavily_key_state(_load_tavily_key_state(), now=now)
        keys_state = state.setdefault("keys", {})
        keys_state[_tavily_key_fingerprint(api_key)] = {
            "cooldown_until": now + cooldown_seconds,
            "updated_at": now,
        }
        _save_tavily_key_state(state)

    return cooldown_seconds


def _tavily_request(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST to the Tavily API and return the parsed JSON response.

    Mirrors :func:`tools.web_tools._tavily_request`. Raises ``ValueError``
    when no Tavily keys are configured; the caller catches and surfaces as
    a typed error response.
    """
    import httpx

    from agent.web_search_provider import get_provider_env

    api_keys = _get_tavily_api_keys()
    if not api_keys:
        raise ValueError(
            "TAVILY_API_KEY environment variable not set. "
            "Get your API key at https://app.tavily.com/home"
        )

    available_keys = _available_tavily_api_keys(api_keys)
    if not available_keys:
        next_retry = min(
            (_cooldown_remaining_for_key(key) for key in api_keys),
            default=0.0,
        )
        raise ValueError(
            "All configured Tavily API keys are cooling down after recent "
            f"quota/auth failures; next retry in {int(next_retry)}s"
        )

    base_url = get_provider_env("TAVILY_BASE_URL") or "https://api.tavily.com"
    url = f"{base_url}/{endpoint.lstrip('/')}"
    logger.info("Tavily %s request to %s", endpoint, url)

    last_error: Optional[BaseException] = None
    for index, api_key in enumerate(available_keys, start=1):
        request_payload = dict(payload)
        request_payload["api_key"] = api_key
        try:
            response = httpx.post(url, json=request_payload, timeout=60)
            response.raise_for_status()
            return response.json()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            fingerprint = _tavily_key_fingerprint(api_key)
            remaining = len(available_keys) - index
            if _is_tavily_quota_or_auth_failure(exc):
                cooldown = _record_tavily_key_cooldown(
                    api_key,
                    headers=_http_headers_from_exception(exc),
                )
                logger.warning(
                    "Tavily key %s unavailable; cooling down for %.0fs%s",
                    fingerprint,
                    cooldown,
                    f" and trying {remaining} more key(s)" if remaining else "",
                )
                if remaining:
                    continue
            elif _is_tavily_retryable_without_cooldown(exc) and remaining:
                logger.warning(
                    "Tavily key %s hit transient error; trying %d more key(s)",
                    fingerprint,
                    remaining,
                )
                continue
            raise

    if last_error is not None:
        raise last_error
    raise ValueError("No Tavily API key available")


def _normalize_tavily_search_results(response: Dict[str, Any]) -> Dict[str, Any]:
    """Map Tavily ``/search`` response to ``{success, data: {web: [...]}}``."""
    web_results = []
    for i, result in enumerate(response.get("results", [])):
        web_results.append(
            {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "description": result.get("content", ""),
                "position": i + 1,
            }
        )
    return {"success": True, "data": {"web": web_results}}


def _normalize_tavily_documents(
    response: Dict[str, Any], fallback_url: str = ""
) -> List[Dict[str, Any]]:
    """Map Tavily ``/extract`` response to standard documents.

    Documents follow the legacy LLM post-processing shape::

        {"url", "title", "content", "raw_content", "metadata"}

    Failures (``failed_results``, ``failed_urls``) become result entries
    with an ``error`` field rather than raising.
    """
    documents: List[Dict[str, Any]] = []
    for result in response.get("results", []):
        url = result.get("url", fallback_url)
        raw = result.get("raw_content", "") or result.get("content", "")
        documents.append(
            {
                "url": url,
                "title": result.get("title", ""),
                "content": raw,
                "raw_content": raw,
                "metadata": {"sourceURL": url, "title": result.get("title", "")},
            }
        )
    for fail in response.get("failed_results", []):
        documents.append(
            {
                "url": fail.get("url", fallback_url),
                "title": "",
                "content": "",
                "raw_content": "",
                "error": fail.get("error", "extraction failed"),
                "metadata": {"sourceURL": fail.get("url", fallback_url)},
            }
        )
    for fail_url in response.get("failed_urls", []):
        url_str = fail_url if isinstance(fail_url, str) else str(fail_url)
        documents.append(
            {
                "url": url_str,
                "title": "",
                "content": "",
                "raw_content": "",
                "error": "extraction failed",
                "metadata": {"sourceURL": url_str},
            }
        )
    return documents


class TavilyWebSearchProvider(WebSearchProvider):
    """Tavily search + extract provider."""

    @property
    def name(self) -> str:
        return "tavily"

    @property
    def display_name(self) -> str:
        return "Tavily"

    def is_available(self) -> bool:
        """Return True when at least one Tavily key is configured."""
        return bool(_get_tavily_api_keys())

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def search(self, query: str, limit: int = 5) -> Dict[str, Any]:
        """Execute a Tavily search."""
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return {"success": False, "error": "Interrupted"}

            logger.info("Tavily search: '%s' (limit=%d)", query, limit)
            raw = _tavily_request(
                "search",
                {
                    "query": query,
                    "max_results": min(limit, 20),
                    "include_raw_content": False,
                    "include_images": False,
                },
            )
            return _normalize_tavily_search_results(raw)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 — including httpx errors
            logger.warning("Tavily search error: %s", exc)
            return {"success": False, "error": f"Tavily search failed: {exc}"}

    def extract(self, urls: List[str], **kwargs: Any) -> List[Dict[str, Any]]:
        """Extract content from one or more URLs via Tavily.

        Sync — the underlying call is httpx.post(...). Returns the legacy
        list-of-results shape; per-URL failures become items with ``error``.
        """
        try:
            from tools.interrupt import is_interrupted

            if is_interrupted():
                return [
                    {"url": u, "error": "Interrupted", "title": ""} for u in urls
                ]

            logger.info("Tavily extract: %d URL(s)", len(urls))
            raw = _tavily_request(
                "extract",
                {
                    "urls": urls,
                    "include_images": False,
                },
            )
            return _normalize_tavily_documents(
                raw, fallback_url=urls[0] if urls else ""
            )
        except ValueError as exc:
            return [{"url": u, "title": "", "content": "", "error": str(exc)} for u in urls]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Tavily extract error: %s", exc)
            return [
                {"url": u, "title": "", "content": "", "error": f"Tavily extract failed: {exc}"}
                for u in urls
            ]

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Tavily",
            "badge": "paid",
            "tag": "Search + extract in one provider.",
            "env_vars": [
                {
                    "key": "TAVILY_API_KEY",
                    "prompt": "Tavily API key",
                    "url": "https://app.tavily.com/home",
                },
                {
                    "key": "TAVILY_API_KEYS",
                    "prompt": "Optional Tavily API key pool (comma-separated or JSON list)",
                    "url": "https://app.tavily.com/home",
                },
                {
                    "key": "TAVILY_API_KEY_1",
                    "prompt": "Optional numbered Tavily API key pool entry",
                    "url": "https://app.tavily.com/home",
                },
            ],
        }
