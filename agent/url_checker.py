"""
URL CONNECTIVITY CHECKER - monitors 2-4 URLs for up/down state changes.

Reports ONLY on state transitions (up->down, down->up) to avoid noise.
State is persisted to a JSON file so restarts don't re-report the same status.

Env vars (all optional, loaded from .env by agent/__init__.py):
    WATCHDOG_APP_URL / WATCHDOG_FRONTEND_URL / WATCHDOG_PORTFOLIO_BACKEND_URL
                     the 3 Render services to monitor (single source of truth)
    CHECK_INTERVAL       seconds between check rounds (default: 120)
    CHECK_TIMEOUT        seconds before a probe times out (default: 10)
    CHECK_STATE_FILE     path to the state file (default: <repo>/logs/.url_state.json)
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_STATE_FILE = os.path.join(BASE_DIR, "logs", ".url_state.json")


# The 3 monitored Render services - single source of truth. No CHECK_URLS
# duplication: the url-checker and the watchdog both read these same vars.
WATCHDOG_URL_VARS = (
    "WATCHDOG_APP_URL",
    "WATCHDOG_FRONTEND_URL",
    "WATCHDOG_PORTFOLIO_BACKEND_URL",
)


def urls():
    """Return the list of URLs to monitor (from the WATCHDOG_* vars)."""
    found = []
    for var in WATCHDOG_URL_VARS:
        val = os.environ.get(var, "").strip()
        if val and val not in found:
            found.append(val)
    return found


def interval():
    """Seconds between check rounds."""
    return int(os.environ.get("CHECK_INTERVAL", "120"))


def timeout():
    """Seconds before a probe times out."""
    return int(os.environ.get("CHECK_TIMEOUT", "10"))


def state_file():
    return os.environ.get("CHECK_STATE_FILE", DEFAULT_STATE_FILE)


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _load_state():
    """Load the last-known state dict {url: "up"|"down", ...}."""
    path = state_file()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_state(state):
    path = state_file()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Probe
# ---------------------------------------------------------------------------

def probe(url):
    """Check one URL. Returns (is_up: bool, detail: str)."""
    start = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout()) as resp:
            code = resp.status
        ms = int((time.monotonic() - start) * 1000)
        if 200 <= code < 400:
            return True, f"HTTP {code} in {ms}ms"
        return False, f"HTTP {code} (non-2xx/3xx) in {ms}ms"
    except urllib.error.HTTPError as exc:
        ms = int((time.monotonic() - start) * 1000)
        # 4xx/5xx with a real response still means the server is reachable
        return True, f"HTTP {exc.code} (reachable) in {ms}ms"
    except Exception as exc:
        ms = int((time.monotonic() - start) * 1000)
        return False, f"{type(exc).__name__}: {exc} after {ms}ms"


# ---------------------------------------------------------------------------
# Check cycle - returns list of state-change events
# ---------------------------------------------------------------------------

def check_all():
    """
    Probe every configured URL and compare against the last-known state.

    Returns a list of dicts, one per state CHANGE:
        {"url": ..., "from": "up"|"down", "to": "up"|"down",
         "at": "HH:MM:SS", "detail": ...}

    If nothing changed, returns an empty list.
    """
    monitored = urls()
    if not monitored:
        return []

    previous = _load_state()
    events = []
    current = {}

    for url in monitored:
        is_up, detail = probe(url)
        new_state = "up" if is_up else "down"
        current[url] = new_state

        old_state = previous.get(url)
        if old_state != new_state:
            events.append({
                "url": url,
                "from": old_state or "unknown",
                "to": new_state,
                "at": datetime.now().strftime("%H:%M:%S"),
                "detail": detail,
            })

    _save_state(current)
    return events


def summary():
    """Return a human-friendly one-liner of every URL's current state."""
    state = _load_state()
    if not state:
        return "No URL state recorded yet."
    parts = []
    for url, status in state.items():
        icon = "UP" if status == "up" else "DOWN"
        parts.append(f"  {icon}  {url}")
    return "URL status:\n" + "\n".join(parts)
