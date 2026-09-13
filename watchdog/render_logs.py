"""
REMOTE LOG MONITOR - reads the ACTUAL logs of your Render services.

This is what turns "is the URL up?" into "what is the service really
logging?". It uses the Render REST API (docs.render.com/api):

    GET /v1/logs?ownerId=<workspace_id>&resource=<service_id>
                 &direction=backward&limit=<n>

The workspace id (ownerId) is resolved automatically from the services
list on first use, or set explicitly via RENDER_OWNER_ID.

Everything degrades gracefully: no key / bad key / network error means
"no findings", never a crash - the watchdog loop keeps running.
"""
import json
import os
import urllib.request

API_BASE = "https://api.render.com/v1"
LOG_LIMIT = 200

# The 3 monitored services: (label, id env var, url env var).
SERVICE_PAIRS = (
    ("trading app",
     "RENDER_BACKEND_TRADING_SERVICE_ID", "WATCHDOG_APP_URL"),
    ("frontend",
     "RENDER_FRONTEND_TRADING_SERVICE_ID", "WATCHDOG_FRONTEND_URL"),
    ("portfolio api",
     "RENDER_BACKEND_PORTFOLIO_SERVICE_ID", "WATCHDOG_PORTFOLIO_BACKEND_URL"),
)

# Substrings that mark a log row as an error worth a human's attention.
ERROR_MARKERS = ("error", "fatal", "critical", "traceback",
                 "exception", "unhandled", " crash ")

_owner_cache = None
_service_map_cache = None


# ---------------------------------------------------------------------------
# Low-level Render API
# ---------------------------------------------------------------------------

def _api_key():
    return os.environ.get("RENDER_API_KEY", "").strip()


def _request(url):
    """GET with the Render bearer token. Returns (status, body_bytes|None)."""
    key = _api_key()
    if not key:
        return None, None
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Bearer {key}",
                 "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except Exception:
        return None, None


def owner_id():
    """The workspace / owner id needed by /logs.

    Uses RENDER_OWNER_ID if set, otherwise discovers it once from the
    services list. Returns "" when it cannot be resolved.
    """
    global _owner_cache
    if _owner_cache:
        return _owner_cache
    explicit = os.environ.get("RENDER_OWNER_ID", "").strip()
    if explicit:
        _owner_cache = explicit
        return explicit
    code, body = _request(f"{API_BASE}/services?limit=1")
    if code == 200 and body:
        try:
            items = json.loads(body.decode("utf-8"))
            svc = items[0]["service"] if isinstance(items, list) and items else {}
            oid = svc.get("ownerId") or (svc.get("owner") or {}).get("id")
            if oid:
                _owner_cache = oid
                return oid
        except Exception:
            pass
    return ""


def discover_services():
    """Auto-discover {url: service_id} for the whole workspace.

    Calls GET /v1/services once and maps each service's public URL to its
    id. This is what removes the need to hand-copy service IDs: with only
    RENDER_API_KEY set, the three monitored URLs are matched to their
    Render services automatically.

    Returns {} on any failure - callers then fall back to env vars.
    """
    global _service_map_cache
    if _service_map_cache is not None:
        return _service_map_cache
    _service_map_cache = {}
    code, body = _request(f"{API_BASE}/services?limit=100")
    if code == 200 and body:
        try:
            items = json.loads(body.decode("utf-8"))
            for item in items or []:
                svc = item.get("service", {}) if isinstance(item, dict) else {}
                url = ((svc.get("serviceDetails") or {}).get("url") or "").strip()
                sid = (svc.get("id") or "").strip()
                if url and sid:
                    _service_map_cache[url.rstrip("/")] = sid
        except Exception:                     # noqa: BLE001 - degrade
            _service_map_cache = {}
    return _service_map_cache


def configured_services():
    """Yield (label, service_id, url) for every service with id+URL set.

    Service id resolution order:
      1. explicit env var (RENDER_*_SERVICE_ID) - always wins
      2. auto-discovery via the Render API (URL -> id match)
    """
    discovered = discover_services()
    for label, sid_env, url_env in SERVICE_PAIRS:
        sid = os.environ.get(sid_env, "").strip()
        url = os.environ.get(url_env, "").strip()
        if url and not sid:
            sid = discovered.get(url.rstrip("/"), "")
        if sid and url:
            yield label, sid, url


# ---------------------------------------------------------------------------
# Log fetching + filtering
# ---------------------------------------------------------------------------

def fetch_logs(service_id, limit=LOG_LIMIT):
    """Return the `limit` most-recent log rows for a service.

    Each row is a dict (usually with 'message' and 'timestamp'). Returns
    [] on any failure - the watchdog degrades, never crashes.
    """
    oid = owner_id()
    if not oid or not service_id:
        return []
    url = (f"{API_BASE}/logs?ownerId={oid}&resource={service_id}"
           f"&direction=backward&limit={int(limit)}")
    code, body = _request(url)
    if code != 200 or not body:
        return []
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return []
    rows = data.get("logs") if isinstance(data, dict) else None
    return rows if isinstance(rows, list) else []


def text_of(row):
    """Best-effort human-readable text for one log row."""
    if not isinstance(row, dict):
        return str(row)
    for key in ("message", "text", "log", "content", "msg"):
        val = row.get(key)
        if val is not None:
            return str(val)
    return json.dumps(row, default=str)


def error_rows(rows):
    """Return the rows whose text looks like a real error."""
    out = []
    for row in rows or []:
        low = text_of(row).lower()
        if any(marker in low for marker in ERROR_MARKERS):
            out.append(row)
    return out


def scan_all():
    """Scan every configured service's logs for ERROR/FATAL rows.

    Returns a list of dicts:
        {"label", "service_id", "url", "errors": [rows]}

    Services whose logs could not be fetched are simply skipped.
    """
    findings = []
    for label, sid, url in configured_services():
        rows = fetch_logs(sid)
        if not rows:
            continue
        errors = error_rows(rows)
        if errors:
            findings.append({"label": label, "service_id": sid,
                             "url": url, "errors": errors})
    return findings