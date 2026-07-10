"""Tests for the local Telegram Mihomo/Clash proxy rotation helper."""

from __future__ import annotations

import httpx
import pytest

from plugins.platforms.telegram.local_proxy_rotation import (
    CandidatePolicy,
    CandidateResult,
    ClashClient,
    LogWatchState,
    RotationResult,
    _cmd_plan,
    _result_to_dict,
    build_parser,
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

    def delay(self, name: str, test_url: str, timeout_ms: int) -> int:
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
            "2026-06-20 12:00:00,000 WARNING plugins.platforms.telegram.adapter: [telegram] Telegram network error (attempt 1/10), reconnecting in 5s. Error: httpx.ConnectError: ",
            "2026-06-20 12:00:00,000 WARNING plugins.platforms.telegram.adapter: [telegram] Telegram polling reconnect failed: Timed out",
            "2026-06-20 12:00:00,000 WARNING gateway.run: telegram paused after 10 consecutive failures (telegram connect timed out after 30s)",
            "2026-06-20 12:00:00,000 WARNING any.logger: [telegram] Polling heartbeat probe failed 60s after reconnect: httpx.ConnectError: ",
        ],
    )
    def test_detects_current_network_markers_without_module_path_dependency(self, line: str):
        assert is_network_line(line) is True

    @pytest.mark.parametrize(
        "line",
        [
            "2026-06-20 12:00:00,000 WARNING plugins.platforms.telegram.adapter: [telegram] Telegram polling conflict (1/3), will retry in 10s. Error: Conflict",
            "2026-06-20 12:00:00,000 INFO plugins.platforms.telegram.adapter: [telegram] Connected to Telegram (polling mode)",
            "2026-06-20 12:00:00,000 ERROR plugins.platforms.telegram.adapter: [telegram] No bot token configured",
        ],
    )
    def test_ignores_non_network_lines(self, line: str):
        assert is_network_line(line) is False


class TestLogWatchState:
    def test_triggers_after_configured_network_lines(self):
        state = LogWatchState()
        assert update_log_state(state, "Telegram network error, scheduling reconnect", trigger_errors=2) is False
        assert update_log_state(state, "Telegram polling reconnect failed: httpx.ConnectError", trigger_errors=2) is True

    def test_success_resets_counter(self):
        state = LogWatchState(consecutive_network_errors=1)
        assert update_log_state(state, "Telegram polling resumed after network error (attempt 1)") is False
        assert state.consecutive_network_errors == 0


class TestSelectorCandidates:
    def test_excludes_skip_regex_and_recent_failures(self):
        selector = {
            "name": "selector",
            "now": "tw-node",
            "all": ["DIRECT", "hk-node", "tw-node", "jp-node", "us-node"],
        }
        policy = CandidatePolicy(
            skip_names=frozenset({"DIRECT"}),
            include_regex="node$",
            exclude_regex="^us-",
        )
        candidates = selector_candidates(
            selector,
            current="tw-node",
            policy=policy,
            recent_failures={"hk-node": 100.0},
            now=200.0,
            recent_failure_ttl=150,
        )
        assert candidates == ["jp-node", "tw-node"]


class TestRotateSelectorOnce:
    def test_probes_candidates_then_switches_only_after_confirmation(self):
        client = FakeClient(
            {"name": "selector", "now": "tw-node", "all": ["hk-node", "tw-node", "jp-node"]},
            delays={"hk-node": 310, "jp-node": 280},
        )
        confirmations: list[tuple[str | None, str, int | None]] = []

        def confirm(current: str | None, chosen: str, delay_ms: int | None) -> bool:
            confirmations.append((current, chosen, delay_ms))
            return True

        result = rotate_selector_once(
            client,
            selector_name="selector",
            policy=CandidatePolicy(),
            confirm_switch=confirm,
            max_candidates=3,
        )

        assert result.changed is True
        assert result.current == "tw-node"
        assert result.chosen == "jp-node"
        assert [item.name for item in result.tried] == ["jp-node"]
        assert confirmations == [("tw-node", "jp-node", 280)]
        assert client.calls.index(("delay", ("jp-node", "https://api.telegram.org", 5000))) < client.calls.index(
            ("set_selector", ("selector", "jp-node"))
        )

    def test_does_not_call_controller_put_when_confirmation_rejects(self):
        client = FakeClient(
            {"name": "selector", "now": "tw-node", "all": ["hk-node", "tw-node", "jp-node"]},
            delays={"hk-node": 310, "jp-node": 280},
        )

        result = rotate_selector_once(
            client,
            selector_name="selector",
            policy=CandidatePolicy(),
            confirm_switch=lambda _current, _chosen, _delay_ms: False,
        )

        assert result.changed is False
        assert result.chosen == "jp-node"
        assert result.reason == "switch not confirmed"
        assert not any(call[0] == "set_selector" for call in client.calls)


