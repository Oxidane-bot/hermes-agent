"""Tests for gateway.telegram_rotation_supervisor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from gateway.telegram_rotation_supervisor import RotationSupervisor


def _write_pid(path: Path, pid: int = 12345) -> None:
    path.write_text(str(pid), encoding="utf-8")


def test_supervisor_starts_helper_when_any_profile_active(tmp_path):
    hermes_home = tmp_path / "hermes"
    profiles_root = hermes_home / "profiles" / "coder"
    profiles_root.mkdir(parents=True)
    _write_pid(profiles_root / "gateway.pid")
    supervisor = RotationSupervisor(hermes_home=hermes_home)
    with patch.object(supervisor, "_start_helper") as start_helper, patch.object(supervisor, "_stop_helper") as stop_helper:
        supervisor.tick()
    start_helper.assert_called_once()
    stop_helper.assert_not_called()


def test_supervisor_stops_helper_when_no_profile_active(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(parents=True)
    supervisor = RotationSupervisor(hermes_home=hermes_home)
    supervisor._state.helper_pid = 23456
    with patch.object(supervisor, "_helper_alive", return_value=True), patch.object(supervisor, "_stop_helper") as stop_helper:
        supervisor.tick()
    stop_helper.assert_called_once()


def test_supervisor_noop_when_state_matches(tmp_path):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir(parents=True)
    supervisor = RotationSupervisor(hermes_home=hermes_home)
    with patch.object(supervisor, "_start_helper") as start_helper, patch.object(supervisor, "_stop_helper") as stop_helper, patch.object(supervisor, "_helper_alive", return_value=False):
        supervisor.tick()
    start_helper.assert_not_called()
    stop_helper.assert_not_called()
