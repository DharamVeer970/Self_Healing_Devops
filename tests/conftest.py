"""Shared pytest configuration for the Self-Healing DevOps Agent tests."""
import os
import sys

import pytest

# Allow tests to import project modules regardless of pytest invocation dir.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove any real LLM keys so tests never touch the network.

    agent/__init__.py loads the developer's .env into os.environ at import
    time; strip those variables before every test for deterministic results.
    """
    for var in ("TOKENROUTER_API_KEY", "OPENROUTER_API_KEY", "LLM_MODEL",
                "TOKENROUTER_BASE_URL"):
        monkeypatch.delenv(var, raising=False)
    yield


@pytest.fixture()
def fake_world(tmp_path, monkeypatch):
    """Redirect every runtime artifact to a temp directory.

    Returns a namespace-ish dict with the paths each test can write to.
    """
    logs = tmp_path / "logs"
    demo = tmp_path / "demo_env"
    logs.mkdir()
    demo.mkdir()

    from agent import monitor, remediate

    monkeypatch.setattr(monitor, "LOG_FILE", str(logs / "app.log"))
    monkeypatch.setattr(monitor, "OFFSET_FILE", str(logs / ".offset"))
    monkeypatch.setattr(monitor, "BASE_DIR", str(tmp_path))
    monkeypatch.setattr(remediate, "STATE_FILE", str(demo / "state.json"))
    monkeypatch.setattr(remediate, "BASE_DIR", str(tmp_path))

    return {
        "root": tmp_path,
        "logs": logs,
        "demo": demo,
        "log_file": logs / "app.log",
        "offset_file": logs / ".offset",
        "state_file": demo / "state.json",
    }
