"""
NOTIFY NODE - pushes incident reports to humans (Slack / email).

Credentials come ONLY from environment variables or the project .env
(auto-loaded by agent/__init__.py) - never hardcoded:

    SLACK_WEBHOOK_URL   https://hooks.slack.com/services/... (1:1 webhook)

    EMAIL_SMTP_HOST     e.g. smtp.gmail.com
    EMAIL_SMTP_PORT     e.g. 587
    EMAIL_USERNAME      SMTP login user
    EMAIL_PASSWORD      SMTP password / app password
    EMAIL_TO            comma-separated recipients
    EMAIL_FROM          optional sender (defaults to EMAIL_USERNAME)

Nothing configured? Delivery is silently skipped. Network problems NEVER
crash the agent loop - every failure degrades to a returned message string.
"""

import json
import os
import smtplib
import urllib.request
from email.mime.text import MIMEText


def slack_webhook_url():
    return os.environ.get("SLACK_WEBHOOK_URL", "").strip() or None


def email_config():
    """Return an SMTP settings dict, or None when email is not configured."""
    required = ("EMAIL_SMTP_HOST", "EMAIL_USERNAME", "EMAIL_PASSWORD",
                "EMAIL_TO")
    values = {k: os.environ.get(k, "").strip() for k in required}
    if not all(values.values()):
        return None
    port_str = os.environ.get("EMAIL_SMTP_PORT", "587").strip()
    port = int(port_str) if port_str else 587
    return {
        "host": values["EMAIL_SMTP_HOST"],
        "port": port,
        "username": values["EMAIL_USERNAME"],
        "password": values["EMAIL_PASSWORD"],
        "to": [addr.strip() for addr in values["EMAIL_TO"].split(",")
               if addr.strip()],
        "from": os.environ.get("EMAIL_FROM",
                               "").strip() or values["EMAIL_USERNAME"],
    }


def channels():
    """Names of the delivery channels currently configured."""
    configured = []
    if slack_webhook_url():
        configured.append("slack")
    if email_config():
        configured.append("email")
    return configured


def _post_slack(url, title, body):
    payload = json.dumps({"text": f"*{title}*\n```{body}```"}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()
    return "sent"


def _post_email(cfg, title, body):
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = cfg["from"]
    msg["To"] = ", ".join(cfg["to"])
    with smtplib.SMTP(cfg["host"], cfg["port"], timeout=15) as smtp:
        smtp.starttls()
        smtp.login(cfg["username"], cfg["password"])
        smtp.send_message(msg)
    return "sent"


def deliver(title, body):
    """
    Send the report through every configured channel.

    Returns {"slack": "sent"|"...", "email": "sent"| "..."} for the channels
    that were configured. Exceptions become descriptive strings - never raised.
    """
    results = {}
    url = slack_webhook_url()
    if url:
        try:
            results["slack"] = _post_slack(url, title, body)
        except Exception as exc:                     # network / bad URL
            results["slack"] = f"failed ({exc})"

    cfg = email_config()
    if cfg:
        try:
            results["email"] = _post_email(cfg, title, body)
        except Exception as exc:                     # auth / network
            results["email"] = f"failed ({exc})"
    return results
