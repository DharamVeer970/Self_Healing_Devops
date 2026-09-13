"""watchdog.render_logs: remote Render log scanning + service auto-discovery.

All tests mock render_logs._request so nothing touches the network.
"""
import json

import pytest

from watchdog import render_logs
from watchdog import run as wd_run


@pytest.fixture(autouse=True)
def _fresh_caches(monkeypatch):
    """Reset module-level caches so tests don't leak into each other."""
    monkeypatch.setattr(render_logs, "_owner_cache", None)
    monkeypatch.setattr(render_logs, "_service_map_cache", None)


def _fake_request(pages):
    """Return a _request stub keyed by substring match on the URL."""
    def stub(url):
        for needle, result in pages.items():
            if needle in url:
                return result
        return None, None
    return stub


# ---- _api_key / _request ----------------------------------------------------

def test_no_api_key_means_no_request():
    assert render_logs._api_key() == ""
    assert render_logs._request("https://api.render.com/v1/services")[0] is None


# ---- owner_id ---------------------------------------------------------------

def test_owner_id_explicit_env_wins(monkeypatch):
    monkeypatch.setenv("RENDER_OWNER_ID", "usr-explicit")
    monkeypatch.setattr(render_logs, "_request",
                        lambda url: (200, b"should-not-be-called"))
    assert render_logs.owner_id() == "usr-explicit"


def test_owner_id_discovered_from_services(monkeypatch):
    svc = json.dumps([{"service": {"ownerId": "usr-auto"}}]).encode()
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", lambda url: (200, svc))
    assert render_logs.owner_id() == "usr-auto"
    assert render_logs._owner_cache == "usr-auto"          # cached


