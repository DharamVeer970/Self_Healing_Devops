"""LangGraph engine: build() and full run_cycle() integration."""
import json

import pytest

from agent import graph as g



pytestmark = pytest.mark.skipif(not g.HAS_LANGGRAPH,
                                reason="langgraph not installed")


@pytest.fixture(autouse=True)
def reset_graph():
    g._APP = None
    yield
    g._APP = None


def _state(sandbox, state):
    with open(sandbox["state_file"], "w", encoding="utf-8") as f:
        json.dump(state, f)


def _wire(sandbox, monkeypatch):
    monkeypatch.setattr(g.monitor, "LOG_FILE", str(sandbox["log_file"]))
    monkeypatch.setattr(g.monitor, "OFFSET_FILE", str(sandbox["offset_file"]))
    monkeypatch.setattr(g.remediate, "STATE_FILE", str(sandbox["state_file"]))
    monkeypatch.setattr(g, "VERIFY_DELAY", 0)
    monkeypatch.setattr(g.diagnose, "llm_explanation", lambda e, d: None)


def test_build_returns_compiled_graph():
    assert g.build() is not None


def test_build_is_cached():
    assert g.build() is g.build()


def test_build_raises_when_langgraph_missing(monkeypatch):
    monkeypatch.setattr(g, "HAS_LANGGRAPH", False)
    monkeypatch.setattr(g, "_APP", None)
    with pytest.raises(ImportError, match="langgraph"):
        g.build()


def test_run_cycle_heals_disk_full(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR OSError: No space left on device\n")
    _state(sandbox, {"running": True, "disk_used_pct": 97.0,
                     "memory_mb": 400.0, "backend_up": True})
    _wire(sandbox, monkeypatch)
    assert g.run_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert "INCIDENT REPORT" in out
    assert "HEALTHY" in out
    # state healed:
    s = json.loads(open(sandbox["state_file"], encoding="utf-8").read())
    assert s["disk_used_pct"] == 32.0


def test_run_cycle_quiet_no_action(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("INFO heartbeat ok\n")
    _wire(sandbox, monkeypatch)
    assert g.run_cycle(auto=True) is False
    assert "all clear" in capsys.readouterr().out


def test_run_cycle_escalation_registers_action(sandbox, capsys, monkeypatch):
    with open(sandbox["log_file"], "w", encoding="utf-8") as f:
        f.write("ERROR Segmentation fault\n")
    _wire(sandbox, monkeypatch)
    assert g.run_cycle(auto=True) is True
    assert "ESCALATED TO HUMAN" in capsys.readouterr().out
