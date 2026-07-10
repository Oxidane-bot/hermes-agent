"""Local Mihomo/Clash selector helper for Telegram network recovery.

This module is intentionally not imported by the Telegram adapter.  It is a
manual/sidecar operator tool for local deployments that want to rotate a
Mihomo/Clash selector after Telegram transport failures appear in gateway logs.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
import getpass
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from typing import Any, Protocol
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx

LOG = logging.getLogger(__name__)

DEFAULT_TEST_URL = "https://api.telegram.org"
DEFAULT_DELAY_TIMEOUT_MS = 5000
DEFAULT_HTTP_TIMEOUT = 5.0
DEFAULT_TRIGGER_ERRORS = 2
DEFAULT_COOLDOWN_SECONDS = 30 * 60
DEFAULT_MAX_CANDIDATES = 3
DEFAULT_RECENT_FAILURE_TTL = 15 * 60
DEFAULT_LOG_PATH = Path.home() / ".hermes/logs/gateway.log"

DEFAULT_SKIP_NAMES = frozenset({"DIRECT"})

NETWORK_ERROR_MARKERS = (
    "telegram network error",
    "telegram polling reconnect failed",
    "polling heartbeat probe failed",
    "telegram connect timed out",
    "connect timed out",
)
SUCCESS_MARKERS = (
    "telegram polling resumed after network error",
    "connected to telegram",
)
IGNORED_MARKERS = (
    "telegram_polling_conflict",
    "telegram polling conflict",
    "bot token already in use",
    "no bot token configured",
    "failed to connect to telegram",
)

_SENSITIVE_QUERY_KEYS = frozenset({"secret", "token", "access_token", "password", "key"})


class ClashAPIError(RuntimeError):
    """Raised when the local Mihomo/Clash API returns an unexpected response."""


class ClashAPI(Protocol):
    def selector(self, name: str) -> dict[str, Any]: ...

    def delay(self, name: str, test_url: str, timeout_ms: int) -> int: ...

    def set_selector(self, selector_name: str, target_name: str) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CandidatePolicy:
    """Candidate filtering knobs exposed by the CLI."""

    skip_names: frozenset[str] = DEFAULT_SKIP_NAMES
    include_regex: str | None = None
    exclude_regex: str | None = None

    def allows(self, name: str) -> bool:
        if name in self.skip_names:
            return False
        if self.include_regex and re.search(self.include_regex, name) is None:
            return False
        if self.exclude_regex and re.search(self.exclude_regex, name) is not None:
            return False
        return True


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


class ClashClient:
    """Small sync client for the local Mihomo/Clash controller API."""

    def __init__(
        self,
        controller_url: str,
        *,
        secret: str | None = None,
        timeout: float = DEFAULT_HTTP_TIMEOUT,
        http_client: httpx.Client | None = None,
    ) -> None:
        normalized = controller_url.rstrip("/")
        if not normalized:
            raise ValueError("controller_url is required")
        _validate_local_controller_url(normalized)
        self._controller_url = normalized
        self.secret = secret
        self.timeout = timeout
        self._client = http_client

    @property
    def controller_url(self) -> str:
        return self._controller_url

    @property
    def safe_controller_url(self) -> str:
        return redact_url(self._controller_url)

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.secret:
            headers["Authorization"] = f"Bearer {self.secret}"
        return headers

    def _request(self, method: str, path: str, *, json_payload: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        _validate_local_controller_url(self._controller_url)
        url = f"{self._controller_url}{path}"
        close_client = False
        client = self._client
        if client is None:
            client = httpx.Client(timeout=timeout or self.timeout)
            close_client = True
        try:
            response = client.request(
                method,
                url,
                headers=self._headers(),
                json=json_payload,
                timeout=timeout or self.timeout,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = exc.response.text[:200]
            raise ClashAPIError(f"HTTP {exc.response.status_code} from Mihomo/Clash API at {self.safe_controller_url}: {body}") from exc
        except httpx.HTTPError as exc:
            raise ClashAPIError(f"Failed to reach Mihomo/Clash API at {self.safe_controller_url}: {exc}") from exc
        finally:
            if close_client:
                client.close()

        if not response.content:
            return {}
        try:
            payload = response.json()
        except ValueError as exc:
            raise ClashAPIError(f"Invalid JSON from Mihomo/Clash API at {self.safe_controller_url}") from exc
        if not isinstance(payload, dict):
            raise ClashAPIError(f"Expected JSON object from Mihomo/Clash API, got {type(payload).__name__}")
        return payload

    def selector(self, name: str) -> dict[str, Any]:
        return self._request("GET", f"/proxies/{quote(name, safe='')}")

    def delay(self, name: str, test_url: str, timeout_ms: int) -> int:
        path = f"/proxies/{quote(name, safe='')}/delay?url={quote(test_url, safe='')}&timeout={int(timeout_ms)}"
        payload = self._request("GET", path, timeout=max(self.timeout, timeout_ms / 1000.0 + 2.0))
        delay = payload.get("delay")
        if not isinstance(delay, int):
            raise ClashAPIError(f"Unexpected delay payload for {name!r}: {payload}")
        return delay

    def set_selector(self, selector_name: str, target_name: str) -> dict[str, Any]:
        return self._request(
            "PUT",
            f"/proxies/{quote(selector_name, safe='')}",
            json_payload={"name": target_name},
        )


def redact_url(url: str) -> str:
    """Redact URL userinfo and sensitive query values before display/logging."""
    split = urlsplit(url)
    netloc = split.netloc
    if "@" in netloc:
        host = netloc.rsplit("@", 1)[1]
        netloc = f"***:***@{host}"
    query_pairs = []
    for key, value in parse_qsl(split.query, keep_blank_values=True):
        query_pairs.append((key, "***" if key.lower() in _SENSITIVE_QUERY_KEYS else value))
    return urlunsplit((split.scheme, netloc, split.path, urlencode(query_pairs, safe="*"), split.fragment))


def _validate_local_controller_url(url: str) -> None:
    """Allow only local Mihomo/Clash controllers, never remote SSRF targets."""
    split = urlsplit(url)
    if split.scheme not in {"http", "https"}:
        raise ValueError("controller_url must be an http(s) local loopback URL")
    host = split.hostname
    if not host:
        raise ValueError("controller_url must be an http(s) local loopback URL")
    try:
        _ = split.port
    except ValueError as exc:
        raise ValueError("controller_url must be an http(s) local loopback URL") from exc

    if host.lower().rstrip(".") == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError("controller_url must be an http(s) local loopback URL") from exc
    if not address.is_loopback:
        raise ValueError("controller_url must be an http(s) local loopback URL")


def _now() -> float:
    return time.monotonic()


def is_network_line(line: str) -> bool:
    """Return True for Telegram network failures regardless of logger path."""
    lowered = line.lower()
    if "telegram" not in lowered:
        return False
    if any(marker in lowered for marker in IGNORED_MARKERS):
        return False
    return any(marker in lowered for marker in NETWORK_ERROR_MARKERS)


def is_success_line(line: str) -> bool:
    lowered = line.lower()
    return "telegram" in lowered and any(marker in lowered for marker in SUCCESS_MARKERS)


def update_log_state(state: LogWatchState, line: str, *, trigger_errors: int = DEFAULT_TRIGGER_ERRORS) -> bool:
    """Update watcher state and return True when a rotation attempt should run."""
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
    selector: dict[str, Any],
    *,
    current: str | None,
    policy: CandidatePolicy,
    recent_failures: dict[str, float] | None = None,
    recent_failure_ttl: int = DEFAULT_RECENT_FAILURE_TTL,
    now: float | None = None,
) -> list[str]:
    now = _now() if now is None else now
    failures = recent_failures or {}
    skip_names = set(policy.skip_names)
    selector_name = selector.get("name")
    if isinstance(selector_name, str):
        skip_names.add(selector_name)

    candidates: list[str] = []
    for name in selector.get("all", []) or []:
        if not isinstance(name, str) or not name:
            continue
        if name in skip_names:
            continue
        failed_at = failures.get(name)
        if failed_at is not None and (now - failed_at) < recent_failure_ttl:
            continue
        if not policy.allows(name):
            continue
        candidates.append(name)

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
            delay_ms = client.delay(name, test_url, timeout_ms)
            results.append(CandidateResult(name=name, delay_ms=delay_ms))
        except Exception as exc:  # noqa: BLE001 - continue scanning remaining candidates.
            results.append(CandidateResult(name=name, delay_ms=None, error=str(exc)))
    results.sort(key=lambda item: (item.delay_ms is None, item.delay_ms if item.delay_ms is not None else 10**9, item.name))
    return results


def rotate_selector_once(
    client: ClashAPI,
    *,
    selector_name: str,
    policy: CandidatePolicy,
    confirm_switch: Callable[[str | None, str, int | None], bool],
    test_url: str = DEFAULT_TEST_URL,
    timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS,
    recent_failures: dict[str, float] | None = None,
    recent_failure_ttl: int = DEFAULT_RECENT_FAILURE_TTL,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> RotationResult:
    selector = client.selector(selector_name)
    current = selector.get("now") if isinstance(selector.get("now"), str) else None
    candidates = selector_candidates(
        selector,
        current=current,
        policy=policy,
        recent_failures=recent_failures,
        recent_failure_ttl=recent_failure_ttl,
    )
    measured = measure_candidates(client, candidates, test_url=test_url, timeout_ms=timeout_ms)
    tried: list[CandidateResult] = []
    for candidate in measured[:max_candidates]:
        tried.append(candidate)
        if not candidate.healthy or candidate.name == current:
            continue
        if not confirm_switch(current, candidate.name, candidate.delay_ms):
            return RotationResult(
                selector=selector_name,
                current=current,
                chosen=candidate.name,
                tried=tried,
                changed=False,
                reason="switch not confirmed",
            )

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


def _result_to_dict(result: RotationResult) -> dict[str, Any]:
    return {
        "selector": result.selector,
        "current": result.current,
        "chosen": result.chosen,
        "changed": result.changed,
        "reason": result.reason,
        "tried": [asdict(candidate) for candidate in result.tried],
    }


def _candidate_to_dict(candidate: CandidateResult) -> dict[str, Any]:
    return asdict(candidate)


class _Parser(argparse.ArgumentParser):
    def parse_args(self, args: Sequence[str] | None = None, namespace: argparse.Namespace | None = None) -> argparse.Namespace:
        parsed = super().parse_args(args, namespace)
        if not getattr(parsed, "controller_url", None):
            self.error("--controller-url is required")
        if not getattr(parsed, "selector", None):
            self.error("--selector is required")
        return parsed


def _add_common_args(parser: argparse.ArgumentParser, *, suppress_defaults: bool = False) -> None:
    default = argparse.SUPPRESS if suppress_defaults else None
    parser.add_argument(
        "--controller-url",
        default=default,
        help="Explicit Mihomo/Clash controller URL, e.g. http://127.0.0.1:9090",
    )
    parser.add_argument("--selector", default=default, help="Explicit selector/proxy group name to rotate")
    parser.add_argument("--secret", default=default, help="Controller secret; never printed")
    parser.add_argument("--secret-env", default=default, help="Read controller secret from this environment variable")
    parser.add_argument("--test-url", default=argparse.SUPPRESS if suppress_defaults else DEFAULT_TEST_URL)
    parser.add_argument("--timeout-ms", type=int, default=argparse.SUPPRESS if suppress_defaults else DEFAULT_DELAY_TIMEOUT_MS)
    parser.add_argument("--max-candidates", type=int, default=argparse.SUPPRESS if suppress_defaults else DEFAULT_MAX_CANDIDATES)
    parser.add_argument(
        "--recent-failure-ttl",
        type=int,
        default=argparse.SUPPRESS if suppress_defaults else DEFAULT_RECENT_FAILURE_TTL,
    )
    parser.add_argument("--skip-name", action="append", default=argparse.SUPPRESS if suppress_defaults else [])
    parser.add_argument("--include-regex", default=default)
    parser.add_argument("--exclude-regex", default=default)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="telegram-local-proxy-rotation",
        description="Local, opt-in Mihomo/Clash selector helper for Telegram network recovery.",
    )
    _add_common_args(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Measure candidates and print the candidate order without switching.")
    _add_common_args(plan, suppress_defaults=True)

    once = subparsers.add_parser("once", help="Measure candidates and switch once only after confirmation.")
    _add_common_args(once, suppress_defaults=True)
    once.add_argument("--yes", action="store_true", help="Confirm the selected switch without an interactive prompt")
    once.add_argument("--dry-run", action="store_true", help="Print the selected switch but do not call PUT")

    watch = subparsers.add_parser("watch", help="Tail a gateway log and rotate after repeated Telegram network failures.")
    _add_common_args(watch, suppress_defaults=True)
    watch.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    watch.add_argument("--trigger-errors", type=int, default=DEFAULT_TRIGGER_ERRORS)
    watch.add_argument("--cooldown-seconds", type=int, default=DEFAULT_COOLDOWN_SECONDS)
    watch.add_argument("--yes", action="store_true", help="Allow non-interactive switching from watch mode")
    watch.add_argument("--dry-run", action="store_true", help="Log intended switches without calling PUT")
    return parser


def _policy_from_args(args: argparse.Namespace) -> CandidatePolicy:
    skip_names = set(DEFAULT_SKIP_NAMES)
    skip_names.update(args.skip_name or [])
    return CandidatePolicy(
        skip_names=frozenset(skip_names),
        include_regex=args.include_regex,
        exclude_regex=args.exclude_regex,
    )


def _secret_from_args(args: argparse.Namespace) -> str | None:
    if args.secret:
        return args.secret
    if args.secret_env:
        return os.environ.get(args.secret_env)
    return None


def _confirm_from_args(args: argparse.Namespace) -> Callable[[str | None, str, int | None], bool]:
    if getattr(args, "dry_run", False):
        return lambda _current, _chosen, _delay_ms: False
    if getattr(args, "yes", False):
        return lambda _current, _chosen, _delay_ms: True

    def confirm(current: str | None, chosen: str, delay_ms: int | None) -> bool:
        if not sys.stdin.isatty():
            LOG.warning("Refusing to switch without --yes in non-interactive mode")
            return False
        answer = input(f"Switch selector from {current!r} to {chosen!r} (delay={delay_ms}ms)? [y/N] ")
        return answer.strip().lower() in {"y", "yes"}

    return confirm


def _make_client(args: argparse.Namespace) -> ClashClient:
    return ClashClient(args.controller_url, secret=_secret_from_args(args))


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _cmd_plan(args: argparse.Namespace) -> int:
    client = _make_client(args)
    selector = client.selector(args.selector)
    current = selector.get("now") if isinstance(selector.get("now"), str) else None
    candidates = selector_candidates(selector, current=current, policy=_policy_from_args(args))
    measured = measure_candidates(client, candidates, test_url=args.test_url, timeout_ms=args.timeout_ms)
    _print_json(
        {
            "selector": args.selector,
            "current": current,
            "results": [_candidate_to_dict(item) for item in measured[: args.max_candidates]],
        }
    )
    return 0


def _cmd_once(args: argparse.Namespace) -> int:
    result = rotate_selector_once(
        _make_client(args),
        selector_name=args.selector,
        policy=_policy_from_args(args),
        confirm_switch=_confirm_from_args(args),
        test_url=args.test_url,
        timeout_ms=args.timeout_ms,
        recent_failure_ttl=args.recent_failure_ttl,
        max_candidates=args.max_candidates,
    )
    _print_json(_result_to_dict(result))
    if getattr(args, "dry_run", False) and result.chosen:
        return 0
    return 0 if result.changed else 1


def _run_rotation_from_watch(
    *,
    client: ClashAPI,
    state: LogWatchState,
    selector_name: str,
    policy: CandidatePolicy,
    confirm_switch: Callable[[str | None, str, int | None], bool],
    test_url: str,
    timeout_ms: int,
    cooldown_seconds: int,
    recent_failure_ttl: int,
    max_candidates: int,
) -> RotationResult:
    now = _now()
    selector = client.selector(selector_name)
    current = selector.get("now") if isinstance(selector.get("now"), str) else None
    failures = dict(state.recent_failures)
    if current:
        failures[current] = now
    result = rotate_selector_once(
        client,
        selector_name=selector_name,
        policy=policy,
        confirm_switch=confirm_switch,
        test_url=test_url,
        timeout_ms=timeout_ms,
        recent_failures=failures,
        recent_failure_ttl=recent_failure_ttl,
        max_candidates=max_candidates,
    )
    if result.changed and result.chosen:
        state.last_rotation_target = result.chosen
        state.last_rotation_at = now
        state.cooldown_until = now + cooldown_seconds
        state.recent_failures[result.chosen] = now
    return result


def watch_gateway_log(
    *,
    log_path: Path,
    client: ClashAPI,
    selector_name: str,
    policy: CandidatePolicy,
    confirm_switch: Callable[[str | None, str, int | None], bool],
    test_url: str = DEFAULT_TEST_URL,
    timeout_ms: int = DEFAULT_DELAY_TIMEOUT_MS,
    trigger_errors: int = DEFAULT_TRIGGER_ERRORS,
    cooldown_seconds: int = DEFAULT_COOLDOWN_SECONDS,
    recent_failure_ttl: int = DEFAULT_RECENT_FAILURE_TTL,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
) -> None:
    state = LogWatchState()
    log_path = log_path.expanduser()
    with log_path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, os.SEEK_END)
        LOG.info("Watching %s for Telegram network errors", log_path)
        while True:
            line = handle.readline()
            if not line:
                time.sleep(0.5)
                continue
            if not update_log_state(state, line, trigger_errors=trigger_errors):
                continue
            if _now() < state.cooldown_until:
                LOG.info("Rotation trigger ignored during cooldown")
                continue
            try:
                result = _run_rotation_from_watch(
                    client=client,
                    state=state,
                    selector_name=selector_name,
                    policy=policy,
                    confirm_switch=confirm_switch,
                    test_url=test_url,
                    timeout_ms=timeout_ms,
                    cooldown_seconds=cooldown_seconds,
                    recent_failure_ttl=recent_failure_ttl,
                    max_candidates=max_candidates,
                )
                LOG.warning("Telegram proxy rotation result: %s", _result_to_dict(result))
            except Exception as exc:  # noqa: BLE001 - watcher should stay alive.
                LOG.exception("Telegram proxy rotation failed: %s", exc)


def _cmd_watch(args: argparse.Namespace) -> int:
    watch_gateway_log(
        log_path=Path(args.log_path),
        client=_make_client(args),
        selector_name=args.selector,
        policy=_policy_from_args(args),
        confirm_switch=_confirm_from_args(args),
        test_url=args.test_url,
        timeout_ms=args.timeout_ms,
        trigger_errors=args.trigger_errors,
        cooldown_seconds=args.cooldown_seconds,
        recent_failure_ttl=args.recent_failure_ttl,
        max_candidates=args.max_candidates,
    )
    return 0


def _configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _configure_logging()
    if args.secret is not None and args.secret == "-":
        args.secret = getpass.getpass("Mihomo/Clash controller secret: ")
    if args.command == "plan":
        return _cmd_plan(args)
    if args.command == "once":
        return _cmd_once(args)
    if args.command == "watch":
        return _cmd_watch(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
