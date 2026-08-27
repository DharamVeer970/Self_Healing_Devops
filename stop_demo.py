"""Utility: stops any running simulated flaky_app processes."""
import subprocess

PS = ("Get-CimInstance Win32_Process | "
      "Where-Object { $_.CommandLine -match 'flaky_app.py' } | "
      "ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "
      "$_.ProcessId }")
result = subprocess.run(
    ["powershell", "-NoProfile", "-Command", PS],
    capture_output=True, text=True,
)
pids = [ln.strip() for ln in result.stdout.splitlines() if ln.strip().isdigit()]
print("Stopped:", ", ".join(pids) if pids else "(none were running)")
