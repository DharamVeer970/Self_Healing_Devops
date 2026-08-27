"""REMEDIATE + VERIFY NODES - fixes mutate only the simulated state."""
import json
from unittest import mock

import pytest

from agent import remediate

STATE = {"running": True, "disk_used_pct": 97.0,
         "memory_mb": 400.0, "backend_up": False}


def _seed_state(fake_world, state):
    with open(fake_world["state_file"], "w", encoding="utf-8") as f:
        json.dump(state, f)


def _read_state(fake_world):
    with open(fake_world["state_file"], "r", encoding="utf-8") as f:
        return json.load(f)


def test_rotate_logs_frees_disk_and_archives(fake_world):
    fake_world["log_file"].write_text("old log content\n", encoding="utf-8")
    _seed_state(fake_world, STATE)

    message = remediate.apply("rotate_logs")
    state = _read_state(fake_world)

    assert state["disk_used_pct"] == 32.0          # 97 - 65
    assert not fake_world["log_file"].read_text()   # fresh empty log
    archives = list(fake_world["logs"].glob("app.log.archived_*"))
    assert len(archives) == 1
    assert "archived" in message


def test_restart_service_resets_state_and_spawns_process(fake_world):
    broken = dict(STATE, running=False, memory_mb=1500.0)
    _seed_state(fake_world, broken)

    with mock.patch.object(remediate.subprocess, "Popen") as popen:
        message = remediate.apply("restart_service")

    state = _read_state(fake_world)
    assert state["running"] is True
    assert state["memory_mb"] == 250.0
    assert state["backend_up"] is True
    popen.assert_called_once()
    assert "restarted" in message.lower()


def test_apply_unknown_action_raises():
    with pytest.raises(ValueError, match="No fix implemented"):
        remediate.apply("rm_rf")


def test_verify_passes_on_healthy_state(fake_world):
    healthy = {"running": True, "disk_used_pct": 32.0,
               "memory_mb": 250.0, "backend_up": True}
    _seed_state(fake_world, healthy)

    ok, problems, state = remediate.verify()
    assert ok is True
    assert problems == []
    assert state == healthy


def test_verify_reports_every_remaining_problem(fake_world):
    bad = {"running": False, "disk_used_pct": 95.0,
           "memory_mb": 2048.0, "backend_up": False}
    _seed_state(fake_world, bad)

    ok, problems, _ = remediate.verify()
    assert ok is False
    assert problems == [
        "service is down", "disk still full",
        "memory exhausted again", "backend still unreachable",
    ]
