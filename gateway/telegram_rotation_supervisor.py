"""Local Telegram rotation companion supervisor.

This process is meant to live next to the gateway process and keep the
Telegram rotation helper running only while at least one Hermes profile is
active.  It does not communicate over Telegram; it watches local profile and
process state and starts/stops the helper accordingly.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from hermes_constants import get_hermes_home

DEFAULT_CHECK_INTERVAL = float(os.getenv("HERMES_TELEGRAM_ROTATOR_SUPERVISOR_INTERVAL", "5"))
DEFAULT_PID_FILE = "telegram_rotation_helper.pid"
DEFAULT_STATE_FILE = "telegram_rotation_helper.state.json"


@dataclass(slots=True)
class SupervisorState:
    helper_pid: int | None = None
    helper_cmd: list[str] | None = None
    last_started_at: float | None = None
    last_stopped_at: float | None = None


class RotationSupervisor:
    def __init__(self, hermes_home: Path | None = None) -> None:
        self.hermes_home = hermes_home or get_hermes_home()
        self.state_path = self.hermes_home / DEFAULT_STATE_FILE
        self.pid_path = self.hermes_home / DEFAULT_PID_FILE
        self._state = self._load_state()

    def _load_state(self) -> SupervisorState:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return SupervisorState(
                helper_pid=int(data.get("helper_pid")) if data.get("helper_pid") else None,
                helper_cmd=list(data.get("helper_cmd") or []) or None,
                last_started_at=data.get("last_started_at"),
                last_stopped_at=data.get("last_stopped_at"),
            )
        except Exception:
            return SupervisorState()

    def _save_state(self) -> None:
        payload = {
            "helper_pid": self._state.helper_pid,
            "helper_cmd": self._state.helper_cmd or [],
            "last_started_at": self._state.last_started_at,
            "last_stopped_at": self._state.last_stopped_at,
        }
        self.state_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _helper_alive(self) -> bool:
        pid = self._state.helper_pid
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def _profiles_active(self) -> bool:
        profiles_root = self.hermes_home / "profiles"
        default_gateway = self.hermes_home / "gateway.pid"
        if default_gateway.exists():
            return True
        if not profiles_root.is_dir():
            return False
        for entry in profiles_root.iterdir():
            if not entry.is_dir():
                continue
            pid_file = entry / "gateway.pid"
            if pid_file.exists():
                return True
        return False

    def _start_helper(self) -> None:
        cmd = [sys.executable, "-m", "gateway.telegram_proxy_rotation", "watch"]
        proc = subprocess.Popen(
            cmd,
            cwd=str(self.hermes_home / "hermes-agent"),
            env={**os.environ, "HERMES_HOME": str(self.hermes_home)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._state.helper_pid = proc.pid
        self._state.helper_cmd = cmd
        self._state.last_started_at = time.time()
        self._save_state()

    def _stop_helper(self) -> None:
        if self._state.helper_pid:
            try:
                os.kill(self._state.helper_pid, signal.SIGTERM)
            except OSError:
                pass
        self._state.helper_pid = None
        self._state.last_stopped_at = time.time()
        self._save_state()

    def tick(self) -> None:
        active = self._profiles_active()
        alive = self._helper_alive()
        if active and not alive:
            self._start_helper()
        elif not active and alive:
            self._stop_helper()

    def run_forever(self, interval: float = DEFAULT_CHECK_INTERVAL) -> None:
        while True:
            self.tick()
            time.sleep(interval)


def main() -> int:
    RotationSupervisor().run_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
