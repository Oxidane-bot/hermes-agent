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
                                # May contain multiple keys separated by
                                # comma, semicolon, or whitespace.
    TAVILY_API_KEYS=...          # optional explicit multi-key pool;
                                # comma/newline list or JSON array
    TAVILY_BASE_URL=...          # optional override of https://api.tavily.com
    TAVILY_KEY_COOLDOWN_SECONDS=172800
                                # optional cooldown after quota/auth failures

Auth note: Tavily uses ``api_key`` in the JSON body for /search and
/extract, but **also requires** ``Authorization: Bearer <key>`` for /crawl
(body-only auth returns 401 on /crawl). The plugin handles both.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from hashlib import sha256
from typing import Any, Dict, List, Mapping, Optional

from agent.web_search_provider import WebSearchProvider

logger = logging.getLogger(__name__)

_TAVILY_DEFAULT_KEY_COOLDOWN_SECONDS = 2 * 24 * 60 * 60
_TAVILY_TRANSIENT_HTTP_STATUSES = {408, 409, 425, 500, 502, 503, 504, 529}
_TAVILY_QUOTA_HTTP_STATUSES = {401, 402, 403, 429}
_TAVILY_STATE_LOCK = threading.Lock()


def _split_tavily_api_keys(raw: str) -> List[str]:
    """Split one env var value into one or more Tavily API keys."""
    stripped = raw.strip()
    if stripped.startswith("["):
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(part).strip() for part in parsed if str(part).strip()]
    return [part.strip() for part in re.split(r"[\s,;]+", raw) if part.strip()]


def _get_tavily_api_keys() -> List[str]:
    """Return configured Tavily API keys in fill-first order.

    Backward compatible with a single ``TAVILY_API_KEY`` while also allowing
    multiple keys via comma/newline-separated ``TAVILY_API_KEY`` or the
    explicit plural ``TAVILY_API_KEYS``. Numbered vars are accepted for users
    who prefer one key per line in process managers:
    ``TAVILY_API_KEY_1``, ``TAVILY_API_KEY_2``, ...
    """
    raw_values: List[str] = []
    for name in ("TAVILY_API_KEY", "TAVILY_API_KEYS"):
        value = os.getenv(name, "")
        if value.strip():
            raw_values.append(value)

    numbered: list[tuple[int, str]] = []
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


def _tavily_key_fingerprint(api_key: str) -> str:
    """Stable non-secret key identifier for cooldown state and logs."""
    return sha256(api_key.encode("utf-8")).hexdigest()[:16]


def _tavily_state_path() -> str:
    try:
        from hermes_constants import get_hermes_home

        base = get_hermes_home()
    except ImportError:
        from pathlib import Path

        base = Path.home() / ".hermes"
    return str(base / "rate_limits" / "tavily_keys.json")


def _load_tavily_key_state() -> Dict[str, Any]:
    path = _tavily_state_path()
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _save_tavily_key_state(state: Dict[str, Any]) -> None:
    try:
        from utils import atomic_json_write

        atomic_json_write(_tavily_state_path(), state)
    except Exception as exc:  # noqa: BLE001 — state is best-effort
        logger.debug("Failed to persist Tavily key state: %s", exc)


def _configured_tavily_key_cooldown_seconds() -> float:
    raw = os.getenv("TAVILY_KEY_COOLDOWN_SECONDS", "").strip()
    if not raw:
        return float(_TAVILY_DEFAULT_KEY_COOLDOWN_SECONDS)
    try:
        return max(60.0, float(raw))
    except ValueError:
        logger.debug("Ignoring invalid TAVILY_KEY_COOLDOWN_SECONDS=%r", raw)
        return float(_TAVILY_DEFAULT_KEY_COOLDOWN_SECONDS)


def _parse_retry_after_seconds(headers: Optional[Mapping[str, str]]) -> Optional[float]:
    if not headers:
        return None
    lowered = {str(k).lower(): str(v) for k, v in headers.items()}
    for key in (
        "retry-after",
        "x-ratelimit-reset",
        "x-ratelimit-reset-requests",
        "x-ratelimit-reset-requests-1h",
    ):
        raw = lowered.get(key)
        if raw is None:
            continue
        try:
            seconds = float(raw)
        except ValueError:
            continue
        if seconds > 0:
            return seconds
    return None


def _tavily_key_cooldown_remaining(api_key: str, *, now: Optional[float] = None) -> float:
    now = time.time() if now is None else now
    fingerprint = _tavily_key_fingerprint(api_key)
    with _TAVILY_STATE_LOCK:
        state = _load_tavily_key_state()
        entry = (state.get("keys") or {}).get(fingerprint) or {}
    try:
        cooldown_until = float(entry.get("cooldown_until") or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, cooldown_until - now)


def _available_tavily_api_keys(keys: List[str]) -> List[str]:
    """Return keys not currently cooled down, preserving configured order."""
    now = time.time()
    return [key for key in keys if _tavily_key_cooldown_remaining(key, now=now) <= 0]


