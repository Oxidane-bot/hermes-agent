"""Tests for Tavily web backend integration.

Coverage:
  _tavily_request() — API key handling, endpoint construction, error propagation.
  _normalize_tavily_search_results() — search response normalization.
  _normalize_tavily_documents() — extract response normalization, failed_results.
  web_search_tool / web_extract_tool — Tavily dispatch paths.
"""

import json
import math
import os
import asyncio
import pytest
from email.utils import formatdate
from unittest.mock import patch, MagicMock

from tests.tools.conftest import register_all_web_providers


@pytest.fixture
def tavily_home(tmp_path, monkeypatch):
    """Isolate Tavily cooldown state and key env vars."""
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    keys = [
        "TAVILY_API_KEY",
        "TAVILY_API_KEYS",
    ]
    keys.extend(
        name
        for name in list(os.environ)
        if name.startswith("TAVILY_API_KEY_") and name.removeprefix("TAVILY_API_KEY_").isdigit()
    )
    for key in keys:
        monkeypatch.delenv(key, raising=False)
    return hermes_home


def _http_status_response(status_code, *, headers=None, body=None):
    """Build a response object that raises an HTTPStatusError."""
    import httpx

    response = MagicMock()
    response.status_code = status_code
    response.headers = headers or {}
    response.json.return_value = body or {}
    response.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"{status_code} error",
        request=httpx.Request("POST", "https://api.tavily.com/search"),
        response=response,
    )
    return response


class TestTavilyKeyPool:
    """Test multi-key parsing and provider availability."""

    def test_parses_delimited_json_and_numbered_keys_with_ordered_dedup(
        self, tavily_home, monkeypatch
    ):
        import hermes_cli.config as config
        from plugins.web.tavily.provider import _get_tavily_api_keys

        monkeypatch.setenv("TAVILY_API_KEY_2", "env-two")
        monkeypatch.setenv("TAVILY_API_KEY_10", "env-ten")
        monkeypatch.setattr(
            config,
            "get_env_value",
            lambda name: {
                "TAVILY_API_KEY": " key-a ; key-b\nkey-a ",
                "TAVILY_API_KEYS": '["key-c", "key-b", "key-d"]',
            }.get(name),
        )

        assert _get_tavily_api_keys() == [
            "key-a",
            "key-b",
            "key-c",
            "key-d",
            "env-two",
            "env-ten",
        ]

    def test_discovers_config_aware_numbered_key_without_process_env(
        self, tavily_home, monkeypatch
    ):
        import hermes_cli.config as config
        from plugins.web.tavily.provider import _get_tavily_api_keys

        sentinel = "config-numbered-only"
        monkeypatch.setattr(
            config,
            "get_env_value",
            lambda name: {"TAVILY_API_KEY_1": sentinel}.get(name),
        )

        assert _get_tavily_api_keys() == [sentinel]

    def test_provider_available_with_plural_key_source_only(
        self, tavily_home, monkeypatch
    ):
        import hermes_cli.config as config
        from plugins.web.tavily.provider import TavilyWebSearchProvider

        monkeypatch.setattr(
            config,
            "get_env_value",
            lambda name: {"TAVILY_API_KEYS": '["pool-a", "pool-b"]'}.get(name),
        )

        assert TavilyWebSearchProvider().is_available() is True


class TestTavilyRetryAfterCooldown:
    """Retry-After handling must keep persisted cooldown JSON finite."""

    def test_infinite_retry_after_falls_back_to_default_finite_state(
        self, tavily_home, monkeypatch
    ):
        from plugins.web.tavily import provider

        now = 1_700_000_000.0
        monkeypatch.setattr(provider.time, "time", lambda: now)

        cooldown = provider._record_tavily_key_cooldown(
            "limited-key",
            headers={"Retry-After": "Infinity"},
        )

        assert cooldown == provider._DEFAULT_TAVILY_COOLDOWN_SECONDS
        with open(provider._tavily_state_path(), encoding="utf-8") as fh:
            raw_state = fh.read()
        assert "Infinity" not in raw_state
        entry = next(iter(json.loads(raw_state)["keys"].values()))
        assert math.isfinite(entry["cooldown_until"])
        assert entry["cooldown_until"] == now + provider._DEFAULT_TAVILY_COOLDOWN_SECONDS

    def test_excessive_retry_after_is_capped_to_one_day(
        self, tavily_home, monkeypatch
    ):
        from plugins.web.tavily import provider

        now = 1_700_000_000.0
        monkeypatch.setattr(provider.time, "time", lambda: now)

        cooldown = provider._record_tavily_key_cooldown(
            "limited-key",
            headers={"Retry-After": "999999999999"},
        )

        assert cooldown == 86_400.0
        with open(provider._tavily_state_path(), encoding="utf-8") as fh:
            entry = next(iter(json.load(fh)["keys"].values()))
        assert entry["cooldown_until"] == now + 86_400.0
        assert math.isfinite(entry["cooldown_until"])

    def test_http_date_retry_after_is_parsed(self, tavily_home, monkeypatch):
        from plugins.web.tavily import provider

        now = 1_700_000_000.0
        monkeypatch.setattr(provider.time, "time", lambda: now)
        retry_at = formatdate(now + 120, usegmt=True)

        cooldown = provider._record_tavily_key_cooldown(
            "limited-key",
            headers={"Retry-After": retry_at},
        )

        assert cooldown == 120.0
        with open(provider._tavily_state_path(), encoding="utf-8") as fh:
            entry = next(iter(json.load(fh)["keys"].values()))
        assert entry["cooldown_until"] == now + 120.0

    def test_rejected_cooldown_env_knob_is_ignored(self, tavily_home, monkeypatch):
        from plugins.web.tavily import provider

        now = 1_700_000_000.0
        monkeypatch.setattr(provider.time, "time", lambda: now)
        monkeypatch.setenv("TAVILY_KEY_COOLDOWN_SECONDS", "60")

        cooldown = provider._record_tavily_key_cooldown("limited-key", headers={})

        assert cooldown == provider._DEFAULT_TAVILY_COOLDOWN_SECONDS


