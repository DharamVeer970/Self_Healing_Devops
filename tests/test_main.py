"""main.py: scan_cycle() full agentic loop + _utf8_console()/report_incident."""
import io
import json
import sys
from unittest import mock

import pytest

import main as agent_main


def _state(sandbox, state):
    with open(sandbox["state_file"], "w", encoding="utf-8") as f:
        json.dump(state, f)


def _wire(sandbox, monkeypatch):
    monkeypatch.setattr(agent_main.monitor, "LOG_FILE",
                        str(sandbox["log_file"]))
    monkeypatch.setattr(agent_main.monitor, "OFFSET_FILE",
                        str(sandbox["offset_file"]))
    monkeypatch.setattr(agent_main.remediate, "STATE_FILE",
                        str(sandbox["state_file"]))
    monkeypatch.setattr(agent_main.time, "sleep", lambda s: None)
    monkeypatch.setattr(agent_main.diagnose, "llm_explanation",
                        lambda e, d: None)
    # Prevent the script writer from patching files during tests
    monkeypatch.setattr(agent_main.script_writer, "attempt_fix",
                        lambda e, d: {"strategy": "mocked",
                                     "needs_review": True,
                                     "message": "mocked for test"})
    # Prevent URL checker from doing real network calls
    monkeypatch.setattr(agent_main.url_checker, "check_all",
                        lambda: [])


# ---- scan_cycle: no errors -------------------------------------------------

def test_quiet_nothing_no_output(sandbox, capsys, monkeypatch):
    _wire(sandbox, monkeypatch)
    assert agent_main.scan_cycle(auto=False) is False
    assert capsys.readouterr().out == ""


def test_heartbeat_prints_all_clear(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("INFO GET /health 200 OK\n")
    _wire(sandbox, monkeypatch)
    assert agent_main.scan_cycle(auto=False) is False
    assert "all clear" in capsys.readouterr().out


# ---- scan_cycle: escalation / blocklist ------------------------------------

def test_unknown_error_escalates(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR Segmentation fault\n")
    _wire(sandbox, monkeypatch)
    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    # Script writer now runs before human escalation
    assert "SCRIPT WRITER" in out


def test_blocklisted_action_escalates(sandbox, capsys, monkeypatch):
    # Force a diagnosis that maps to a dangerous action via a patched rule.
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR some made up thing\n")
    _wire(sandbox, monkeypatch)
    monkeypatch.setattr(agent_main.safety, "check",
                        lambda a: (False, "'rm_rf' is on the blocklist."))
    monkeypatch.setattr(agent_main.diagnose, "classify",
                        lambda e: {"action": "rm_rf", "error_line": "E",
                                   "diagnosis": "D", "confidence": 0.5})
    assert agent_main.scan_cycle(auto=False) is True
    assert "SCRIPT WRITER" in capsys.readouterr().out


# ---- scan_cycle: auto healing ----------------------------------------------

def test_auto_heals_disk_full(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR OSError: No space left on device\n")
    _state(sandbox, {"running": True, "disk_used_pct": 97.0,
                     "memory_mb": 400.0, "backend_up": True})
    _wire(sandbox, monkeypatch)
    assert agent_main.scan_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert "INCIDENT REPORT" in out
    assert "HEALTHY" in out
    s = json.loads(open(sandbox["state_file"], encoding="utf-8").read())
    assert s["disk_used_pct"] == 32.0


# ---- scan_cycle: operator approval -----------------------------------------

def test_operator_approves_fix(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR OSError: No space left on device\n")
    _state(sandbox, {"running": True, "disk_used_pct": 97.0,
                     "memory_mb": 400.0, "backend_up": True})
    _wire(sandbox, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda p: "y")
    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    assert "Action taken" in out


def test_operator_declines_no_fix(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR OSError: No space left on device\n")
    _state(sandbox, {"running": True, "disk_used_pct": 97.0,
                     "memory_mb": 400.0, "backend_up": True})
    _wire(sandbox, monkeypatch)
    monkeypatch.setattr("builtins.input", lambda p: "n")
    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    assert "Skipped by operator" in out
    s = json.loads(open(sandbox["state_file"], encoding="utf-8").read())
    assert s["disk_used_pct"] == 97.0              # untouched


# ---- scan_cycle: URL connectivity goes to script writer ----------------------

def test_url_down_goes_to_script_writer(sandbox, capsys, monkeypatch):
    """A URL down event fires the script writer even with no log errors."""
    _wire(sandbox, monkeypatch)
    monkeypatch.setattr(agent_main.url_checker, "check_all",
                        lambda: [{"url": "https://x.onrender.com", "from": "up",
                                  "to": "down", "at": "12:00:00",
                                  "detail": "Connection refused"}])
    assert agent_main.scan_cycle(auto=False) is True
    out = capsys.readouterr().out
    assert "URL DOWN" in out
    assert "SCRIPT WRITER" in out


# ---- report_incident --------------------------------------------------------

def test_report_incident_delegates_to_emit(monkeypatch, capsys):
    emit = mock.Mock()
    monkeypatch.setattr(agent_main.reporting, "emit", emit)
    agent_main.report_incident(["e"], {"action": "a", "error_line": "E",
                                       "diagnosis": "D", "confidence": 0.5})
    emit.assert_called_once()


# ---- _utf8_console ----------------------------------------------------------

def test_utf8_console_reconfigures_streams(monkeypatch):
    class FakeStream:
        def __init__(self):
            self.reconfigured = None

        def reconfigure(self, **kw):
            self.reconfigured = kw

    out, err = FakeStream(), FakeStream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    agent_main._utf8_console()
    assert out.reconfigured == {"encoding": "utf-8", "errors": "replace"}
    assert err.reconfigured == {"encoding": "utf-8", "errors": "replace"}


def test_utf8_console_skips_streams_without_reconfigure(monkeypatch):
    class Plain:
        pass

    monkeypatch.setattr(sys, "stdout", Plain())
    monkeypatch.setattr(sys, "stderr", Plain())
    # Should not raise.
    agent_main._utf8_console()
