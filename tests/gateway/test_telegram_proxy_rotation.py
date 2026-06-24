"""Tests for gateway.telegram_proxy_rotation — Clash/Mihomo helper flow."""

from __future__ import annotations

from pathlib import Path

import pytest

from gateway.telegram_proxy_rotation import (
    CandidateResult,
    ClashClient,
    LogWatchState,
    is_network_line,
    rotate_selector_once,
    selector_candidates,
    update_log_state,
)


class FakeClient:
    def __init__(self, selector_data: dict[str, object], delays: dict[str, int] | None = None):
        self._selector_data = selector_data
        self._delays = delays or {}
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def selector(self, name: str) -> dict[str, object]:
        self.calls.append(("selector", (name,)))
        return self._selector_data

    def delay(self, name: str, test_url: str = "https://api.telegram.org", timeout_ms: int = 5000) -> int:
        self.calls.append(("delay", (name, test_url, timeout_ms)))
        if name not in self._delays:
            raise RuntimeError(f"missing delay for {name}")
        return self._delays[name]

    def set_selector(self, selector_name: str, target_name: str) -> dict[str, object]:
        self.calls.append(("set_selector", (selector_name, target_name)))
        self._selector_data = {**self._selector_data, "now": target_name}
        return {"name": target_name}


class TestNetworkLineClassification:
    @pytest.mark.parametrize(
        "line",
        [
            "2026-06-20 12:00:00,000 WARNING gateway.platforms.telegram: [Telegram] Telegram network error (attempt 1/10), reconnecting in 5s. Error: httpx.ConnectError: ",
            "2026-06-20 12:00:00,000 WARNING gateway.platforms.telegram: [Telegram] Telegram polling reconnect failed: Timed out",
            "2026-06-20 12:00:00,000 WARNING gateway.platforms.telegram: [Telegram] Polling heartbeat probe failed 60s after reconnect: httpx.ConnectError: ",
        ],
    )
    def test_detects_network_lines(self, line):
        assert is_network_line(line) is True

    @pytest.mark.parametrize(
        "line",
        [
            "2026-06-20 12:00:00,000 WARNING gateway.run: telegram paused after 10 consecutive failures (telegram connect timed out after 30s)",
            "2026-06-20 12:00:00,000 WARNING gateway.platforms.telegram: [Telegram] Telegram polling conflict (1/3), will retry in 10s. Error: Conflict",
            "2026-06-20 12:00:00,000 INFO gateway.platforms.telegram: [Telegram] Connected to Telegram (polling mode)",
        ],
    )
    def test_ignores_non_network_lines(self, line):
        assert is_network_line(line) is False


class TestLogWatchState:
    def test_triggers_after_two_network_lines(self):
        state = LogWatchState()
        assert update_log_state(state, "2026-06-20 12:00:00,000 WARNING gateway.platforms.telegram: [Telegram] Telegram network error, scheduling reconnect: httpx.ConnectError: ") is False
        assert update_log_state(state, "2026-06-20 12:00:01,000 WARNING gateway.platforms.telegram: [Telegram] Telegram polling reconnect failed: httpx.ConnectError: ") is True

    def test_success_resets_counter(self):
        state = LogWatchState(consecutive_network_errors=1)
        assert update_log_state(state, "2026-06-20 12:00:00,000 INFO gateway.platforms.telegram: [Telegram] Telegram polling resumed after network error (attempt 1)") is False
        assert state.consecutive_network_errors == 0


class TestSelectorCandidates:
    def test_excludes_skip_and_recent_failures(self):
        selector = {"name": "电报消息", "now": "台湾节点", "all": ["节点选择", "自动选择", "香港节点", "台湾节点", "日本节点", "DIRECT"]}
        candidates = selector_candidates(
            selector,
            current="台湾节点",
            recent_failures={"香港节点": 100.0},
            now=200.0,
            recent_failure_ttl=150,
        )
        assert candidates == ["日本节点", "台湾节点"]


class TestRotateSelectorOnce:
    def test_switches_to_best_healthy_candidate(self):
        client = FakeClient(
            {"name": "电报消息", "now": "台湾节点", "all": ["节点选择", "自动选择", "香港节点", "台湾节点", "日本节点", "DIRECT"]},
            delays={"香港节点": 310, "日本节点": 280},
        )
        result = rotate_selector_once(
            client,
            selector_name="电报消息",
            max_candidates=3,
        )
        assert result.changed is True
        assert result.current == "台湾节点"
        assert result.chosen == "日本节点"
        assert [item.name for item in result.tried] == ["日本节点"]
        assert ("set_selector", ("电报消息", "日本节点")) in client.calls

    def test_returns_no_change_when_only_current_is_healthy(self):
        client = FakeClient(
            {"name": "电报消息", "now": "台湾节点", "all": ["台湾节点", "DIRECT"]},
            delays={},
        )
        result = rotate_selector_once(client, selector_name="电报消息")
        assert result.changed is False
        assert result.chosen is None
        assert result.reason == "no healthy candidate found"


class TestClashClient:
    def test_client_can_be_constructed(self):
        client = ClashClient("http://127.0.0.1:9090")
        assert client.controller_url == "http://127.0.0.1:9090"

    def test_client_accepts_multiple_controller_urls(self):
        client = ClashClient(["http://127.0.0.1:19090", "http://127.0.0.1:9090"])
        assert client.controller_urls == ("http://127.0.0.1:19090", "http://127.0.0.1:9090")
        assert client.controller_url == "http://127.0.0.1:19090"
