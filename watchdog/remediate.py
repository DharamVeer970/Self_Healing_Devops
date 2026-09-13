"""REMOTE REMEDIATE NODE - heals the service through the Render API.

On Render, "restart the service" means "trigger a fresh deploy of the
latest commit". The API key comes ONLY from the environment
(RENDER_API_KEY) and is never logged or embedded in errors.
"""

import json
import os
import time
import urllib.request

API_BASE = "https://api.render.com/v1"
DEPLOY_POLL_SECS = 15
DEPLOY_TIMEOUT_SECS = 360               # free-tier builds can be slow
APP_POLL_SECS = 10
APP_POLL_DEADLINE_SECS = 120
DEPLOY_FAILURE_STATES = ("deactivated", "canceled", "build_failed",
                         "pre_deploy_failed")


def _headers():
    key = os.environ.get("RENDER_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "RENDER_API_KEY is not set - put it in .env (never in code)")
    return {"Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json"}


def service_id():
    sid = os.environ.get("RENDER_SERVICE_ID", "").strip()
    if not sid:
        raise RuntimeError(
            "RENDER_SERVICE_ID is not set - put it in .env")
    return sid


def _deploy(deploy_id):
    """Fetch one deploy object from the Render API."""
    url = f"{API_BASE}/services/{service_id()}/deploys/{deploy_id}"
    req = urllib.request.Request(url, headers=_headers())
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("deploy", data) if isinstance(data, dict) else {}


def trigger_restart():
    """Trigger a fresh deploy (= restart). Returns the new deploy id."""
    headers = _headers()                    # validate the key first
    url = f"{API_BASE}/services/{service_id()}/deploys"
    body = json.dumps({"clearCache": "do_not_clear"}).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers,
                                 method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    deploy = data.get("deploy", data) if isinstance(data, dict) else {}
    return str(deploy.get("id", ""))


def verify_restart(deploy_id, monitor, deadline=DEPLOY_TIMEOUT_SECS):
    """Wait until the deploy is live AND the app answers 200 again.

    `monitor` is injected (watchdog.monitor) so tests can fake probes.
    Returns (ok: bool, message: str) - honest in both directions.
    """
    waited = 0
    status = ""
    while waited < deadline:
        time.sleep(DEPLOY_POLL_SECS)
        waited += DEPLOY_POLL_SECS
        try:
            status = str(_deploy(deploy_id).get("status", ""))
        except Exception as exc:  # NOSONAR - broad catch intentional: must report honestly, never crash watchdog
            return False, f"deploy status check failed: {exc}"
        if status == "live":
            break
        if status in DEPLOY_FAILURE_STATES:
            return False, f"deploy ended with status '{status}'"
    else:
        return False, f"deploy still not live after {deadline}s"

    waited = 0
    while waited < APP_POLL_DEADLINE_SECS:
        code, ms, _ = monitor._request(f"{monitor.APP_URL}/")
        if code == 200:
            return True, f"app healthy again (200 in {ms}ms)"
        time.sleep(APP_POLL_SECS)
        waited += APP_POLL_SECS
    return False, "deploy is live but the app never returned 200"