"""DIAGNOSE LLM layer - config resolution and offline behavior.

Network calls are never attempted here: credentials are stripped by the
autouse `clean_env` fixture in conftest.py.
"""
from agent import diagnose


def test_no_key_means_offline(monkeypatch):
    monkeypatch.delenv("TOKENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    key, url, model = diagnose.llm_config()
    assert key is None
    assert url is None
    assert model == diagnose.DEFAULT_MODEL
    assert diagnose.llm_available() is False


def test_tokenrouter_key_preferred(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr-test-key")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    key, url, model = diagnose.llm_config()
    assert key == "tr-test-key"
    assert url == "https://api.tokenrouter.ai/v1/chat/completions"
    assert model == diagnose.DEFAULT_MODEL


def test_openrouter_fallback_used_when_no_tokenrouter(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")

    key, url, _ = diagnose.llm_config()
    assert key == "or-test-key"
    assert url == "https://openrouter.ai/api/v1/chat/completions"


def test_model_override_from_env(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-test-key")
    monkeypatch.setenv("LLM_MODEL", "meta-llama/llama-3.3-70b-instruct:free")

    _, _, model = diagnose.llm_config()
    assert model == "meta-llama/llama-3.3-70b-instruct:free"


def test_explanation_is_none_without_key():
    assert diagnose.llm_explanation(["ERROR x"], {"diagnosis": "d"}) is None
