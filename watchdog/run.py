"""WATCHDOG ENTRY POINT - one healing cycle over the remote service.

Usage:
    python -m watchdog.run --once --auto    # what the GitHub cron runs
    python -m watchdog.run --auto           # loop every WATCHDOG_SLEEP
    python -m watchdog.run                  # interactive: asks y/N first
"""

import argparse
import sys
import time
from datetime import datetime

from agent import notify, safety, reporting
from watchdog import diagnose, monitor, remediate

WATCHDOG_SLEEP = 300                    # seconds between loop cycles


def _now():
    return datetime.now().strftime("%H:%M:%S")


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _report(errors, diagnosis, outcome=None, verification=None):
    reporting.emit(errors, diagnosis, outcome, verification)


def run_cycle(auto=False):
    """One MONITOR -> ... -> REPORT pass. Returns True if it took action."""
    server_status, attempts = monitor.check_server()
    chat_code, chat_ms, snippet = None, 0, ""
    if server_status != "down":
        chat_code, chat_ms, snippet = monitor.check_chat()

    diag = diagnose.classify(server_status, chat_code)
    action = diag["action"]

    if action == "no_action":
        state = ("awake" if server_status == "awake"
                 else "recovered from cold start")
        print(f"[{_now()}] all clear - server {state}, chat OK "
              f"({chat_ms}ms, reply: {snippet or '-'})")
        return False

    errors = [f"probe {i + 1}: code={code} ({ms}ms)"
              for i, (code, ms) in enumerate(attempts)]
    if chat_code:
        errors.append(f"chat probe: code={chat_code} ({chat_ms}ms)")

    # ---- SAFETY -------------------------------------------------------
    allowed, reason = safety.check(action)
    if action == "escalate_to_human" or not allowed:
        _report(errors, diag)
        print(f" ⚠ ESCALATED TO HUMAN -> {reason}\n")
        return True

    # ---- REMEDIATE (approval unless --auto) ----------------------------
    if not auto:
        _report(errors, diag)
        answer = input(f" ▶ Apply '{action}' now? [y/N]: ").strip().lower()
        if answer != "y":
            print(" Skipped by operator. Will re-detect next cycle.\n")
            return True

    print(f" [{_now()}] {diag['diagnosis']}")
    print(f" [{_now()}] triggering Render deploy (restart)...")
    try:
        deploy_id = remediate.trigger_restart()
    except Exception as exc:                # noqa: BLE001 - report honestly
        _report(errors, diag,
                outcome=f"restart could not be triggered: {exc}",
                verification=(False, ["restart not triggered"], {}))
        return True

    ok, msg = remediate.verify_restart(deploy_id, monitor)
    verification = (True, [], {}) if ok else (False, [msg], {})
    _report(errors, diag, outcome=msg, verification=verification)
    return True


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