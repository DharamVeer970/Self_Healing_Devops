"""
REMEDIATE NODE - performs the actual fixes as small, safe local operations.

On a real server these would be paramiko/SSH calls; here they operate on the
simulated environment (state.json) so no Docker or VPS is required.
"""

import json
import os
import subprocess
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(BASE_DIR, "service", "state.json")
FLAKY_APP = os.path.join(BASE_DIR, "service", "flaky_app.py")


def _load_state():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def rotate_logs():
    """Free disk space by archiving the big simulated log file."""
    state = _load_state()
    freed_before = state["disk_used_pct"]
    state["disk_used_pct"] = max(20.0, freed_before - 65)
    _save_state(state)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    archived = os.path.join(BASE_DIR, "logs", f"app.log.archived_{ts}")
    log_file = os.path.join(BASE_DIR, "logs", "app.log")
    try:
        os.replace(log_file, archived)            # old log -> archive file
        with open(log_file, "w", encoding="utf-8"):
            pass                                  # fresh empty log
    except FileNotFoundError:
        pass
    return f"Freed simulated disk ({freed_before:.0f}% -> {state['disk_used_pct']:.0f}%); " \
           f"archived old log to {os.path.basename(archived)}"


def restart_service():
    """Relaunch the flaky app process after an OOM crash / backend outage."""
    state = _load_state()
    state["running"] = True
    state["memory_mb"] = 250.0                    # memory cleared on restart
    state["backend_up"] = True                    # backend comes back healthy
    _save_state(state)

    # start detached so it keeps running after this agent exits
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    subprocess.Popen(
        [sys.executable, FLAKY_APP],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, cwd=BASE_DIR,
    )
    return "Service restarted: memory reset, backend marked up, new process launched."


FIXES = {
    "rotate_logs": rotate_logs,
    "restart_service": restart_service,
}


def apply(action):
    fn = FIXES.get(action)
    if fn is None:
        raise ValueError(f"No fix implemented for '{action}'")
    return fn()


def verify():
    """VERIFICATION NODE - check the simulated machine is healthy again."""
    s = _load_state()
    problems = []
    if not s.get("running"):
        problems.append("service is down")
    if s.get("disk_used_pct", 100) >= 90:
        problems.append("disk still full")
    if s.get("memory_mb", 9999) >= 1024:
        problems.append("memory exhausted again")
    if not s.get("backend_up"):
        problems.append("backend still unreachable")
    return (len(problems) == 0), problems, s
