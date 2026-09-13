"""


E2E smoke test - local dev helper (NOT pushed; tests/ is gitignored).

Seeds a disk-full incident in the simulated service state, then drives the
real agent through one full cycle and checks it healed the machine.

Run from the project root:
    python tests/e2e_smoke.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STATE_FILE = os.path.join(ROOT, "service", "state.json")
LOG_FILE = os.path.join(ROOT, "logs", "app.log")
OFFSET_FILE = os.path.join(ROOT, "logs", ".offset")


def seed_incident():
    """Write a disk-full incident into the service state + log."""
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"running": True, "disk_used_pct": 97.0,
                   "memory_mb": 400.0, "backend_up": True}, f)
    with open(LOG_FILE, "w", encoding="utf-8") as f:
        f.write("ERROR [pid 1] OSError: [Errno 28] No space left on "
                "device - failed to write (disk=97%)\n")
    with open(OFFSET_FILE, "w", encoding="utf-8") as f:
        f.write("0")


def read_state():
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def main():
    seed_incident()
    print("Seeded disk-full incident. Running agent (offline rule engine)...\n")

    import main as agent_main
    agent_main._utf8_console()
    assert agent_main.scan_cycle(auto=True) is True, "agent took no action"

    state = read_state()
    assert state["disk_used_pct"] == 32.0, \
        f"disk not healed: {state['disk_used_pct']}%"
    assert state["backend_up"] is True
    print("\nE2E PASS: detected -> healed (97% -> 32%) -> verified healthy")


if __name__ == "__main__":
    main()
