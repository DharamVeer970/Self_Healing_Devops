"""Dev-only helper: seeds a fake error log + state so we can test the agent."""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
os.makedirs(os.path.join(BASE, "logs"), exist_ok=True)
with open(os.path.join(BASE, "logs", ".offset"), "w", encoding="utf-8") as f:
    f.write("0")

state = {
    "running": True,
    "disk_used_pct": 97.0,
    "memory_mb": float(sys.argv[1]) if len(sys.argv) > 1 else 400.0,
    "backend_up": False,
}
with open(os.path.join(BASE, "demo_env", "state.json"), "w",
          encoding="utf-8") as f:
    json.dump(state, f, indent=2)

with open(os.path.join(BASE, "logs", "app.log"), "a", encoding="utf-8") as f:
    f.write("2026-08-27 10:00:01 | ERROR [pid 123] OSError: [Errno 28] "
            "No space left on device - failed to write (disk=97%)\n")
    if state["memory_mb"] >= 1024:
        f.write("2026-08-27 10:00:02 | FATAL [pid 123] Process crashed - "
                "MemoryError: OutOfMemoryError unable to allocate 256 MiB\n")
print("seeded: disk full + backend down",
      "(+ OOM crash)" if state["memory_mb"] >= 1024 else "")