def test_owner_id_empty_when_unresolvable(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", lambda url: (401, None))
    assert render_logs.owner_id() == ""


# ---- discover_services: URL -> service id -----------------------------------

def test_discover_maps_url_to_service_id(monkeypatch):
    body = json.dumps([
        {"service": {"id": "srv-app",
                     "serviceDetails": {"url": "https://app.onrender.com"}}},
        {"service": {"id": "srv-fe",
                     "serviceDetails": {"url": "https://fe.onrender.com"}}},
    ]).encode()
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", lambda url: (200, body))
    mapping = render_logs.discover_services()
    assert mapping["https://app.onrender.com"] == "srv-app"
    assert mapping["https://fe.onrender.com"] == "srv-fe"


def test_discover_cached_second_call_no_extra_request(monkeypatch):
    calls = []

    def stub(url):
        calls.append(url)
        body = json.dumps([{"service": {
            "id": "srv-x",
            "serviceDetails": {"url": "https://x.onrender.com"}}}]).encode()
        return 200, body
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", stub)
    render_logs.discover_services()
    render_logs.discover_services()
    assert len(calls) == 1                                  # one API call only


def test_discover_failure_returns_empty(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", lambda url: (500, None))
    assert render_logs.discover_services() == {}



# ---- configured_services: env wins, discovery fills the gaps ----------------

SERVICES_BODY = json.dumps([
    {"service": {"id": "srv-auto",
                 "serviceDetails": {"url": "https://trading-agent-v6kg.onrender.com"}}},
]).encode()


def test_configured_explicit_env_id_beats_discovery(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", lambda url: (200, SERVICES_BODY))
    monkeypatch.setenv("RENDER_BACKEND_TRADING_SERVICE_ID", "srv-explicit")
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://trading-agent-v6kg.onrender.com")
    pairs = list(render_logs.configured_services())
    assert pairs == [("trading app", "srv-explicit",
                      "https://trading-agent-v6kg.onrender.com")]


def test_configured_auto_discovered_id(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setattr(render_logs, "_request", lambda url: (200, SERVICES_BODY))
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://trading-agent-v6kg.onrender.com")
    pairs = list(render_logs.configured_services())
    assert pairs == [("trading app", "srv-auto",
                      "https://trading-agent-v6kg.onrender.com")]


def test_configured_skips_services_without_id(monkeypatch):
    # No RENDER_API_KEY -> discovery impossible -> no id -> skipped.
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://trading-agent-v6kg.onrender.com")
    assert list(render_logs.configured_services()) == []


def test_configured_skips_services_without_url(monkeypatch):
    monkeypatch.setenv("RENDER_BACKEND_TRADING_SERVICE_ID", "srv-1")
    assert list(render_logs.configured_services()) == []


# ---- fetch_logs -------------------------------------------------------------

LOGS_BODY = json.dumps({"logs": [
    {"message": "INFO GET / 200", "timestamp": "2026-01-01T00:00:00Z"},
    {"message": "ERROR db connection refused", "timestamp": "2026-01-01T00:01:00Z"},
]}).encode()


def test_fetch_logs_parses_rows(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_OWNER_ID", "usr-1")
    monkeypatch.setattr(render_logs, "_request",
                        _fake_request({"/logs": (200, LOGS_BODY)}))
    rows = render_logs.fetch_logs("srv-1")
    assert len(rows) == 2
    assert "db connection refused" in render_logs.text_of(rows[1])


def test_fetch_logs_returns_empty_without_owner(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    assert render_logs.fetch_logs("srv-1") == []


def test_fetch_logs_degrades_on_api_error(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_OWNER_ID", "usr-1")
    monkeypatch.setattr(render_logs, "_request", lambda url: (500, None))
    assert render_logs.fetch_logs("srv-1") == []


# ---- text_of / error_rows ---------------------------------------------------

def test_text_of_prefers_message_field():
    assert render_logs.text_of({"message": "boom"}) == "boom"
    assert render_logs.text_of({"text": "fallback"}) == "fallback"
    assert render_logs.text_of("raw string") == "raw string"
    # Weird shape -> never crashes, returns some string.
    assert isinstance(render_logs.text_of({"weird": 1}), str)


def test_error_rows_match_markers():
    rows = [{"message": "all good"},
            {"message": "ERROR: unhandled exception"},
            {"message": "Traceback (most recent call last):"},
            {"message": "fatal: cannot continue"},
            {"message": "just a warning here, nothing failed"}]
    errors = render_logs.error_rows(rows)
    assert len(errors) == 3


def test_error_rows_empty_for_clean_logs():
    assert render_logs.error_rows([{"message": "ok"}]) == []
    assert render_logs.error_rows(None) == []


# ---- scan_all ---------------------------------------------------------------

def test_scan_all_returns_findings_only_for_erroring_services(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_OWNER_ID", "usr-1")
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://app.onrender.com")
    monkeypatch.setenv("RENDER_BACKEND_TRADING_SERVICE_ID", "srv-app")
    body = json.dumps({"logs": [{"message": "ERROR crash in handler"},
                                {"message": "INFO fine"}]}).encode()
    monkeypatch.setattr(render_logs, "_request",
                        _fake_request({"/logs": (200, body)}))
    findings = render_logs.scan_all()
    assert len(findings) == 1
    assert findings[0]["service_id"] == "srv-app"
    assert len(findings[0]["errors"]) == 1


def test_scan_all_skips_unfetchable_services(monkeypatch):
    monkeypatch.setenv("RENDER_API_KEY", "k")
    monkeypatch.setenv("RENDER_OWNER_ID", "usr-1")
    monkeypatch.setenv("WATCHDOG_APP_URL", "https://app.onrender.com")
    monkeypatch.setenv("RENDER_BACKEND_TRADING_SERVICE_ID", "srv-app")
    monkeypatch.setattr(render_logs, "_request", lambda url: (500, None))
    assert render_logs.scan_all() == []


# ---- dedupe in watchdog.run._log_incidents ----------------------------------

def _finding(msg):
    return [{"service_id": "srv-1", "label": "svc", "url": "https://x",
             "errors": [{"message": msg}]}]


def test_log_incidents_report_once_then_silence(tmp_path):
    state_file = str(tmp_path / "state.json")
    scan = _finding("ERROR boom")
    first = wd_run._log_incidents(log_scan=scan, state_file=state_file)
    assert len(first) == 1
    # Same error again -> deduped, not re-alarmed.
    second = wd_run._log_incidents(log_scan=scan, state_file=state_file)
    assert second == []
    # A NEW error line alarms again.
    third = wd_run._log_incidents(
        log_scan=_finding("ERROR different crash"), state_file=state_file)
    assert len(third) == 1


def test_log_incidents_empty_scan_returns_empty(tmp_path):
    assert wd_run._log_incidents(
        log_scan=[], state_file=str(tmp_path / "s.json")) == []
