"""URL Connectivity Checker: state-change detection, probe, persistence."""
import json
import os
from unittest import mock

import pytest

from agent import url_checker


# ---- urls / config ----------------------------------------------------------



def test_urls_empty_when_unset(monkeypatch):
    assert url_checker.urls() == []


def test_urls_reads_watchdog_vars(monkeypatch):
    """The 3 WATCHDOG_* vars are the single source of truth."""
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://a.onrender.com")
    monkeypatch.setenv("WATCHDOG_FRONTEND_URL", "https://b.onrender.com")
    assert url_checker.urls() == [
        "https://a.onrender.com",
        "https://b.onrender.com",
    ]


def test_urls_dedupes(monkeypatch):
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://same.onrender.com")
    monkeypatch.setenv("WATCHDOG_FRONTEND_URL", "https://same.onrender.com")
    assert url_checker.urls() == ["https://same.onrender.com"]


def test_urls_reads_your_real_render_urls(monkeypatch):
    """Your actual Render WATCHDOG_* URLs are checked."""
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://trading-agent-v6kg.onrender.com")
    monkeypatch.setenv("WATCHDOG_FRONTEND_URL", "https://trading-agent-1-4mha.onrender.com")
    monkeypatch.setenv("WATCHDOG_PORTFOLIO_BACKEND_URL", "https://dharam-portfolio-api.onrender.com")
    assert url_checker.urls() == [
        "https://trading-agent-v6kg.onrender.com",
        "https://trading-agent-1-4mha.onrender.com",
        "https://dharam-portfolio-api.onrender.com",
    ]


def test_interval_default():
    assert url_checker.interval() == 120


def test_interval_override(monkeypatch):
    monkeypatch.setenv("CHECK_INTERVAL", "30")
    assert url_checker.interval() == 30


def test_timeout_default():
    assert url_checker.timeout() == 10


# ---- probe ------------------------------------------------------------------

def test_probe_success(monkeypatch):
    resp = mock.Mock()
    resp.status = 200
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    monkeypatch.setattr(url_checker.urllib.request, "urlopen",
                        lambda *a, **k: resp)
    is_up, detail = url_checker.probe("https://example.com")
    assert is_up is True
    assert "200" in detail


def test_probe_http_error_is_still_reachable(monkeypatch):
    """A 500 response means the server IS up (just returning an error)."""
    import urllib.error
    err = urllib.error.HTTPError(
        url="https://example.com", code=500, msg="Internal Error",
        hdrs={}, fp=None)

    def raise_err(*a, **k):
        raise err

    monkeypatch.setattr(url_checker.urllib.request, "urlopen", raise_err)
    is_up, detail = url_checker.probe("https://example.com")
    assert is_up is True
    assert "500" in detail


def test_probe_connection_refused(monkeypatch):
    monkeypatch.setattr(
        url_checker.urllib.request, "urlopen",
        mock.Mock(side_effect=ConnectionRefusedError("refused")))
    is_up, detail = url_checker.probe("https://down.com")
    assert is_up is False
    assert "refused" in detail.lower()


def test_probe_timeout(monkeypatch):
    import urllib.error
    monkeypatch.setattr(
        url_checker.urllib.request, "urlopen",
        mock.Mock(side_effect=urllib.error.URLError("timed out")))
    is_up, detail = url_checker.probe("https://slow.com")
    assert is_up is False


# ---- state persistence ------------------------------------------------------

def test_state_missing_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(url_checker, "DEFAULT_STATE_FILE",
                        str(tmp_path / "nonexistent.json"))
    monkeypatch.setenv("CHECK_STATE_FILE", str(tmp_path / "nonexistent.json"))
    assert url_checker._load_state() == {}


def test_state_roundtrip(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    monkeypatch.setenv("CHECK_STATE_FILE", str(path))
    # Reload module-level reference
    monkeypatch.setattr(url_checker, "DEFAULT_STATE_FILE", str(path))
    state = {"https://a.com": "up", "https://b.com": "down"}
    url_checker._save_state(state)
    assert url_checker._load_state() == state


def test_state_corrupted_resets(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_text("not json{{{", encoding="utf-8")
    monkeypatch.setenv("CHECK_STATE_FILE", str(path))
    monkeypatch.setattr(url_checker, "DEFAULT_STATE_FILE", str(path))
    assert url_checker._load_state() == {}


# ---- check_all --------------------------------------------------------------

def test_check_all_no_urls(monkeypatch):
    for var in url_checker.WATCHDOG_URL_VARS:
        monkeypatch.delenv(var, raising=False)
    assert url_checker.check_all() == []


def test_check_all_reports_on_change(monkeypatch, tmp_path):
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://a.com")
    monkeypatch.setenv("WATCHDOG_FRONTEND_URL", "https://b.com")
    monkeypatch.setenv("CHECK_STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(url_checker, "DEFAULT_STATE_FILE",
                        str(tmp_path / "state.json"))

    # First call: both come up -> 2 events
    resp = mock.Mock()
    resp.status = 200
    resp.__enter__ = mock.Mock(return_value=resp)
    resp.__exit__ = mock.Mock(return_value=False)
    monkeypatch.setattr(url_checker.urllib.request, "urlopen",
                        lambda *a, **k: resp)
    events = url_checker.check_all()
    assert len(events) == 2
    assert all(e["from"] == "unknown" for e in events)
    assert all(e["to"] == "up" for e in events)

    # Second call: both still up -> 0 events
    events2 = url_checker.check_all()
    assert events2 == []

    # Third call: a.com goes down -> 1 event
    def selective_urlopen(req, *a, **k):
        if "a.com" in req.full_url:
            raise ConnectionRefusedError("down")
        return resp

    monkeypatch.setattr(url_checker.urllib.request, "urlopen",
                        selective_urlopen)
    events3 = url_checker.check_all()
    assert len(events3) == 1
    assert events3[0]["url"] == "https://a.com"
    assert events3[0]["from"] == "up"
    assert events3[0]["to"] == "down"


# ---- summary ----------------------------------------------------------------

def test_summary_no_state(monkeypatch, tmp_path):
    monkeypatch.setenv("CHECK_STATE_FILE", str(tmp_path / "x.json"))
    monkeypatch.setattr(url_checker, "DEFAULT_STATE_FILE",
                        str(tmp_path / "x.json"))
    assert "No URL state" in url_checker.summary()


def test_summary_shows_states(monkeypatch, tmp_path):
    path = tmp_path / "state.json"
    monkeypatch.setenv("CHECK_STATE_FILE", str(path))
    monkeypatch.setattr(url_checker, "DEFAULT_STATE_FILE", str(path))
    url_checker._save_state({"https://a.com": "up", "https://b.com": "down"})
    s = url_checker.summary()
    assert "UP" in s
    assert "DOWN" in s
    assert "a.com" in s
    assert "b.com" in s
