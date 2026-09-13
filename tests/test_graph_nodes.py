"""LangGraph node behaviors (each node in isolation)."""
import json
from unittest import mock

import pytest

from agent import graph as g



pytestmark = pytest.mark.skipif(not g.HAS_LANGGRAPH,
                                reason="langgraph not installed")

DISK_DIAG = {"diagnosis": {"action": "rotate_logs", "error_line": "ERROR x",
                            "diagnosis": "Disk is full.", "confidence": 0.97},
             "errors": ["ERROR x"]}


def test_node_monitor_tails_and_extracts(sandbox, monkeypatch):
    monkeypatch.setattr(g.monitor, "LOG_FILE", str(sandbox["log_file"]))
    monkeypatch.setattr(g.monitor, "OFFSET_FILE", str(sandbox["offset_file"]))
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("INFO ok\nERROR bad\nFATAL worse\n")
    out = g.node_monitor({})
    assert out["lines"] == ["INFO ok", "ERROR bad", "FATAL worse"]
    assert out["errors"] == ["ERROR bad", "FATAL worse"]


def test_node_diagnose_classifies_errors():
    out = g.node_diagnose({"errors": ["ERROR No space left on device"]})
    assert out["diagnosis"]["action"] == "rotate_logs"


def test_node_diagnose_no_errors_none():
    out = g.node_diagnose({"errors": []})
    assert out["diagnosis"] is None
    assert out["verification"] is None
    assert out["declined"] is False


def test_node_safety_allows_known():
    out = g.node_safety({"diagnosis": {"action": "rotate_logs"}})
    assert out["allowed"] is True and out["reason"]


def test_node_safety_blocks_dangerous():
    out = g.node_safety({"diagnosis": {"action": "rm_rf"}})
    assert out["allowed"] is False


def test_node_remediate_auto_applies(monkeypatch):
    monkeypatch.setattr(g.remediate, "apply", lambda a: "Applied " + a)
    out = g.node_remediate({**DISK_DIAG, "allowed": True, "auto": True})
    assert out["action_taken"] is True
    assert out["outcome"] == "Applied rotate_logs"
    assert out["attempt"] == 1


def test_node_remediate_increments_attempt(monkeypatch):
    monkeypatch.setattr(g.remediate, "apply", lambda a: "ok")
    out = g.node_remediate({**DISK_DIAG, "allowed": True, "auto": True,
                            "attempt": 2})
    assert out["attempt"] == 3


def test_node_remediate_approval_yes_applies(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda p: "y")
    monkeypatch.setattr(g.remediate, "apply", lambda a: "ok")
    out = g.node_remediate({**DISK_DIAG, "allowed": True, "auto": False,
                            "attempt": 0})
    assert out["action_taken"] is True and out["declined"] is False


def test_node_remediate_approval_no_declines(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda p: "n")
    monkeypatch.setattr(
        g.remediate, "apply",
        mock.Mock(side_effect=AssertionError("must not apply")))
    out = g.node_remediate({**DISK_DIAG, "allowed": True, "auto": False,
                            "attempt": 0})
    assert out["declined"] is True
    assert out["outcome"] == "Skipped by operator."
    assert out["attempt"] == 1


def test_node_remediate_not_allowed_skips_apply():
    with mock.patch.object(g.remediate, "apply",
                           mock.Mock(side_effect=AssertionError("no"))):
        out = g.node_remediate({**DISK_DIAG, "allowed": False})
    assert out == {"action_taken": True}


def test_node_verify_waits_and_checks(sandbox, monkeypatch):
    with open(sandbox["state_file"], "w", encoding="utf-8") as f:
        json.dump({"running": True, "disk_used_pct": 10.0,
                   "memory_mb": 100.0, "backend_up": True}, f)
    monkeypatch.setattr(g, "VERIFY_DELAY", 0)
    monkeypatch.setattr(g, "time", mock.Mock(sleep=mock.Mock()))
    monkeypatch.setattr(g.remediate, "STATE_FILE", str(sandbox["state_file"]))
    out = g.node_verify({})
    assert out["verification"][0] is True


def test_node_report_declined_prints_separator(capsys):
    out = g.node_report({"declined": True, "diagnosis": None})
    assert "===" in capsys.readouterr().out
    assert out.get("declined") is True


def test_node_report_no_diagnosis_reports_still_broken(capsys, monkeypatch):
    monkeypatch.setattr(g, "_now_short", lambda: "00:00:00")
    with mock.patch.object(
            g.reporting, "emit",
            mock.Mock(side_effect=AssertionError("must not emit"))):
        out = g.node_report({"diagnosis": None, "attempt": 2,
                             "verification": (False, ["down"], {})})
    assert "STILL BROKEN" in capsys.readouterr().out
    assert out["action_taken"] is True


def test_node_report_normal_emits(monkeypatch):
    monkeypatch.setattr(g.diagnose, "llm_explanation", lambda e, d: None)
    emit = mock.Mock()
    monkeypatch.setattr(g.reporting, "emit", emit)
    g.node_report({"diagnosis": {"action": "rotate_logs", "confidence": 0.97,
                                 "error_line": "E", "diagnosis": "D"},
                   "fresh_diagnosis": True,
                   "allowed": True, "errors": [], "reason": "r",
                   "verification": (True, [], {})})
    emit.assert_called_once()


def test_node_report_escalation_prints_warning(capsys, monkeypatch):
    monkeypatch.setattr(g.diagnose, "llm_explanation", lambda e, d: None)
    monkeypatch.setattr(g.reporting, "emit", mock.Mock())
    out = g.node_report({"diagnosis": {"action": "escalate_to_human",
                                       "confidence": 0.0,
                                       "error_line": "E", "diagnosis": "D"},
                         "fresh_diagnosis": True,
                         "allowed": True, "errors": [], "reason": "Esc.",
                         "verification": None})
    assert "ESCALATED TO HUMAN" in capsys.readouterr().out
    assert out["action_taken"] is True


def test_node_clear_with_heartbeat_prints(capsys, monkeypatch):
    monkeypatch.setattr(g, "_now_short", lambda: "00:00:00")
    g.node_clear({"lines": ["INFO heartbeat"]})
    assert "all clear" in capsys.readouterr().out


def test_node_clear_no_heartbeat_silent(capsys):
    g.node_clear({"lines": ["ERROR alone"]})
    assert capsys.readouterr().out == ""

