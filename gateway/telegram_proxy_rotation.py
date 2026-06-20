"""Clash/Mihomo selector helper for Telegram reconnect recovery.

This module stays outside the main Telegram gateway state machine on purpose.
It is meant to be used by a small local sidecar or a manual operator command
that can act even when Telegram itself is down.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import argparse
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Iterable, Protocol, Sequence
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

LOG = logging.getLogger(__name__)

DEFAULT_CONTROLLER_URL = "http://127.0.0.1:9090"
DEFAULT_SELECTOR_NAME = "电报消息"
DEFAULT_TEST_URL = "https://api.telegram.org"
DEFAULT_DELAY_TIMEOUT_MS = 5000
DEFAULT_HTTP_TIMEOUT = 5.0
DEFAULT_TRIGGER_ERRORS = 2
DEFAULT_COOLDOWN_SECONDS = 30 * 60
DEFAULT_MAX_CANDIDATES = 3
DEFAULT_HEALTHY_GRACE_SECONDS = 15
DEFAULT_LOG_PATH = Path.home() / ".hermes/logs/gateway.log"
DEFAULT_SKIP_NAMES = frozenset({"节点选择", "自动选择", "手动切换", "DIRECT"})
DEFAULT_RECENT_FAILURE_TTL = 15 * 60
DEFAULT_EVENT_ENDPOINT = os.getenv("HERMES_TELEGRAM_ROTATOR_EVENT_URL", "http://127.0.0.1:17893/v1/telegram-rotation/events")

# Network-only events we care about. Conflict/auth errors are intentionally
# excluded so the helper does not churn on non-network failures.
NETWORK_ERROR_MARKERS = (
    "Telegram network error",
    "Telegram polling reconnect failed",
    "Polling heartbeat probe failed",
    "telegram connect timed out",
    "connect timed out",
)
SUCCESS_MARKERS = (
    "Telegram polling resumed after network error",
    "Connected to Telegram",
    "✓ telegram reconnected successfully",
)
IGNORED_MARKERS = (
    "telegram_polling_conflict",
    "Telegram polling conflict",
    "bot token already in use",
    "No bot token configured",
    "Failed to connect to Telegram",
)


class ClashAPIError(RuntimeError):
    """Raised when the local Clash/Mihomo API returns an unexpected response."""


@dataclass(slots=True)
class CandidateResult:
    name: str
    delay_ms: int | None
    error: str | None = None

    @property
    def healthy(self) -> bool:
        return self.delay_ms is not None and self.delay_ms >= 0 and self.error is None


@dataclass(slots=True)
class RotationResult:
    selector: str
    current: str | None
    chosen: str | None
    tried: list[CandidateResult] = field(default_factory=list)
    changed: bool = False
    reason: str = ""


@dataclass(slots=True)
class LogWatchState:
    consecutive_network_errors: int = 0
    cooldown_until: float = 0.0
    recent_failures: dict[str, float] = field(default_factory=dict)
    last_rotation_target: str | None = None
    last_rotation_at: float = 0.0


class ClashAPI(Protocol):
    def selector(self, name: str) -> dict: ...
    def delay(self, name: str, test_url: str = DEFAULT_TEST_URL, timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS) -> int: ...
    def set_selector(self, selector_name: str, target_name: str) -> dict: ...


class ClashClient:
    def __init__(self, controller_url: str = DEFAULT_CONTROLLER_URL, timeout: float = DEFAULT_HTTP_TIMEOUT):
        self.controller_url = controller_url.rstrip("/")
        self.timeout = timeout

    def _json(self, method: str, path: str, payload: dict | None = None, timeout: float | None = None) -> dict:
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib_request.Request(
            f"{self.controller_url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib_request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read()
        except urllib_error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise ClashAPIError(f"HTTP {exc.code} for {path}: {body}") from exc
        except urllib_error.URLError as exc:
            raise ClashAPIError(f"Failed to reach Clash API at {self.controller_url}: {exc}") from exc
        if not raw:
            return {}
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ClashAPIError(f"Invalid JSON from {path}: {raw[:200]!r}") from exc
        if not isinstance(decoded, dict):
            raise ClashAPIError(f"Expected JSON object from {path}, got {type(decoded).__name__}")
        return decoded

    def version(self) -> dict:
        return self._json("GET", "/version")

    def selector(self, name: str) -> dict:
        return self._json("GET", f"/proxies/{_quote_path(name)}")

    def delay(self, name: str, test_url: str = DEFAULT_TEST_URL, timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS) -> int:
        payload = self._json(
            "GET",
            f"/proxies/{_quote_path(name)}/delay?url={urllib_parse.quote(test_url, safe='')}&timeout={int(timeout_ms)}",
            timeout=max(self.timeout, timeout_ms / 1000.0 + 2.0),
        )
        delay = payload.get("delay")
        if not isinstance(delay, int):
            raise ClashAPIError(f"Unexpected delay payload for {name!r}: {payload}")
        return delay

    def set_selector(self, selector_name: str, target_name: str) -> dict:
        return self._json(
            "PUT",
            f"/proxies/{_quote_path(selector_name)}",
            payload={"name": target_name},
        )


NETWORK_LINE_RE = re.compile(
    r"gateway\.platforms\.telegram: \[Telegram\] (?P<msg>.*)$",
    re.IGNORECASE,
)
TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)")


def _quote_path(value: str) -> str:
    return urllib_parse.quote(value, safe="")


def _now() -> float:
    return time.monotonic()


def is_network_line(line: str) -> bool:
    if not line:
        return False
    if not NETWORK_LINE_RE.search(line):
        return False
    lowered = line.lower()
    if any(marker.lower() in lowered for marker in IGNORED_MARKERS):
        return False
    return any(marker.lower() in lowered for marker in NETWORK_ERROR_MARKERS)


def is_success_line(line: str) -> bool:
    if not line:
        return False
    lowered = line.lower()
    return any(marker.lower() in lowered for marker in SUCCESS_MARKERS)


def update_log_state(state: LogWatchState, line: str, trigger_errors: int = DEFAULT_TRIGGER_ERRORS) -> bool:
    """Update log watcher state and return True when a rotation should run."""
    if is_success_line(line):
        state.consecutive_network_errors = 0
        return False

    if not is_network_line(line):
        return False

    state.consecutive_network_errors += 1
    if state.consecutive_network_errors >= trigger_errors:
        state.consecutive_network_errors = 0
        return True
    return False


def selector_candidates(
    selector: dict,
    *,
    current: str | None = None,
    skip_names: Iterable[str] = DEFAULT_SKIP_NAMES,
    recent_failures: dict[str, float] | None = None,
    recent_failure_ttl: int = DEFAULT_RECENT_FAILURE_TTL,
    now: float | None = None,
) -> list[str]:
    now = _now() if now is None else now
    recent_failures = recent_failures or {}
    skip = set(skip_names)
    skip.add(selector.get("name", ""))
    candidates = []
    for name in selector.get("all", []) or []:
        if not isinstance(name, str) or not name:
            continue
        if name in skip:
            continue
        if recent_failures.get(name) and (now - recent_failures[name]) < recent_failure_ttl:
            continue
        candidates.append(name)

    # Keep the current choice as a last resort if everything else fails.
    if current and current in candidates:
        candidates.remove(current)
        candidates.append(current)
    return candidates


def measure_candidates(
    client: ClashAPI,
    candidate_names: Sequence[str],
    *,
    test_url: str = DEFAULT_TEST_URL,
    timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS,
) -> list[CandidateResult]:
    results: list[CandidateResult] = []
    for name in candidate_names:
        try:
            delay = client.delay(name, test_url=test_url, timeout_ms=timeout_ms)
            results.append(CandidateResult(name=name, delay_ms=delay))
        except Exception as exc:  # noqa: BLE001 - helper should surface and continue
            results.append(CandidateResult(name=name, delay_ms=None, error=str(exc)))
    results.sort(key=lambda item: (item.delay_ms is None, item.delay_ms if item.delay_ms is not None else 10**9, item.name))
    return results


def rotate_selector_once(
    client: ClashAPI,
    *,
    selector_name: str = DEFAULT_SELECTOR_NAME,
    test_url: str = DEFAULT_TEST_URL,
    timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS,
    skip_names: Iterable[str] = DEFAULT_SKIP_NAMES,
    recent_failures: dict[str, float] | None = None,
    recent_failure_ttl: int = DEFAULT_RECENT_FAILURE_TTL,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> RotationResult:
    selector = client.selector(selector_name)
    current = selector.get("now") if isinstance(selector, dict) else None
    candidates = selector_candidates(
        selector,
        current=current,
        skip_names=skip_names,
        recent_failures=recent_failures,
        recent_failure_ttl=recent_failure_ttl,
    )
    measured = measure_candidates(client, candidates, test_url=test_url, timeout_ms=timeout_ms)
    tried: list[CandidateResult] = []
    for candidate in measured[:max_candidates]:
        tried.append(candidate)
        if not candidate.healthy:
            continue
        if candidate.name == current:
            continue
        client.set_selector(selector_name, candidate.name)
        confirm = client.selector(selector_name).get("now")
        changed = confirm == candidate.name
        return RotationResult(
            selector=selector_name,
            current=current,
            chosen=candidate.name,
            tried=tried,
            changed=changed,
            reason="switched" if changed else f"selector still reports {confirm!r}",
        )

    return RotationResult(
        selector=selector_name,
        current=current,
        chosen=None,
        tried=tried,
        changed=False,
        reason="no healthy candidate found",
    )


def watch_gateway_log(
    *,
    log_path: Path = DEFAULT_LOG_PATH,
    client: ClashAPI,
    selector_name: str = DEFAULT_SELECTOR_NAME,
    test_url: str = DEFAULT_TEST_URL,
    timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS,
    trigger_errors: int = DEFAULT_TRIGGER_ERRORS,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    recent_failure_ttl: int = DEFAULT_RECENT_FAILURE_TTL,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    dry_run: bool = False,
) -> None:
    state = LogWatchState()
    log_path = log_path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)

    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        fh.seek(0, os.SEEK_END)
        LOG.info("Watching %s for Telegram network errors", log_path)
        while True:
            line = fh.readline()
            if not line:
                time.sleep(0.5)
                continue
            if not update_log_state(state, line, trigger_errors=trigger_errors):
                continue

            now = _now()
            if now < state.cooldown_until:
                continue

            try:
                selector = client.selector(selector_name)
                current = selector.get("now")
                failures = dict(state.recent_failures)
                if current:
                    failures[current] = now
                candidates = selector_candidates(
                    selector,
                    current=current,
                    recent_failures=failures,
                    recent_failure_ttl=recent_failure_ttl,
                )
                measured = measure_candidates(client, candidates, test_url=test_url, timeout_ms=timeout_ms)
                chosen = next((item for item in measured if item.healthy and item.name != current), None)
                if chosen is None:
                    LOG.warning("No healthy Telegram proxy candidate found for %s", selector_name)
                    continue

                LOG.warning(
                    "Trigger reached; switching %s from %s to %s after delay scan",
                    selector_name,
                    current,
                    chosen.name,
                )
                if dry_run:
                    LOG.warning("Dry-run mode: would PUT selector to %s", chosen.name)
                else:
                    client.set_selector(selector_name, chosen.name)
                    confirm = client.selector(selector_name).get("now")
                    if confirm != chosen.name:
                        raise ClashAPIError(f"selector still reports {confirm!r} after setting {chosen.name!r}")
                    state.last_rotation_target = chosen.name
                    state.last_rotation_at = now
                    state.cooldown_until = now + cooldown_seconds
                    state.recent_failures[chosen.name] = now
                    LOG.warning("Telegram selector switched to %s; cooling down for %ds", chosen.name, cooldown_seconds)
            except Exception as exc:  # noqa: BLE001 - keep watcher alive
                LOG.exception("Telegram proxy rotation failed: %s", exc)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="telegram_proxy_rotation",
        description="Rotate Clash/Mihomo selector nodes for Hermes Telegram recovery.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--controller-url", default=DEFAULT_CONTROLLER_URL)
    common.add_argument("--selector-name", default=DEFAULT_SELECTOR_NAME)
    common.add_argument("--test-url", default=DEFAULT_TEST_URL)
    common.add_argument("--timeout-ms", type=int, default=DEFAULT_DELAY_TIMEOUT_MS)
    common.add_argument("--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES)
    common.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    common.add_argument("--recent-failure-ttl", type=int, default=DEFAULT_RECENT_FAILURE_TTL)

    plan = subparsers.add_parser("plan", parents=[common], help="Print the measured candidate order and exit.")
    once = subparsers.add_parser("once", parents=[common], help="Measure candidates and switch once if possible.")
    once.add_argument("--dry-run", action="store_true")
    watch = subparsers.add_parser("watch", parents=[common], help="Tail gateway.log and rotate on repeated network errors.")
    watch.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    watch.add_argument("--trigger-errors", type=int, default=DEFAULT_TRIGGER_ERRORS)
    watch.add_argument("--dry-run", action="store_true")

    return parser


def _configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _configure_logging()
    client = ClashClient(args.controller_url)

    if args.command == "plan":
        selector = client.selector(args.selector_name)
        current = selector.get("now")
        candidates = selector_candidates(selector, current=current)
        measured = measure_candidates(client, candidates, test_url=args.test_url, timeout_ms=args.timeout_ms)
        print(json.dumps({
            "selector": args.selector_name,
            "current": current,
            "results": [item.__dict__ for item in measured[: args.max_candidates]],
        }, ensure_ascii=False, indent=2))
        return 0

    if args.command == "once":
        result = rotate_selector_once(
            client,
            selector_name=args.selector_name,
            test_url=args.test_url,
            timeout_ms=args.timeout_ms,
            max_candidates=args.max_candidates,
        )
        print(json.dumps({
            "selector": result.selector,
            "current": result.current,
            "chosen": result.chosen,
            "changed": result.changed,
            "reason": result.reason,
            "tried": [item.__dict__ for item in result.tried],
        }, ensure_ascii=False, indent=2))
        return 0 if result.changed or args.dry_run else 1

    if args.command == "watch":
        watch_gateway_log(
            log_path=Path(args.log_path),
            client=client,
            selector_name=args.selector_name,
            test_url=args.test_url,
            timeout_ms=args.timeout_ms,
            trigger_errors=args.trigger_errors,
            cooldown_seconds=args.cooldown_seconds,
            recent_failure_ttl=args.recent_failure_ttl,
            max_candidates=args.max_candidates,
            dry_run=args.dry_run,
        )
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
