"""Remediate node: fixes, boundary logic, verification honesty."""
import json
from unittest import mock

import pytest

from agent import remediate
from conftest import write




def _seed(sandbox, state):
    write(sandbox["state_file"], json.dumps(state))


def _state(sandbox):
    return json.loads(open(sandbox["state_file"], encoding="utf-8").read())


# ---- apply dispatch --------------------------------------------------------

def test_apply_known_actions(sandbox):
    _seed(sandbox, {"running": True, "disk_used_pct": 50.0,
                    "memory_mb": 300.0, "backend_up": True})
    with mock.patch.object(remediate.subprocess, "Popen"):
        assert "restarted" in remediate.apply("restart_service").lower()


@pytest.mark.parametrize("action", ["rm_rf", "drop_database", "", None,
                                    "rotate_log", "restart_services"])
def test_apply_unknown_action_raises(sandbox, action):
    with pytest.raises(ValueError, match="No fix implemented"):
        remediate.apply(action)


# ---- rotate_logs -----------------------------------------------------------

def test_rotate_logs_frees_disk(sandbox):
    _seed(sandbox, {"running": True, "disk_used_pct": 97.0,
                    "memory_mb": 400.0, "backend_up": True})
    msg = remediate.rotate_logs()
    assert _state(sandbox)["disk_used_pct"] == 32.0   # 97 - 65
    assert "archived" in msg


def test_rotate_logs_never_goes_below_floor(sandbox):
    _seed(sandbox, {"disk_used_pct": 30.0})           # 30 - 65 -> clamped
    remediate.rotate_logs()
    assert _state(sandbox)["disk_used_pct"] == 20.0


def test_rotate_logs_at_exactly_floor(sandbox):
    _seed(sandbox, {"disk_used_pct": 85.0})           # 85 - 65 = 20, no clamp
    remediate.rotate_logs()
    assert _state(sandbox)["disk_used_pct"] == 20.0


def test_rotate_logs_archives_and_empties_log(sandbox):
    _seed(sandbox, {"disk_used_pct": 70.0})
    write(sandbox["log_file"], "old content\n")
    remediate.rotate_logs()
    archives = list(sandbox["logs"].glob("app.log.archived_*"))
    assert len(archives) == 1
    assert open(sandbox["log_file"], encoding="utf-8").read() == ""


def test_rotate_logs_missing_log_file_no_crash(sandbox):
    _seed(sandbox, {"disk_used_pct": 70.0})           # no log file exists
    msg = remediate.rotate_logs()                     # swallows FileNotFoundError
    assert "Freed simulated disk" in msg


def test_rotate_logs_message_reports_both_values(sandbox):
    _seed(sandbox, {"disk_used_pct": 50.0})
    msg = remediate.rotate_logs()
    assert "50% -> 20%" in msg


# ---- restart_service -------------------------------------------------------

def test_restart_resets_state_fields(sandbox):
    _seed(sandbox, {"running": False, "memory_mb": 2000.0,
                    "backend_up": False, "disk_used_pct": 40.0})
    with mock.patch.object(remediate.subprocess, "Popen"):
        msg = remediate.restart_service()
    s = _state(sandbox)
    assert s["running"] is True
    assert s["memory_mb"] == 250.0
    assert s["backend_up"] is True
    assert "restarted" in msg.lower()


def test_restart_spawns_detached_process(sandbox):
    _seed(sandbox, {"running": False})
    with mock.patch.object(remediate.subprocess, "Popen") as popen:
        remediate.restart_service()
    popen.assert_called_once()
    args = popen.call_args[0][0]
    assert args[-1].endswith("flaky_app.py")          # targets FLAKY_APP


def test_restart_passes_creationflags_on_windows(sandbox, monkeypatch):
    _seed(sandbox, {"running": False})
    monkeypatch.setattr(remediate.os, "name", "nt")
    with mock.patch.object(remediate.subprocess, "Popen") as popen:
        remediate.restart_service()
    kw = popen.call_args[1]
    assert kw["creationflags"] != 0                   # detached on Windows


def test_restart_no_creationflags_on_posix(sandbox, monkeypatch):
    _seed(sandbox, {"running": False})
    monkeypatch.setattr(remediate.os, "name", "posix")
    with mock.patch.object(remediate.subprocess, "Popen") as popen:
        remediate.restart_service()
    kw = popen.call_args[1]
    assert kw["creationflags"] == 0


# ---- verify -----------------------------------------------------------------

HEALTHY = {"running": True, "disk_used_pct": 32.0,
           "memory_mb": 250.0, "backend_up": True}


def test_verify_healthy(sandbox):
    _seed(sandbox, HEALTHY)
    ok, problems, state = remediate.verify()
    assert ok is True
    assert problems == []
    assert state == HEALTHY


@pytest.mark.parametrize("mutate,expected", [
    ({"running": False}, ["service is down"]),
    ({"disk_used_pct": 90.0}, ["disk still full"]),
    ({"disk_used_pct": 89.9}, []),                   # boundary: under 90 OK
    ({"memory_mb": 1024.0}, ["memory exhausted again"]),
    ({"memory_mb": 1023.9}, []),                     # boundary: under 1024 OK
    ({"backend_up": False}, ["backend still unreachable"]),
])
def test_verify_individual_problems(sandbox, mutate, expected):
    _seed(sandbox, dict(HEALTHY, **mutate))
    ok, problems, _ = remediate.verify()
    assert problems == expected
    assert ok is (not expected)


def test_verify_reports_all_problems_together(sandbox):
    _seed(sandbox, {"running": False, "disk_used_pct": 95.0,
                    "memory_mb": 2048.0, "backend_up": False})
    ok, problems, _ = remediate.verify()
    assert ok is False
    assert set(problems) == {"service is down", "disk still full",
                             "memory exhausted again",
                             "backend still unreachable"}


def test_verify_missing_keys_use_unsafe_defaults(sandbox):
    # Missing disk/memory keys default to FULL values -> must be flagged.
    _seed(sandbox, {"running": True})
    _, problems, _ = remediate.verify()
    assert "disk still full" in problems
    assert "memory exhausted again" in problems

