"""Shared pytest fixtures. Every test is hermetic: real runtime files are
redirected to a temp dir, and no LLM credentials leak through so nothing
touches the network."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Environment keys that must never leak into a test (would hit the network
# or read a real .env key).
ENV_KEYS = (
    "TOKENROUTER_API_KEY", "OPENROUTER_API_KEY", "LLM_MODEL",
    "TOKENROUTER_BASE_URL", "SLACK_WEBHOOK_URL",
    "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_USERNAME",
    "EMAIL_PASSWORD", "EMAIL_TO", "EMAIL_FROM",
    "RENDER_API_KEY", "RENDER_SERVICE_ID", "RENDER_OWNER_ID",
    "RENDER_BACKEND_TRADING_SERVICE_ID",
    "RENDER_FRONTEND_TRADING_SERVICE_ID",
    "RENDER_BACKEND_PORTFOLIO_SERVICE_ID",
    "WATCHDOG_APP_URL", "WATCHDOG_FRONTEND_URL",
    "WATCHDOG_PORTFOLIO_BACKEND_URL",
    "CHECK_INTERVAL", "CHECK_TIMEOUT", "CHECK_STATE_FILE",
    "SCRIPT_FIX_DIR", "SCRIPT_DRY_RUN", "SCRIPT_PATCH_SOURCE",
    "ALLOW_SOURCE_PATCH_IN_CI", "GITHUB_ACTIONS", "CI",
    "WATCHDOG_CHAT_PROBE", "WATCHDOG_LOG_STATE_FILE", "WATCHDOG_SLEEP",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Strip all credential/notification env vars before every test."""
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Redirect every runtime artifact to a temp dir and return paths.

    Returns a dict with:
        root, logs, service, log_file, offset_file, state_file
    """
    logs = tmp_path / "logs"
    service = tmp_path / "service"
    logs.mkdir()
    service.mkdir()

    from agent import monitor, remediate

    monkeypatch.setattr(monitor, "LOG_FILE", str(logs / "app.log"))
    monkeypatch.setattr(monitor, "OFFSET_FILE", str(logs / ".offset"))
    monkeypatch.setattr(monitor, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(remediate, "STATE_FILE", str(service / "state.json"))
    monkeypatch.setattr(remediate, "BASE_DIR", str(tmp_path))

    return {
        "root": tmp_path,
        "logs": logs,
        "service": service,
        "log_file": logs / "app.log",
        "offset_file": logs / ".offset",
        "state_file": service / "state.json",
    }


def write(path, text):
    """Write text to a path (creates parent dirs)."""
    os.makedirs(os.path.dirname(str(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()
