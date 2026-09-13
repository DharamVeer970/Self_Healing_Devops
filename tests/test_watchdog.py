"""Watchdog: remote monitor classification, diagnose rules, Render remediate."""
import json
from unittest import mock

import pytest

from watchdog import diagnose, monitor, remediate
from watchdog import run as wd_run


def _resp(code, body=b""):
    return code, 123, body


# ---- monitor.check_server ---------------------------------------------------

def test_server_awake_on_first_probe(monkeypatch):
    monkeypatch.setattr(monitor, "PING_DELAYS", (0, 0, 0))
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    monkeypatch.setattr(monitor, "_request", lambda *a, **k: _resp(200))
    status, attempts = monitor.check_server()
    assert status == "awake"
    assert attempts == [(200, 123)]


def test_server_sleep_recovered(monkeypatch):
    monkeypatch.setattr(monitor, "PING_DELAYS", (0, 0, 0))
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    codes = iter([503, 200])
    monkeypatch.setattr(monitor, "_request",
                        lambda *a, **k: _resp(next(codes)))
    status, attempts = monitor.check_server()
    assert status == "sleep_recovered"
    assert len(attempts) == 2


def test_server_down_after_all_retries(monkeypatch):
    monkeypatch.setattr(monitor, "PING_DELAYS", (0, 0, 0))
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    monkeypatch.setattr(monitor, "_request", lambda *a, **k: _resp(503))
    status, attempts = monitor.check_server()
    assert status == "down"
    assert len(attempts) == 3


def test_server_down_on_timeout_too(monkeypatch):
    monkeypatch.setattr(monitor, "PING_DELAYS", (0, 0, 0))
    monkeypatch.setattr(monitor.time, "sleep", lambda s: None)
    monkeypatch.setattr(monitor, "_request", lambda *a, **k: _resp(None))
    assert monitor.check_server()[0] == "down"


def test_chat_probe_parses_reply(monkeypatch):
    body = json.dumps({"reply": "He built a self-healing agent!"}).encode()
    monkeypatch.setattr(monitor, "_request",
                        lambda *a, **k: (200, 500, body))
    code, ms, snippet = monitor.check_chat()
    assert code == 200
    assert "self-healing" in snippet


def test_chat_probe_sends_empty_query(monkeypatch):
    captured = {}

    def spy(url, payload=None, timeout=60):
        captured["payload"] = payload
        return (200, 100, b"")
    monkeypatch.setattr(monitor, "_request", spy)
    monitor.check_chat()
    assert captured["payload"] == {"query": "", "history": []}


def test_chat_probe_502(monkeypatch):
    monkeypatch.setattr(monitor, "_request", lambda *a, **k: (502, 10, b""))
    assert monitor.check_chat()[0] == 502


# ---- diagnose ---------------------------------------------------------------

def test_down_restarts():
    d = diagnose.classify("down", None)
    assert d["action"] == "restart_service"
    assert d["confidence"] > 0.9


def test_chat_502_restarts_low_confidence():
    d = diagnose.classify("awake", 502)
    assert d["action"] == "restart_service"
    assert d["confidence"] < 0.9


def test_chat_429_escalates():
    assert diagnose.classify("awake", 429)["action"] == "escalate_to_human"


def test_unexpected_chat_code_escalates():
    assert diagnose.classify("awake", 500)["action"] == "escalate_to_human"


def test_healthy_no_action():
    assert diagnose.classify("awake", 200)["action"] == "no_action"


def test_sleep_recovered_no_action():
    d = diagnose.classify("sleep_recovered", 200)
    assert d["action"] == "no_action"
    assert "cold start" in d["diagnosis"]


# ---- remediate --------------------------------------------------------------