class TestCliContract:
    def test_result_to_dict_serializes_slotted_candidate_results(self):
        payload = _result_to_dict(
            RotationResult(
                selector="telegram",
                current="tw-node",
                chosen="jp-node",
                tried=[CandidateResult(name="jp-node", delay_ms=280)],
                changed=True,
                reason="switched",
            )
        )

        assert payload == {
            "selector": "telegram",
            "current": "tw-node",
            "chosen": "jp-node",
            "changed": True,
            "reason": "switched",
            "tried": [{"name": "jp-node", "delay_ms": 280, "error": None}],
        }

    def test_plan_command_prints_json_for_slotted_candidate_results(self, monkeypatch, capsys):
        client = FakeClient(
            {"name": "selector", "now": "tw-node", "all": ["jp-node"]},
            delays={"jp-node": 280},
        )
        monkeypatch.setattr(
            "plugins.platforms.telegram.local_proxy_rotation._make_client",
            lambda _args: client,
        )
        args = build_parser().parse_args(
            [
                "--controller-url",
                "http://127.0.0.1:9090",
                "--selector",
                "telegram",
                "plan",
            ]
        )

        assert _cmd_plan(args) == 0

        output = capsys.readouterr().out
        assert '"results": [' in output
        assert '"delay_ms": 280' in output

    def test_controller_and_selector_are_explicit_cli_inputs(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["plan"])

        args = parser.parse_args(
            [
                "--controller-url",
                "http://127.0.0.1:9090",
                "--selector",
                "telegram",
                "plan",
                "--skip-name",
                "DIRECT",
                "--include-regex",
                "node",
            ]
        )
        assert args.controller_url == "http://127.0.0.1:9090"
        assert args.selector == "telegram"
        assert args.skip_name == ["DIRECT"]
        assert args.include_regex == "node"

    def test_client_redacts_controller_credentials_for_display(self):
        client = ClashClient("http://user:pass@127.0.0.1:9090?secret=token", secret="controller-secret")
        assert client.safe_controller_url == "http://***:***@127.0.0.1:9090?secret=***"

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com",
            "http://169.254.169.254",
            "ftp://127.0.0.1",
            "http://0.0.0.0:9090",
        ],
    )
    def test_client_rejects_non_local_http_controller_urls(self, url: str):
        with pytest.raises(ValueError, match="local loopback"):
            ClashClient(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:9090",
            "https://localhost:9090",
            "http://[::1]:9090",
        ],
    )
    def test_client_accepts_loopback_http_controller_urls(self, url: str):
        assert ClashClient(url).controller_url == url

    def test_controller_url_assignment_is_rejected_and_requests_use_private_loopback_target(self):
        sent_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            sent_requests.append(request)
            return httpx.Response(200, json={}, request=request)

        transport = httpx.MockTransport(handler)
        http_client = httpx.Client(transport=transport)
        client = ClashClient(
            "http://127.0.0.1:9090",
            secret="controller-secret",
            http_client=http_client,
        )
        with pytest.raises((AttributeError, ValueError)):
            client.controller_url = "https://example.invalid"

        payload = client._request("GET", "/proxies/telegram")

        assert payload == {}
        assert len(sent_requests) == 1
        request = sent_requests[0]
        assert str(request.url) == "http://127.0.0.1:9090/proxies/telegram"
        assert request.headers["Authorization"] == "Bearer controller-secret"
        assert request.url.host == "127.0.0.1"
