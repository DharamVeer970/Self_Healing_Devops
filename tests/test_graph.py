"""LANGGRAPH ENGINE - cyclic state machine behavior (graph mode)."""
import json

import pytest

import agent.graph as agent_graph

pytestmark = pytest.mark.skipif(
    not agent_graph.HAS_LANGGRAPH, reason="langgraph not installed")


@pytest.fixture(autouse=True)
def fast_and_fresh(monkeypatch):
    monkeypatch.setattr(agent_graph, "VERIFY_DELAY", 0)
    agent_graph._APP = None            # rebuild graph per test (cheap)
    yield
    agent_graph._APP = None


def _seed_state(fake_world, state):
    fake_world["state_file"].write_text(json.dumps(state), encoding="utf-8")


def _read_state(fake_world):
    return json.loads(fake_world["state_file"].read_text(encoding="utf-8"))


def test_quiet_log_routes_to_clear_node(fake_world, capsys):
    fake_world["log_file"].write_text("INFO  GET /health 200 OK\n",
                                      encoding="utf-8")

    assert agent_graph.run_cycle(auto=True) is False
    assert "all clear" in capsys.readouterr().out


def test_disk_full_healed_through_the_graph(fake_world, capsys):
    fake_world["log_file"].write_text(
        "ERROR [pid 1] OSError: [Errno 28] No space left on device\n",
        encoding="utf-8")
    _seed_state(fake_world, {"running": True, "disk_used_pct": 97.0,
                             "memory_mb": 400.0, "backend_up": True})

    assert agent_graph.run_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert "INCIDENT REPORT" in out
    assert "HEALTHY" in out
    assert _read_state(fake_world)["disk_used_pct"] == 32.0


def test_unknown_error_escalates_without_fixing(fake_world, capsys):
    fake_world["log_file"].write_text("ERROR Segmentation fault\n",
                                      encoding="utf-8")
    before = _state_or_none(fake_world)

    assert agent_graph.run_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert "ESCALATED TO HUMAN" in out
    assert "rotate_logs" not in out
    assert _state_or_none(fake_world) == before   # untouched


def test_retry_loop_gives_up_after_max_attempts(fake_world, capsys,
                                                monkeypatch):
    """Disk gets fixed but backend stays down -> verify fails -> retry."""
    calls = []
    real_apply = agent_graph.remediate.apply

    def counting_apply(action):
        calls.append(action)
        return real_apply(action)

    monkeypatch.setattr(agent_graph.remediate, "apply", counting_apply)
    fake_world["log_file"].write_text(
        "ERROR OSError: [Errno 28] No space left on device\n",
        encoding="utf-8")
    _seed_state(fake_world, {"running": True, "disk_used_pct": 97.0,
                             "memory_mb": 250.0, "backend_up": False})

    assert agent_graph.run_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert len(calls) <= agent_graph.MAX_ATTEMPTS      # loop terminated
    assert "BROKEN" in out or "HEALTHY" in out         # a final report exists


def _state_or_none(fake_world):
    path = fake_world["state_file"]
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))