def _record_tavily_key_success(api_key: str) -> None:
    fingerprint = _tavily_key_fingerprint(api_key)
    now = time.time()
    with _TAVILY_STATE_LOCK:
        state = _load_tavily_key_state()
        keys_state = state.get("keys") or {}
        entry = keys_state.get(fingerprint)
        if not isinstance(entry, dict):
            return
        entry.pop("cooldown_until", None)
        entry.pop("last_error", None)
        entry.pop("last_status", None)
        entry["last_success_at"] = now
        entry["updated_at"] = now
        state["keys"] = keys_state
        _save_tavily_key_state(state)


def _record_tavily_key_cooldown(
    api_key: str,
    *,
    reason: str,
    status_code: Optional[int] = None,
    headers: Optional[Mapping[str, str]] = None,
    error: Optional[BaseException] = None,
) -> float:
    now = time.time()
    cooldown_seconds = _parse_retry_after_seconds(headers)
    if cooldown_seconds is None:
        cooldown_seconds = _configured_tavily_key_cooldown_seconds()
    cooldown_until = now + cooldown_seconds
    fingerprint = _tavily_key_fingerprint(api_key)
    with _TAVILY_STATE_LOCK:
        state = _load_tavily_key_state()
        keys_state = state.setdefault("keys", {})
        entry = keys_state.setdefault(fingerprint, {})
        entry.update(
            {
                "cooldown_until": cooldown_until,
                "reason": reason,
                "last_status": status_code,
                "last_error": str(error)[:300] if error else "",
                "updated_at": now,
            }
        )
        entry["failure_count"] = int(entry.get("failure_count") or 0) + 1
        _save_tavily_key_state(state)
    return cooldown_seconds


def _http_status_from_exception(exc: BaseException) -> Optional[int]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code if isinstance(status_code, int) else None


def _http_headers_from_exception(exc: BaseException) -> Optional[Mapping[str, str]]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    return headers if isinstance(headers, Mapping) else None


def _is_tavily_quota_or_auth_failure(exc: BaseException) -> bool:
    status_code = _http_status_from_exception(exc)
    if status_code in _TAVILY_QUOTA_HTTP_STATUSES:
        return True
    text = str(exc).lower()
    return any(token in text for token in ("quota", "rate limit", "rate_limit", "usage limit", "credit"))


def _is_tavily_retryable_without_cooldown(exc: BaseException) -> bool:
    status_code = _http_status_from_exception(exc)
    if status_code is None:
        return True
    return status_code in _TAVILY_TRANSIENT_HTTP_STATUSES


def _tavily_request(endpoint: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """POST to the Tavily API and return the parsed JSON response.

    Mirrors :func:`tools.web_tools._tavily_request`. Raises ``ValueError``
    when ``TAVILY_API_KEY`` is unset; the caller catches and surfaces as
    a typed error response.
    """
    import httpx

    api_keys = _get_tavily_api_keys()
    if not api_keys:
        raise ValueError(
            "TAVILY_API_KEY environment variable not set. "
            "Get your API key at https://app.tavily.com/home"
        )
    available_keys = _available_tavily_api_keys(api_keys)
    if not available_keys:
        waits = [
            _tavily_key_cooldown_remaining(key)
            for key in api_keys
        ]
        next_retry = min((wait for wait in waits if wait > 0), default=0)
        raise ValueError(
            "All configured Tavily API keys are cooling down after recent "
            f"quota/auth failures; next retry in {int(next_retry)}s"
        )

    base_url = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")
    payload_template = dict(payload)  # don't mutate caller's dict
    url = f"{base_url}/{endpoint.lstrip('/')}"
    logger.info("Tavily %s request to %s", endpoint, url)

    last_error: Optional[BaseException] = None
    for index, api_key in enumerate(available_keys, start=1):
        payload_for_key = dict(payload_template)
        payload_for_key["api_key"] = api_key
        # Tavily /crawl requires Bearer header auth in addition to body auth;
        # /search and /extract are body-only.
        headers = {"Authorization": f"Bearer {api_key}"} if endpoint.strip("/") == "crawl" else {}

        try:
            response = httpx.post(url, json=payload_for_key, headers=headers, timeout=60)
            response.raise_for_status()
            _record_tavily_key_success(api_key)
            return response.json()
        except Exception as exc:  # noqa: BLE001 — failover handles HTTP + transport errors
            last_error = exc
            status_code = _http_status_from_exception(exc)
            fingerprint = _tavily_key_fingerprint(api_key)
            remaining = len(available_keys) - index
            if _is_tavily_quota_or_auth_failure(exc):
                cooldown = _record_tavily_key_cooldown(
                    api_key,
                    reason="quota_or_auth_failure",
                    status_code=status_code,
                    headers=_http_headers_from_exception(exc),
                    error=exc,
                )
                logger.warning(
                    "Tavily key %s failed with status %s; cooling down for %.0fs%s",
                    fingerprint,
                    status_code,
                    cooldown,
                    f" and trying {remaining} more key(s)" if remaining else "",
                )
                if remaining:
                    continue
            elif _is_tavily_retryable_without_cooldown(exc) and remaining:
                logger.warning(
                    "Tavily key %s hit transient error (%s); trying %d more key(s)",
                    fingerprint,
                    exc,
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
        """Return True when at least one Tavily API key is configured."""
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
                    "prompt": "Tavily API key(s)",
                    "url": "https://app.tavily.com/home",
                },
            ],
        }
