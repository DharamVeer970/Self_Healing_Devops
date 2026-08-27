"""End-to-end agentic loop: MONITOR -> DIAGNOSE -> SAFETY -> REMEDIATE."""
import json

import main as agent_main


def _seed_incident(fake_world):
    fake_world["log_file"].write_text(
        "2026-08-27 10:00:01 | ERROR [pid 123] OSError: [Errno 28] "
        "No space left on device - failed to write (disk=97%)\n",
        encoding="utf-8",
    )
    with open(fake_world["state_file"], "w", encoding="utf-8") as f:
        json.dump({"running": True, "disk_used_pct": 97.0,
                   "memory_mb": 400.0, "backend_up": True}, f)


def _read_state(fake_world):
    return json.loads(fake_world["state_file"].read_text(encoding="utf-8"))


def test_quiet_cycle_takes_no_action(fake_world, capsys, monkeypatch):
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    fake_world["log_file"].write_text(
        "INFO  GET /health 200 OK\n", encoding="utf-8")

    assert agent_main.scan_cycle(auto=False) is False
    out = capsys.readouterr().out
    assert "all clear" in out


def test_silent_log_file_produces_no_output(fake_world, capsys, monkeypatch):
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)

    assert agent_main.scan_cycle(auto=False) is False
    assert capsys.readouterr().out == ""


def test_escalation_reports_to_human_without_fixing(
        fake_world, capsys, monkeypatch):
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    fake_world["log_file"].write_text(
        "ERROR Segmentation fault (core dumped)\n", encoding="utf-8")
    before = _state_missing_ok(fake_world)

    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    assert "ESCALATED TO HUMAN" in out
    assert "rotate_logs" not in out
    assert before == _state_missing_ok(fake_world)   # state untouched


def test_auto_mode_heals_disk_full_and_verifies(
        fake_world, capsys, monkeypatch):
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    _seed_incident(fake_world)

    assert agent_main.scan_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert "INCIDENT REPORT" in out
    assert "HEALTHY" in out                          # verification passed
    assert _read_state(fake_world)["disk_used_pct"] == 32.0


def test_operator_approves_the_proposed_fix(
        fake_world, capsys, monkeypatch):
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda prompt: "y")
    _seed_incident(fake_world)

    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    assert "Action taken" in out                     # fix actually ran
    assert "HEALTHY" in out                          # verification passed


def test_operator_declines_and_nothing_is_applied(
        fake_world, capsys, monkeypatch):
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    _seed_incident(fake_world)

    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    assert "Skipped by operator" in out
    assert _read_state(fake_world)["disk_used_pct"] == 97.0  # untouched


def _state_missing_ok(fake_world):
    path = fake_world["state_file"]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