# ─── _tavily_request ─────────────────────────────────────────────────────────

class TestTavilyRequest:
    """Test suite for the _tavily_request helper."""

    def test_raises_without_api_key(self, tavily_home):
        """No TAVILY_API_KEY → ValueError with guidance."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TAVILY_API_KEY", None)
            os.environ.pop("TAVILY_API_KEYS", None)
            from tools.web_tools import _tavily_request
            with pytest.raises(ValueError, match="TAVILY_API_KEY"):
                _tavily_request("search", {"query": "test"})

    def test_posts_with_api_key_in_body(self, tavily_home):
        """api_key is injected into the JSON payload."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test-key"}):
            with patch("tools.web_tools.httpx.post", return_value=mock_response) as mock_post:
                from tools.web_tools import _tavily_request
                result = _tavily_request("search", {"query": "hello"})

                mock_post.assert_called_once()
                call_kwargs = mock_post.call_args
                payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
                assert payload["api_key"] == "tvly-test-key"
                assert payload["query"] == "hello"
                assert "api.tavily.com/search" in call_kwargs.args[0]

    def test_accepts_plural_key_pool(self, tavily_home):
        """TAVILY_API_KEYS should be treated as a valid multi-key source."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": []}
        mock_response.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"TAVILY_API_KEYS": "tvly-a,tvly-b"}):
            with patch("tools.web_tools.httpx.post", return_value=mock_response) as mock_post:
                from tools.web_tools import _tavily_request

                result = _tavily_request("search", {"query": "hello"})

        assert result == {"results": []}
        assert mock_post.call_args.kwargs["json"]["api_key"] == "tvly-a"

    def test_falls_through_on_401_to_next_key(self, tavily_home):
        """A 401 on one key should fall through to the next configured key."""
        bad = _http_status_response(401, body={"detail": "unauthorized"})
        good = MagicMock()
        good.json.return_value = {"results": [{"title": "ok"}]}
        good.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"TAVILY_API_KEYS": "bad-key,good-key"}):
            with patch("tools.web_tools.httpx.post", side_effect=[bad, good]) as mock_post:
                from tools.web_tools import _tavily_request

                result = _tavily_request("search", {"query": "hello"})

        assert result["results"][0]["title"] == "ok"
        assert mock_post.call_count == 2
        assert mock_post.call_args_list[0].kwargs["json"]["api_key"] == "bad-key"
        assert mock_post.call_args_list[1].kwargs["json"]["api_key"] == "good-key"

    def test_falls_through_on_429_and_persists_hashed_cooldown(
        self, tavily_home
    ):
        from plugins.web.tavily.provider import _tavily_state_path

        limited = _http_status_response(
            429,
            headers={"Retry-After": "120"},
            body={"detail": "rate limit exceeded"},
        )
        good = MagicMock()
        good.json.return_value = {"results": [{"title": "ok"}]}
        good.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"TAVILY_API_KEYS": "limited-key,good-key"}):
            with patch("tools.web_tools.httpx.post", side_effect=[limited, good]):
                from tools.web_tools import _tavily_request

                result = _tavily_request("search", {"query": "hello"})

        assert result["results"][0]["title"] == "ok"
        with open(_tavily_state_path(), encoding="utf-8") as fh:
            raw_state = fh.read()
        assert "limited-key" not in raw_state
        assert "good-key" not in raw_state
        state = json.loads(raw_state)
        assert isinstance(state.get("keys"), dict)
        assert len(state["keys"]) == 1
        fingerprint, entry = next(iter(state["keys"].items()))
        assert fingerprint != "limited-key"
        assert set(entry) == {"cooldown_until", "updated_at"}
        assert entry["cooldown_until"] > entry["updated_at"]

    def test_skips_cooled_key_on_later_request(self, tavily_home):
        """Persisted cooldown should skip the bad key on the next request."""
        from plugins.web.tavily.provider import _tavily_state_path

        limited = _http_status_response(
            429,
            headers={"Retry-After": "300"},
            body={"detail": "quota exceeded"},
        )
        good = MagicMock()
        good.json.return_value = {"results": [{"title": "ok"}]}
        good.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"TAVILY_API_KEYS": "limited-key,good-key"}):
            with patch("tools.web_tools.httpx.post", side_effect=[limited, good]):
                from tools.web_tools import _tavily_request

                _tavily_request("search", {"query": "hello"})

        assert os.path.exists(_tavily_state_path())
        fresh = MagicMock()
        fresh.json.return_value = {"results": [{"title": "next"}]}
        fresh.raise_for_status = MagicMock()
        with patch.dict(os.environ, {"TAVILY_API_KEYS": "limited-key,good-key"}):
            with patch("tools.web_tools.httpx.post", return_value=fresh) as mock_post:
                from tools.web_tools import _tavily_request

                result = _tavily_request("search", {"query": "second"})

        assert result["results"][0]["title"] == "next"
        mock_post.assert_called_once()
        assert mock_post.call_args.kwargs["json"]["api_key"] == "good-key"

    def test_transient_error_tries_next_key_without_persisting_cooldown(
        self, tavily_home
    ):
        from plugins.web.tavily.provider import _tavily_state_path
        import httpx as _httpx

        good = MagicMock()
        good.json.return_value = {"results": [{"title": "ok"}]}
        good.raise_for_status = MagicMock()

        with patch.dict(os.environ, {"TAVILY_API_KEYS": "flaky-key,good-key"}):
            with patch(
                "tools.web_tools.httpx.post",
                side_effect=[_httpx.ConnectTimeout("timed out"), good],
            ) as mock_post:
                from tools.web_tools import _tavily_request

                result = _tavily_request("search", {"query": "hello"})

        assert result["results"][0]["title"] == "ok"
        assert mock_post.call_count == 2
        assert not os.path.exists(_tavily_state_path())

    def test_raises_on_http_error(self, tavily_home):
        """Non-2xx responses propagate as httpx.HTTPStatusError."""
        import httpx as _httpx
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = _httpx.HTTPStatusError(
            "401 Unauthorized", request=MagicMock(), response=mock_response
        )

        with patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-bad-key"}):
            with patch("tools.web_tools.httpx.post", return_value=mock_response):
                from tools.web_tools import _tavily_request
                with pytest.raises(_httpx.HTTPStatusError):
                    _tavily_request("search", {"query": "test"})


# ─── _normalize_tavily_search_results ─────────────────────────────────────────

class TestNormalizeTavilySearchResults:
    """Test search result normalization."""

    def test_basic_normalization(self):
        from tools.web_tools import _normalize_tavily_search_results
        raw = {
            "results": [
                {"title": "Python Docs", "url": "https://docs.python.org", "content": "Official docs", "score": 0.9},
                {"title": "Tutorial", "url": "https://example.com", "content": "A tutorial", "score": 0.8},
            ]
        }
        result = _normalize_tavily_search_results(raw)
        assert result["success"] is True
        web = result["data"]["web"]
        assert len(web) == 2
        assert web[0]["title"] == "Python Docs"
        assert web[0]["url"] == "https://docs.python.org"
        assert web[0]["description"] == "Official docs"
        assert web[0]["position"] == 1
        assert web[1]["position"] == 2

    def test_empty_results(self):
        from tools.web_tools import _normalize_tavily_search_results
        result = _normalize_tavily_search_results({"results": []})
        assert result["success"] is True
        assert result["data"]["web"] == []

    def test_missing_fields(self):
        from tools.web_tools import _normalize_tavily_search_results
        result = _normalize_tavily_search_results({"results": [{}]})
        web = result["data"]["web"]
        assert web[0]["title"] == ""
        assert web[0]["url"] == ""
        assert web[0]["description"] == ""


# ─── _normalize_tavily_documents ──────────────────────────────────────────────

class TestNormalizeTavilyDocuments:
    """Test extract/crawl document normalization."""

    def test_basic_document(self):
        from tools.web_tools import _normalize_tavily_documents
        raw = {
            "results": [{
                "url": "https://example.com",
                "title": "Example",
                "raw_content": "Full page content here",
            }]
        }
        docs = _normalize_tavily_documents(raw)
        assert len(docs) == 1
        assert docs[0]["url"] == "https://example.com"
        assert docs[0]["title"] == "Example"
        assert docs[0]["content"] == "Full page content here"
        assert docs[0]["raw_content"] == "Full page content here"
        assert docs[0]["metadata"]["sourceURL"] == "https://example.com"

    def test_falls_back_to_content_when_no_raw_content(self):
        from tools.web_tools import _normalize_tavily_documents
        raw = {"results": [{"url": "https://example.com", "content": "Snippet"}]}
        docs = _normalize_tavily_documents(raw)
        assert docs[0]["content"] == "Snippet"

    def test_failed_results_included(self):
        from tools.web_tools import _normalize_tavily_documents
        raw = {
            "results": [],
            "failed_results": [
                {"url": "https://fail.com", "error": "timeout"},
            ],
        }
        docs = _normalize_tavily_documents(raw)
        assert len(docs) == 1
        assert docs[0]["url"] == "https://fail.com"
        assert docs[0]["error"] == "timeout"
        assert docs[0]["content"] == ""

    def test_failed_urls_included(self):
        from tools.web_tools import _normalize_tavily_documents
        raw = {
            "results": [],
            "failed_urls": ["https://bad.com"],
        }
        docs = _normalize_tavily_documents(raw)
        assert len(docs) == 1
        assert docs[0]["url"] == "https://bad.com"
        assert docs[0]["error"] == "extraction failed"

    def test_fallback_url(self):
        from tools.web_tools import _normalize_tavily_documents
        raw = {"results": [{"content": "data"}]}
        docs = _normalize_tavily_documents(raw, fallback_url="https://fallback.com")
        assert docs[0]["url"] == "https://fallback.com"


# ─── web_search_tool (Tavily dispatch) ────────────────────────────────────────

class TestWebSearchTavily:
    """Test web_search_tool dispatch to Tavily."""

    _register_providers = staticmethod(register_all_web_providers)

    @pytest.fixture(autouse=True)
    def _populate_web_registry(self):
        self._register_providers()
        yield
        from agent.web_search_registry import _reset_for_tests
        _reset_for_tests()

    def test_search_dispatches_to_tavily(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{"title": "Result", "url": "https://r.com", "content": "desc", "score": 0.9}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("tools.web_tools._get_backend", return_value="tavily"), \
             patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test"}), \
             patch("tools.web_tools.httpx.post", return_value=mock_response), \
             patch("tools.interrupt.is_interrupted", return_value=False):
            from tools.web_tools import web_search_tool
            result = json.loads(web_search_tool("test query", limit=3))
            assert result["success"] is True
            assert len(result["data"]["web"]) == 1
            assert result["data"]["web"][0]["title"] == "Result"

    def test_search_dispatches_with_plural_keys(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{"title": "Result", "url": "https://r.com", "content": "desc", "score": 0.9}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("tools.web_tools._get_backend", return_value="tavily"), \
             patch.dict(os.environ, {"TAVILY_API_KEYS": "tvly-a,tvly-b"}), \
             patch("tools.web_tools.httpx.post", return_value=mock_response), \
             patch("tools.interrupt.is_interrupted", return_value=False):
            from tools.web_tools import web_search_tool
            result = json.loads(web_search_tool("test query", limit=3))
            assert result["success"] is True
            assert len(result["data"]["web"]) == 1
            assert result["data"]["web"][0]["title"] == "Result"


# ─── web_extract_tool (Tavily dispatch) ───────────────────────────────────────

class TestWebExtractTavily:
    """Test web_extract_tool dispatch to Tavily."""

    _register_providers = staticmethod(register_all_web_providers)

    @pytest.fixture(autouse=True)
    def _populate_web_registry(self):
        self._register_providers()
        yield
        from agent.web_search_registry import _reset_for_tests
        _reset_for_tests()

    def test_extract_dispatches_to_tavily(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [{"url": "https://example.com", "raw_content": "Extracted content", "title": "Page"}]
        }
        mock_response.raise_for_status = MagicMock()

        async def _fake_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        with patch("tools.web_tools._get_extract_backend", return_value="tavily"), \
             patch.dict(os.environ, {"TAVILY_API_KEY": "tvly-test"}), \
             patch("tools.web_tools.httpx.post", return_value=mock_response), \
             patch("tools.web_tools.async_is_safe_url", return_value=True), \
             patch("tools.web_tools.asyncio.to_thread", side_effect=_fake_to_thread):
            from tools.web_tools import web_extract_tool
            result = json.loads(asyncio.get_event_loop().run_until_complete(
                web_extract_tool(["https://example.com"])
            ))
            assert "results" in result
            assert len(result["results"]) == 1
            assert result["results"][0]["url"] == "https://example.com"
            assert "Extracted content" in result["results"][0]["content"]
