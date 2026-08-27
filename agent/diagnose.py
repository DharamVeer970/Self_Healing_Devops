"""
DIAGNOSE NODE - turns raw error lines into (diagnosis, recommended_action).

Two layers:
  1. Rule-based classifier (always available, deterministic, instant).
  2. OPTIONAL LLM enrichment for human-friendly root-cause reports.

LLM credentials come ONLY from environment variables (or the project .env,
which is auto-loaded by agent/__init__.py) - never hardcoded:
    TOKENROUTER_API_KEY   (preferred, if set)
    OPENROUTER_API_KEY    (fallback)
Both providers speak the OpenAI-compatible /chat/completions protocol.

Optional extras (all env, all optional):
    LLM_MODEL            model id      (default: openai/gpt-4o-mini)
    TOKENROUTER_BASE_URL override base (default: https://api.tokenrouter.ai/v1)

No keys? Everything still works offline via the rule engine.
"""

import json
import os
import re
import urllib.request

# ---------------------------------------------------------------------------
# 1. RULE-BASED DIAGNOSIS (the reliable core)
# ---------------------------------------------------------------------------
RULES = [
    {
        "pattern": re.compile(r"No space left on device", re.I),
        "diagnosis": "Disk is full - the app cannot write its log files.",
        "action": "rotate_logs",
        "confidence": 0.97,
    },
    {
        "pattern": re.compile(r"OutOfMemoryError|MemoryError", re.I),
        "diagnosis": "Memory exhausted by a leak; the service has crashed.",
        "action": "restart_service",
        "confidence": 0.93,
    },
    {
        "pattern": re.compile(r"502 Bad Gateway|upstream.*unavailable|Connection refused",
                              re.I),
        "diagnosis": "Reverse proxy is up but the backend app behind it is down.",
        "action": "restart_service",
        "confidence": 0.90,
    },
]

ESCALATE = {
    "diagnosis": "Unknown error - needs a human.",
    "action": "escalate_to_human",
    "confidence": 0.0,
}


def classify(error_lines):
    """Match the most recent severe error against known rules."""
    if not error_lines:
        return None
    for line in reversed(error_lines):                 # newest first
        for rule in RULES:
            if rule["pattern"].search(line):
                result = dict(rule)
                result["error_line"] = line.strip()
                return result
    return dict(ESCALATE, error_line=error_lines[-1].strip())


# ---------------------------------------------------------------------------
# 2. OPTIONAL LLM EXPLANATION - credentials ONLY via env vars / .env
# ---------------------------------------------------------------------------
DEFAULT_MODEL = "openai/gpt-4o-mini"


def llm_config():
    """
    Resolve provider settings purely from the environment.
    Returns (api_key, url, model) or (None, None, model) when no key present.
    """
    tokenrouter_key = os.environ.get("TOKENROUTER_API_KEY")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)

    if tokenrouter_key:
        base = os.environ.get(
            "TOKENROUTER_BASE_URL", "https://api.tokenrouter.ai/v1"
        ).rstrip("/")
        return tokenrouter_key, f"{base}/chat/completions", model

    if openrouter_key:
        # documented OpenRouter endpoint (OpenAI-compatible)
        return (openrouter_key,
                "https://openrouter.ai/api/v1/chat/completions", model)

    return None, None, model


def llm_available():
    """True when a key exists in the environment."""
    key, _, _ = llm_config()
    return key is not None


def llm_explanation(error_lines, diagnosis):
    """Return an LLM-written root-cause paragraph, or None (offline mode)."""
    api_key, url, model = llm_config()
    if not api_key:
        return None

    prompt = (
        "You are an SRE assistant. Here are recent server log errors:\n"
        + "\n".join(error_lines[-10:])
        + f"\n\nAutomated diagnosis: {diagnosis['diagnosis']} "
          f"(proposed fix: {diagnosis['action']}).\n"
        "In <=5 sentences explain the likely root cause and whether the "
        "proposed fix is safe."
    )

    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a concise SRE expert."},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 300,
        "temperature": 0.2,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if "openrouter.ai" in url:          # OpenRouter attribution headers
        headers["HTTP-Referer"] = "http://localhost:self-healing-agent"
        headers["X-Title"] = "Self-Healing DevOps Agent"

    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except Exception as exc:                     # network/key/model problems
        return f"(LLM explanation unavailable: {exc})"
