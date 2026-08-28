"""REMOTE MONITOR NODE - watches the portfolio backend over HTTP.

A Render free-tier service has three states, and confusing them causes
false alarms, so each probe round classifies explicitly:

    awake           200 on the first probe
    sleep_recovered an early probe failed (503/timeout) but a later one
                    returned 200 -> that was just a cold start, normal
    down            every probe failed -> real crash

The server liveness probe (GET /) is free. The chat probe (POST /chat)
invoices the chat + reranker providers, so it is OFF unless the operator
explicitly opts in via WATCHDOG_CHAT_PROBE=1.
"""

import json
import os
import time
import urllib.error
import urllib.request

APP_URL = os.environ.get("WATCHDOG_APP_URL", "https://dharam-portfolio-api.onrender.com")
PING_DELAYS = (0, 20, 45)            # seconds to wait BEFORE each probe
CHAT_TIMEOUT = 60                    # cold RAG replies can be slow


def chat_probe_enabled():
    """Whether the (paid) /chat probe should run this cycle."""
    raw = os.environ.get("WATCHDOG_CHAT_PROBE", "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _request(url, payload=None, timeout=30):
    """One HTTP probe. Returns (status_code or None, elapsed_ms, body bytes)."""
    start = time.monotonic()
    try:
        data = (json.dumps(payload).encode("utf-8")
                if payload is not None else None)
        headers = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(url, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            code = resp.status
        return code, int((time.monotonic() - start) * 1000), body
    except urllib.error.HTTPError as exc:      # 4xx/5xx with a real response
        try:
            body = exc.read()
        except Exception:                      # noqa: BLE001 - best effort
            body = b""
        return exc.code, int((time.monotonic() - start) * 1000), body
    except Exception:                          # timeout / DNS / refused
        return None, int((time.monotonic() - start) * 1000), b""


def check_server(delays=PING_DELAYS):
    """Probe GET / until it answers 200 (max len(delays) rounds).

    Returns (status, attempts) where status is 'awake', 'sleep_recovered'
    or 'down', and attempts is a list of (code, ms) per probe.
    """
    attempts = []
    for i, delay in enumerate(delays):
        if delay:
            time.sleep(delay)
        code, ms, _ = _request(f"{APP_URL}/")
        attempts.append((code, ms))
        if code == 200:
            return ("awake" if i == 0 else "sleep_recovered"), attempts
    return "down", attempts


def check_chat():
    """Probe POST /chat with an empty test query (opt-in, billed tokens).

    Returns (code or None, elapsed_ms, reply_snippet).
    """
    code, ms, body = _request(
        f"{APP_URL}/chat",
        payload={"query": "", "history": []},
        timeout=CHAT_TIMEOUT,
    )
    snippet = ""
    if code == 200:
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception:                      # noqa: BLE001 - non-fatal
            data = None
        if isinstance(data, dict):
            for key in ("reply", "answer", "response", "message", "text"):
                if data.get(key):
                    snippet = str(data[key])[:80]
                    break
    return code, ms, snippet