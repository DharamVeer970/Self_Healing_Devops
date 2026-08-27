"""
FLAKY APP - Simulates a badly-behaved production service (NO Docker needed).

It maintains a fake "system state" in state.json:
  - disk_used_pct : slowly fills up -> eventually "No space left on device"
  - memory_mb     : leaks MBs every tick -> eventually OutOfMemoryError + CRASH
  - backend_up    : can be switched off -> causes 502 Bad Gateway errors

Run it in one terminal:
    python demo_env/flaky_app.py
"""

import json
import os
import random
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.json")
LOG_FILE = os.path.join(BASE_DIR, "..", "logs", "app.log")

DISK_LIMIT_PCT = 90      # disk errors start above this %
MEM_LIMIT_MB = 1024      # app crashes above this


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"running": True, "disk_used_pct": 55.0,
            "memory_mb": 300.0, "backend_up": True}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


def log(line):
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(f"{ts} | {line}\n")


def main():
    state = load_state()
    pid = os.getpid()
    log(f"INFO  [pid {pid}] FlakyApp v1.0 starting (simulated service)")
    print(f"[FlakyApp] running with pid {pid}. Logs -> {os.path.abspath(LOG_FILE)}")
    print("[FlakyApp] Press Ctrl+C to stop it manually.\n")

    while state["running"]:
        time.sleep(2)

        # ---- simulate disk filling up -------------------------------------
        state["disk_used_pct"] = min(100.0, state["disk_used_pct"] + random.uniform(0.8, 2.5))

        # ---- simulate a slow memory leak ----------------------------------
        state["memory_mb"] += random.uniform(12, 30)

        # ---- decide what happens this tick --------------------------------
        if state["memory_mb"] >= MEM_LIMIT_MB:
            log(f"ERROR [pid {pid}] MemoryError: OutOfMemoryError - "
                f"unable to allocate {random.randint(64,512)} MiB "
                f"(rss={state['memory_mb']:.0f}MB, limit={MEM_LIMIT_MB}MB)")
            state["running"] = False          # the process dies!
            save_state(state)
            log(f"FATAL [pid {pid}] Process crashed - exited unexpectedly")
            print("\n[FlakyApp] 💥 CRASHED from out-of-memory! (agent must restart it)")
            break

        if state["disk_used_pct"] >= DISK_LIMIT_PCT:
            log(f"ERROR [pid {pid}] OSError: [Errno 28] No space left on device "
                f"- failed to write /var/log/app.log (disk={state['disk_used_pct']:.0f}%)")

        if not state["backend_up"]:
            log(f"ERROR [pid {pid}] upstream server temporarily unavailable: "
                f"502 Bad Gateway <html><body>connect() failed (111: Connection refused) "
                f"while connecting to upstream 127.0.0.1:8000</body></html>")

        # ---- healthy heartbeat so logs don't look only-broken -------------
        log(f"INFO  [pid {pid}] GET /health 200 OK | mem={state['memory_mb']:.0f}MB "
            f"| disk={state['disk_used_pct']:.0f}%")

        save_state(state)


if __name__ == "__main__":
    main()
