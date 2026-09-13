"""Notify node: config resolution + self-healing delivery (never raises)."""
import json
from unittest import mock

import pytest

from agent import notify




def _set_email(monkeypatch, **over):
    base = {"EMAIL_SMTP_HOST": "smtp.x.io", "EMAIL_USERNAME": "u",
            "EMAIL_PASSWORD": "p", "EMAIL_TO": "a@x.io",
            "EMAIL_SMTP_PORT": "587", "EMAIL_FROM": "from@x.io"}
    base.update(over)
    for k in base:
        monkeypatch.setenv(k, base[k])


# ---- slack_webhook_url -----------------------------------------------------

def test_slack_url_unset_is_none(monkeypatch):
    assert notify.slack_webhook_url() is None


def test_slack_url_empty_string_is_none(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "   ")
    assert notify.slack_webhook_url() is None


def test_slack_url_returned_stripped(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "  https://hooks.example/x  ")
    assert notify.slack_webhook_url() == "https://hooks.example/x"


# ---- email_config ----------------------------------------------------------

REQUIRED = ("EMAIL_SMTP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD", "EMAIL_TO")


def test_email_none_when_any_required_missing(monkeypatch):
    for missing in REQUIRED:
        _set_email(monkeypatch, **{missing: ""})
        assert notify.email_config() is None


def test_email_config_defaults_port_and_from(monkeypatch):
    _set_email(monkeypatch, EMAIL_SMTP_PORT="", EMAIL_FROM="")
    cfg = notify.email_config()
    assert cfg["port"] == 587
    assert cfg["from"] == "u"                       # defaults to username
    assert cfg["host"] == "smtp.x.io"


def test_email_config_custom_port(monkeypatch):
    _set_email(monkeypatch, EMAIL_SMTP_PORT="465")
    assert notify.email_config()["port"] == 465


def test_email_config_splits_and_trims_recipients(monkeypatch):
    _set_email(monkeypatch, EMAIL_TO=" a@x.io , b@x.io ,,  c@x.io ")
    assert notify.email_config()["to"] == ["a@x.io", "b@x.io", "c@x.io"]


def test_email_config_invalid_port_raises(monkeypatch):
    _set_email(monkeypatch, EMAIL_SMTP_PORT="not-a-number")
    with pytest.raises(ValueError):
        notify.email_config()


# ---- channels --------------------------------------------------------------

def test_channels_none(monkeypatch):
    assert notify.channels() == []


def test_channels_slack_only(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://x")
    assert notify.channels() == ["slack"]


def test_channels_email_only(monkeypatch):
    _set_email(monkeypatch)
    assert notify.channels() == ["email"]


def test_channels_both(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://x")
    _set_email(monkeypatch)
    assert sorted(notify.channels()) == ["email", "slack"]


# ---- deliver ----------------------------------------------------------------

def test_deliver_nothing_configured(monkeypatch):
    assert notify.deliver("t", "b") == {}


def test_deliver_slack_sent(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    captured = {}

    class SpyResp:
        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return mock.MagicMock(
            __enter__=mock.MagicMock(return_value=SpyResp()),
            __exit__=mock.MagicMock(return_value=False))

    with mock.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
        result = notify.deliver("Incident", "body text")
    assert result == {"slack": "sent"}
    assert captured["url"] == "https://hooks.example/x"
    assert "Incident" in captured["payload"]["text"]
    assert "body text" in captured["payload"]["text"]


def test_deliver_slack_failure_degrades(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    with mock.patch.object(notify.urllib.request, "urlopen",
                           mock.Mock(side_effect=OSError("boom"))):
        result = notify.deliver("t", "b")
    assert "failed" in result["slack"]


def test_deliver_email_sent(monkeypatch):
    _set_email(monkeypatch, EMAIL_TO="a@x.io")
    with mock.patch.object(notify.smtplib, "SMTP") as smtp_cls:
        instance = smtp_cls.return_value.__enter__.return_value
        result = notify.deliver("Title", "Body")
    assert result == {"email": "sent"}
    smtp_cls.assert_called_once_with("smtp.x.io", 587, timeout=15)
    instance.starttls.assert_called_once()
    instance.login.assert_called_once_with("u", "p")
    instance.send_message.assert_called_once()


def test_deliver_email_failure_degrades(monkeypatch):
    _set_email(monkeypatch)
    with mock.patch.object(notify.smtplib, "SMTP",
                           mock.Mock(side_effect=ConnectionRefusedError())):
        result = notify.deliver("t", "b")
    assert "failed" in result["email"]


def test_deliver_both_channels(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://x")
    _set_email(monkeypatch)
    with mock.patch.object(
            notify.urllib.request, "urlopen",
            mock.Mock(side_effect=OSError("net"))), \
            mock.patch.object(notify.smtplib, "SMTP",
                              mock.Mock(side_effect=OSError("smtp"))):
        result = notify.deliver("t", "b")
    assert "failed" in result["slack"]
    assert "failed" in result["email"]


def test_deliver_one_channel_failure_keeps_other_result(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://x")
    _set_email(monkeypatch)
    with mock.patch.object(notify.urllib.request, "urlopen",
                           mock.Mock(side_effect=OSError("net"))), \
            mock.patch.object(notify.smtplib, "SMTP") as smtp_cls:
        result = notify.deliver("t", "b")
    assert "failed" in result["slack"]
    assert result["email"] == "sent"

