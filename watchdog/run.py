"""WATCHDOG ENTRY POINT - one healing cycle over the remote service.

Usage:
    python -m watchdog.run --once --auto    # what the GitHub cron runs
    python -m watchdog.run --auto           # loop every WATCHDOG_SLEEP
    python -m watchdog.run                  # interactive: asks y/N first
"""

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime

from agent import notify, safety, reporting
from watchdog import diagnose, monitor, remediate, render_logs

WATCHDOG_SLEEP = 300                    # seconds between loop cycles
LOG_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "logs", ".watchdog_log_errors.json")


def _now():
    return datetime.now().strftime("%H:%M:%S")


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _report(errors, diagnosis, outcome=None, verification=None):
    reporting.emit(errors, diagnosis, outcome, verification)


def _hash_row(row):
    # SHA1 used only for dedupe (non-security), marked safe (S4790).
    return hashlib.sha1(
        render_logs.text_of(row).encode("utf-8", "replace"),
        usedforsecurity=False).hexdigest()


def _log_incidents(log_scan=None, state_file=None):
    """Scan every service's logs and return only NEW error incidents.

    Dedupe: the same error lines are reported once, then remembered in a
    state file (logs/.watchdog_log_errors.json) so a 20-minute cron job
    does not re-alarm on the same crash until new errors appear.

    `log_scan` / `state_file` are injectable for tests.
    """
    if log_scan is None:
        log_scan = render_logs.scan_all()
    state_path = state_file or os.environ.get(
        "WATCHDOG_LOG_STATE_FILE", LOG_STATE_FILE)

    state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                state = loaded
        except Exception:                    # noqa: BLE001 - corrupt = reset
            state = {}

    incidents = []
    for inc in log_scan or []:
        all_digests = {_hash_row(r) for r in inc.get("errors", [])}
        seen = set(state.get(inc.get("service_id"), []) or [])
        fresh = [r for r in inc.get("errors", [])
                 if _hash_row(r) not in seen]
        if fresh:
            incidents.append({**inc, "errors": fresh})
        state[inc.get("service_id", "")] = sorted(all_digests | seen)[-500:]

    try:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError:                          # noqa: BLE001 - reporting only
        pass
    return incidents


def _report_log_incidents(incidents):
    """Escalate the NEW remote-log errors to a human (print + notify)."""
    for inc in incidents:
        errors = [render_logs.text_of(r) for r in inc["errors"]]
        sample = "\n".join(errors[-3:])
        diagnosis = {
            "error_line": (f"{inc['label']} ({inc['url']}) "
                           "logged an error"),
            "diagnosis": (f"{inc['label']} is reachable, but its recent "
                          f"Render logs show errors:\n{sample}"),
            "action": "escalate_to_human",
            "confidence": 0.6,
        }
        _report(errors, diagnosis)
        print(f" ⚠ LOG ERROR in {inc['label']}: {inc['url']}")
        for line in errors[-3:]:
            print(f"   ⚠ {line[:200]}")
        print()
    print(f" ⚠ {len(incidents)} service(s) logged new errors (see report).\n")


def _probe_server():
    """Probe server and optionally chat; return (status, attempts, chat_code, ms, snippet)."""
    server_status, attempts = monitor.check_server()
    chat_code, chat_ms, snippet = None, 0, ""
    if server_status != "down" and monitor.chat_probe_enabled():
        chat_code, chat_ms, snippet = monitor.check_chat()
    return server_status, attempts, chat_code, chat_ms, snippet


def _handle_no_action(server_status, chat_code, chat_ms, snippet, log_incidents):
    state = "awake" if server_status == "awake" else "recovered from cold start"
    if chat_code is not None:
        chat = f"chat OK ({chat_ms}ms, reply: {snippet or '-'})"
    else:
        chat = "chat probe off (set WATCHDOG_CHAT_PROBE=1 to enable)"
    if log_incidents:
        _report_log_incidents(log_incidents)
        return True
    print(f"[{_now()}] all clear - server {state}, {chat}")
    return False


def _collect_errors(attempts, chat_code, chat_ms, log_incidents):
    errors = [f"probe {i + 1}: code={code} ({ms}ms)"
              for i, (code, ms) in enumerate(attempts)]
    if chat_code:
        errors.append(f"chat probe: code={chat_code} ({chat_ms}ms)")
    for inc in log_incidents:
        errors.append(f"{inc['label']} LOG ERROR: "
                      f"{render_logs.text_of(inc['errors'][-1])[:150]}")
    return errors


def _maybe_escalate(action, allowed, reason, errors, diag):
    if action == "escalate_to_human" or not allowed:
        _report(errors, diag)
        print(f" \u26a0 ESCALATED TO HUMAN -> {reason}\n")
        return True
    return False


def _maybe_prompt_operator(action, errors, diag, auto):
    if auto:
        return False
    _report(errors, diag)
    answer = input(f" \u25b6 Apply '{action}' now? [y/N]: ").strip().lower()
    if answer != "y":
        print(" Skipped by operator. Will re-detect next cycle.\n")
        return True
    return False


def _do_restart(errors, diag):
    print(f" [{_now()}] {diag['diagnosis']}")
    print(f" [{_now()}] triggering Render deploy (restart)...")
    try:
        deploy_id = remediate.trigger_restart()
    except Exception as exc:  # NOSONAR - report honestly, never crash watchdog
        _report(errors, diag,
                outcome=f"restart could not be triggered: {exc}",
                verification=(False, ["restart not triggered"], {}))
        return True, None
    ok, msg = remediate.verify_restart(deploy_id, monitor)
    verification = (True, [], {}) if ok else (False, [msg], {})
    _report(errors, diag, outcome=msg, verification=verification)
    return True, ok


def run_cycle(auto=False):
    """One MONITOR -> ... -> REPORT pass. Returns True if it took action."""
    server_status, attempts, chat_code, chat_ms, snippet = _probe_server()
    diag = diagnose.classify(server_status, chat_code)
    log_incidents = _log_incidents()

    if diag["action"] == "no_action":
        return _handle_no_action(server_status, chat_code, chat_ms, snippet, log_incidents)

    errors = _collect_errors(attempts, chat_code, chat_ms, log_incidents)
    allowed, reason = safety.check(diag["action"])

    if _maybe_escalate(diag["action"], allowed, reason, errors, diag):
        return True

    if _maybe_prompt_operator(diag["action"], errors, diag, auto):
        return True

    acted, _ = _do_restart(errors, diag)
    return acted


def main():
    parser = argparse.ArgumentParser(description="Portfolio Watchdog")
    parser.add_argument("--auto", action="store_true",
                        help="restart automatically without asking")
    parser.add_argument("--once", action="store_true",
                        help="run one cycle and exit (cron mode)")
    parser.add_argument("--every", type=int, default=WATCHDOG_SLEEP,
                        help="seconds between cycles in loop mode")
    args = parser.parse_args()

    _utf8_console()
    channels = ", ".join(notify.channels()) or "none"
    print(f" Portfolio Watchdog - target: {monitor.APP_URL}\n"
          f" Mode: {'AUTONOMOUS' if args.auto else 'SUGGEST-ONLY'}"
          f" | Notify: {channels}\n")

    while True:
        try:
            run_cycle(auto=args.auto)
        except Exception as exc:            # noqa: BLE001 - never die
            print(f"[{_now()}] watchdog cycle error: {exc}")
        if args.once:
            break
        time.sleep(args.every)


if __name__ == "__main__":
    main()