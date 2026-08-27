"""
MONITOR NODE - tails logs/app.log and extracts ERROR/FATAL lines.

Pure Python, zero dependencies. Tracks a byte offset between scans so we
only analyze NEW lines each cycle (just like `tail -f`).
"""

import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE = os.path.join(BASE_DIR, "logs", "app.log")
OFFSET_FILE = os.path.join(BASE_DIR, "logs", ".offset")


def _read_offset():
    try:
        with open(OFFSET_FILE, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _write_offset(offset):
    os.makedirs(os.path.dirname(OFFSET_FILE), exist_ok=True)
    with open(OFFSET_FILE, "w", encoding="utf-8") as f:
        f.write(str(offset))


def read_new_lines():
    """Return list of new log lines since last call (and remember position).

    Reads in binary mode so the persisted offset is always a real byte
    offset (text-mode offsets silently break on non-ASCII log lines).
    Only whole lines are consumed: a partially written trailing line is
    left unread until its newline arrives on a later scan.
    """
    if not os.path.exists(LOG_FILE):
        return []

    offset = _read_offset()
    size = os.path.getsize(LOG_FILE)

    if offset > size:          # log was rotated/truncated -> start fresh
        offset = 0

    with open(LOG_FILE, "rb") as f:
        f.seek(offset)
        chunk = f.read()

    complete_len = chunk.rfind(b"\n") + 1      # last full line's end
    if complete_len:
        _write_offset(offset + complete_len)

    return chunk[:complete_len].decode("utf-8", errors="replace").splitlines()


def extract_errors(lines):
    """Filter down to ERROR / FATAL lines."""
    return [ln for ln in lines if "ERROR" in ln or "FATAL" in ln]


def latest_state():
    """Read the simulated machine state that demo_env/state.json holds."""
    path = os.path.join(BASE_DIR, "demo_env", "state.json")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None
