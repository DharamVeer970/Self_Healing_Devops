"""Diagnose node: LLM config resolution and explanation (no network)."""
import json
from unittest import mock

from agent import diagnose



DISK = "ERROR OSError: No space left on device"


def _urlopen_ok(content="Root cause found."):
    """Return a urlopen stand-in that produces a valid completion."""
    def fake(req, timeout=30):
        body = json.dumps({"choices": [{"message": {"content": content}}]})
        return mock.MagicMock(
            __enter__=mock.MagicMock(return_value=mock.Mock(
                read=lambda: body.encode())),
            __exit__=mock.MagicMock(return_value=False))
    return fake


# ---- llm_config ------------------------------------------------------------

def test_no_keys_offline():
    key, url, model = diagnose.llm_config()
    assert key is None
    assert url is None
    assert model == diagnose.DEFAULT_MODEL


def test_default_model():
    assert diagnose.DEFAULT_MODEL == "openai/gpt-4o-mini"


def test_tokenrouter_preferred_over_openrouter(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr")
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    key, url, _ = diagnose.llm_config()
    assert key == "tr"
    assert url == "https://api.tokenrouter.ai/v1/chat/completions"


def test_openrouter_fallback(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    key, url, _ = diagnose.llm_config()
    assert key == "or"
    assert url == "https://openrouter.ai/api/v1/chat/completions"


def test_base_url_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr")
    monkeypatch.setenv("TOKENROUTER_BASE_URL", "https://api.example/v1/")
    _, url, _ = diagnose.llm_config()
    assert url == "https://api.example/v1/chat/completions"


def test_llm_model_override_via_env(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "tr")
    monkeypatch.setenv("LLM_MODEL", "custom/model")
    _, _, model = diagnose.llm_config()
    assert model == "custom/model"


def test_model_used_even_without_key(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "foo/bar")
    key, url, model = diagnose.llm_config()
    assert key is None
    assert url is None
    assert model == "foo/bar"


# ---- llm_available ---------------------------------------------------------

def test_llm_available_false_offline():
    assert diagnose.llm_available() is False


def test_llm_available_true_with_tokenrouter(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "x")
    assert diagnose.llm_available() is True


# ---- llm_explanation -------------------------------------------------------

def test_no_key_returns_none():
    assert diagnose.llm_explanation([DISK], {"diagnosis": "d"}) is None


def test_network_error_returns_unavailable_message(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "k")
    with mock.patch.object(
            diagnose.urllib.request, "urlopen",
            mock.Mock(side_effect=OSError("timeout"))):
        out = diagnose.llm_explanation([DISK], {"diagnosis": "d",
                                                 "action": "a"})
    assert out.startswith("(LLM explanation unavailable")


def test_success_parses_and_trims_response(monkeypatch):
    monkeypatch.setenv("TOKENROUTER_API_KEY", "k")
    captured = {}

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["auth"] = req.get_header("Authorization")
        captured["body"] = json.loads(req.data.decode("utf-8"))
        body = json.dumps({"choices": [{"message": {"content": "  Root cause.  "}}]})
        return mock.MagicMock(
            __enter__=mock.MagicMock(return_value=mock.Mock(
                read=lambda: body.encode())),
            __exit__=mock.MagicMock(return_value=False))

    with mock.patch.object(diagnose.urllib.request, "urlopen", fake_urlopen):
        out = diagnose.llm_explanation([DISK], {"diagnosis": "D",
                                                 "action": "A"})
    assert out == "Root cause."
    assert captured["url"].endswith("/chat/completions")
    assert captured["auth"] == "Bearer k"
    assert captured["body"]["model"] == diagnose.DEFAULT_MODEL
    assert captured["body"]["max_tokens"] == 300
    assert "D" in captured["body"]["messages"][1]["content"]


def test_openrouter_adds_attribution_headers(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    captured = {}

    def fake_urlopen(req, timeout=30):
        # Request stores headers capitalized ("HTTP-Referer" -> "Http-referer"),
        # so inspect them case-insensitively.
        headers = {k.lower(): v for k, v in req.header_items()}
        captured["ref"] = headers.get("http-referer")
        captured["title"] = headers.get("x-title")
        return _urlopen_ok()

    with mock.patch.object(diagnose.urllib.request, "urlopen", fake_urlopen):
        diagnose.llm_explanation([DISK], {"diagnosis": "d", "action": "a"})
    assert captured["ref"]
    assert captured["title"]


def test_prompt_only_uses_last_10_error_lines(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    many = [f"ERROR line {i}" for i in range(50)]
    captured = {}

    def fake_urlopen(req, timeout=30):
        captured["content"] = json.loads(req.data.decode(
            "utf-8"))["messages"][1]["content"]
        return _urlopen_ok()

    with mock.patch.object(diagnose.urllib.request, "urlopen", fake_urlopen):
        diagnose.llm_explanation(many, {"diagnosis": "d", "action": "a"})
    assert "ERROR line 40" in captured["content"]
    assert "ERROR line 0" not in captured["content"]   # beyond last 10


def test_malformed_response_degrades_gracefully(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or")
    with mock.patch.object(diagnose.urllib.request, "urlopen",
                           mock.Mock(side_effect=ValueError("bad json"))):
        out = diagnose.llm_explanation([DISK], {"diagnosis": "d",
                                                 "action": "a"})
    assert out.startswith("(LLM explanation unavailable")