def test_trigger_restart_builds_request(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-x")
    captured = {}

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["method"] = req.get_method()
        body = json.dumps({"id": "dep-1", "status": "created"}).encode()
        return mock.MagicMock(
            __enter__=mock.MagicMock(return_value=mock.Mock(
                read=lambda: body)),
            __exit__=mock.MagicMock(return_value=False))

    monkeypatch.setattr(remediate.urllib.request, "urlopen", fake_urlopen)
    assert remediate.trigger_restart() == "dep-1"
    assert captured["url"].endswith("/services/srv-x/deploys")
    assert captured["auth"] == "Bearer k"
    assert captured["method"] == "POST"


def test_trigger_restart_handles_nested_deploy_shape(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-x")
    body = json.dumps({"deploy": {"id": "dep-2",
                                  "status": "created"}}).encode()
    monkeypatch.setattr(
        remediate.urllib.request, "urlopen",
        mock.MagicMock(return_value=mock.MagicMock(
            __enter__=mock.MagicMock(return_value=mock.Mock(
                read=lambda: body)),
            __exit__=mock.MagicMock(return_value=False))))
    assert remediate.trigger_restart() == "dep-2"


def test_missing_key_raises_without_leaking(monkeypatch):
    monkeypatch.delenv("RENDER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="RENDER_API_KEY"):
        remediate.trigger_restart()


def test_missing_service_id_raises(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.delenv("RENDER_SERVICE_ID", raising=False)
    with pytest.raises(RuntimeError, match="RENDER_SERVICE_ID"):
        remediate.trigger_restart()


class _FakeMonitor:
    APP_URL = "https://fake"
    next = []

    @staticmethod
    def _request(url, payload=None, timeout=30):
        return _FakeMonitor.next.pop(0)


def _fake_urlopen(payloads):
    bodies = iter(json.dumps(p).encode() for p in payloads)

    def fake(req, timeout=30):
        return mock.MagicMock(
            __enter__=mock.MagicMock(return_value=mock.Mock(
                read=lambda: next(bodies))),
            __exit__=mock.MagicMock(return_value=False))
    return fake


def test_verify_restart_success(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-x")
    monkeypatch.setattr(remediate, "DEPLOY_POLL_SECS", 0)
    monkeypatch.setattr(remediate, "APP_POLL_SECS", 0)
    monkeypatch.setattr(remediate.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        remediate.urllib.request, "urlopen",
        _fake_urlopen([{"deploy": {"status": "build_in_progress"}},
                       {"deploy": {"status": "live"}}]))
    _FakeMonitor.next = [(200, 50, b"")]
    ok, msg = remediate.verify_restart("dep-1", _FakeMonitor)
    assert ok is True
    assert "healthy" in msg


def test_verify_restart_build_failed_is_honest(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-x")
    monkeypatch.setattr(remediate, "DEPLOY_POLL_SECS", 0)
    monkeypatch.setattr(remediate.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        remediate.urllib.request, "urlopen",
        _fake_urlopen([{"deploy": {"status": "build_failed"}}]))
    ok, msg = remediate.verify_restart("dep-1", _FakeMonitor)
    assert ok is False
    assert "build_failed" in msg


def test_verify_restart_app_never_returns_200(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_SERVICE_ID", "srv-x")
    monkeypatch.setattr(remediate, "DEPLOY_POLL_SECS", 0)
    monkeypatch.setattr(remediate, "APP_POLL_SECS", 0)
    monkeypatch.setattr(remediate, "APP_POLL_DEADLINE_SECS", 0)
    monkeypatch.setattr(remediate.time, "sleep", lambda s: None)
    monkeypatch.setattr(
        remediate.urllib.request, "urlopen",
        _fake_urlopen([{"deploy": {"status": "live"}}]))
    _FakeMonitor.next = [(503, 10, b"")]
    ok, msg = remediate.verify_restart("dep-1", _FakeMonitor)
    assert ok is False
    assert "200" in msg


# ---- run_cycle --------------------------------------------------------------

def test_run_cycle_healthy_prints_clear(capsys, monkeypatch):
    monkeypatch.setattr(wd_run.monitor, "check_server",
                        lambda *a, **k: ("awake", [(200, 10)]))
    monkeypatch.setattr(wd_run.monitor, "check_chat",
                        lambda *a, **k: (200, 100, "hi"))
    assert wd_run.run_cycle(auto=True) is False
    assert "all clear" in capsys.readouterr().out


def test_run_cycle_skips_chat_by_default(capsys, monkeypatch):
    # Default: no WATCHDOG_CHAT_PROBE set -> the paid /chat probe must NOT run.
    called = []

    def fail_if_called(*a, **k):
        called.append(True)
        return 200, 1, "x"

    monkeypatch.setattr(wd_run.monitor, "chat_probe_enabled",
                        lambda: False)
    monkeypatch.setattr(wd_run.monitor, "check_server",
                        lambda *a, **k: ("awake", [(200, 10)]))
    monkeypatch.setattr(wd_run.monitor, "check_chat", fail_if_called)
    assert wd_run.run_cycle(auto=True) is False
    assert called == []
    assert "probe off" in capsys.readouterr().out


def test_run_cycle_escalates_on_429(capsys, monkeypatch):
    monkeypatch.setattr(wd_run.monitor, "chat_probe_enabled", lambda: True)
    monkeypatch.setattr(wd_run.monitor, "check_server",
                        lambda *a, **k: ("awake", [(200, 10)]))
    monkeypatch.setattr(wd_run.monitor, "check_chat",
                        lambda *a, **k: (429, 100, ""))
    monkeypatch.setattr(wd_run.notify, "deliver", lambda **kw: {})
    assert wd_run.run_cycle(auto=True) is True
    assert "ESCALATED" in capsys.readouterr().out


def test_run_cycle_down_restarts_and_reports(capsys, monkeypatch):
    monkeypatch.setattr(wd_run.monitor, "check_server",
                        lambda *a, **k: ("down", [(503, 10)]))
    monkeypatch.setattr(wd_run.remediate, "trigger_restart",
                        lambda: "dep-9")
    monkeypatch.setattr(wd_run.remediate, "verify_restart",
                        lambda d, m: (True, "app healthy again"))
    monkeypatch.setattr(wd_run.notify, "deliver", lambda **kw: {})
    assert wd_run.run_cycle(auto=True) is True
    out = capsys.readouterr().out
    assert "HEALTHY" in out
    assert "restart" in out.lower()