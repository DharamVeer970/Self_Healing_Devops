"""NOTIFY NODE - Slack/email delivery degrades gracefully, never raises."""
import json
from unittest import mock

from agent import notify


def test_nothing_configured_means_no_delivery(monkeypatch):
    for var in ("SLACK_WEBHOOK_URL", "EMAIL_SMTP_HOST", "EMAIL_USERNAME",
                "EMAIL_PASSWORD", "EMAIL_TO"):
        monkeypatch.delenv(var, raising=False)
    assert notify.channels() == []
    assert notify.deliver("t", "b") == {}


def test_slack_only_configuration(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    assert notify.channels() == ["slack"]
    assert notify.email_config() is None

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"ok"

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        return FakeResp()

    with mock.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
        result = notify.deliver("title", "body")

    assert result == {"slack": "sent"}
    assert captured["url"] == "https://hooks.example/x"
    assert "body" in captured["payload"]["text"]
    assert "title" in captured["payload"]["text"]


def test_email_requires_all_fields(monkeypatch):
    base = {"EMAIL_SMTP_HOST": "smtp.test.io", "EMAIL_USERNAME": "u",
            "EMAIL_PASSWORD": "p", "EMAIL_TO": "a@x.io,b@x.io"}
    for missing in base:
        partial = {k: v for k, v in base.items() if k != missing}
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "")
        for k, v in partial.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv(missing, raising=False)
        assert notify.email_config() is None


def test_email_sends_via_smtp_and_splits_recipients(monkeypatch):
    monkeypatch.setenv("EMAIL_SMTP_HOST", "smtp.test.io")
    monkeypatch.setenv("EMAIL_SMTP_PORT", "587")
    monkeypatch.setenv("EMAIL_USERNAME", "ops@x.io")
    monkeypatch.setenv("EMAIL_PASSWORD", "secret")
    monkeypatch.setenv("EMAIL_TO", " a@x.io , b@x.io ")

    cfg = notify.email_config()
    assert cfg["to"] == ["a@x.io", "b@x.io"]
    assert cfg["port"] == 587

    with mock.patch.object(notify.smtplib, "SMTP") as smtp_cls:
        instance = smtp_cls.return_value.__enter__.return_value
        assert notify.deliver("t", "b") == {"email": "sent"}
        smtp_cls.assert_called_once_with("smtp.test.io", 587, timeout=15)
        instance.starttls.assert_called_once()
        instance.login.assert_called_once_with("ops@x.io", "secret")
        instance.send_message.assert_called_once()


def test_network_failure_never_raises(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.example/x")
    with mock.patch.object(
            notify.urllib.request, "urlopen",
            mock.Mock(side_effect=OSError("boom"))):
        result = notify.deliver("t", "b")
    assert "failed" in result["slack"]
