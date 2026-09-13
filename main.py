"""
SELF-HEALING DEVOPS AGENT - main entry point (100% Docker-free).

Agentic loop (same nodes you'd model in LangGraph, done in plain Python):
    MONITOR -> DIAGNOSE -> SAFETY CHECK -> (REMEDIATE -> VERIFY)* -> REPORT
                                                          \
                                              escalate -> SCRIPT WRITER

New in this version:
    - URL connectivity checker runs every cycle; reports state changes.
    - Script-writer agent activates on escalation; patches source or
      writes fix scripts to fixes/ for human review.

Usage:
    python main.py            # continuous watch, asks y/n before each fix
    python main.py --auto     # continuous watch, auto-applies allowlisted fixes
    python main.py --once     # single scan cycle, then exit (great for testing)
    python main.py --graph    # use the LangGraph engine (cyclic state machine)
    python main.py --check-urls  # run one URL connectivity check and print status

Run the fake broken server in a second terminal first:
    python service/flaky_app.py
"""

import argparse
import sys
import time
from datetime import datetime

from agent import diagnose, graph as agent_graph, monitor, \
    remediate, reporting, safety, script_writer, url_checker


def _utf8_console():
    """Prevent UnicodeEncodeError crashes on non-UTF-8 consoles (cp1252).

    Symbols like 'HEALTHY check-mark' or 'warning' in reports must never
    kill the agent loop; unsupported glyphs degrade gracefully instead.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

BANNER = r"""
  ____  _____ _   _ _____ _   _ _    _ _____   _____ _    _  ____  _     _  ___  _   _
 / ___|| ____| \ | | ____| | | | |  | | ____| |  ___/ \  | |/ /   / \   | |/ _ \| \ | |
 \___ \|  _| |  \| |  _| | |_| | |__| |  _|   | |_ / _ \ | ' /   / _ \  | | | | |_ \| |
  ___) | |___| |\  | |___|  _  |  __  | |___  |  _/ ___ \| . \  / ___ \ | |_| | | \_  |
 |____/|_____|_| \_|_____|_| |_|_|  |_|_____| |_|/_/   \_\_|\_\/_/   \_\|_|\___/|_| \_|
                    observe -> diagnose -> fix -> verify -> report
"""

def report_incident(errors, diagnosis, outcome=None, verification=None,
                    llm_text=None):
    """REPORT NODE - prints + delivers a human-readable incident summary."""
    reporting.emit(errors, diagnosis, outcome, verification, llm_text)



def _handle_url_changes():
    """Check for URL connectivity state changes; report if any.

    Returns the list of state-change events (empty when nothing changed).
    """
    events = url_checker.check_all()
    for ev in events:
        direction = "DOWN" if ev["to"] == "down" else "RECOVERED"
        print(f"\n [{ev['at']}] URL {direction}: {ev['url']}")
        print(f"   was: {ev['from']}  ->  now: {ev['to']}")
        print(f"   detail: {ev['detail']}")
        sys.stdout.flush()
    return events


def _handle_escalation(errors, diagnosis):
    """ESCALATION HANDLER: rule-based agent couldn't fix.

    First tries the script-writer agent (patch source / write fix scripts).
    If that also fails, escalates to human review.
    """
    result = script_writer.attempt_fix(errors, diagnosis)
    strategy = result.get("strategy", "none")

    print(f"\n 🔧 SCRIPT WRITER (strategy: {strategy})")

    if strategy == "patch_source_logging":
        for patch_msg in result.get("patches", []):
            print(f"   ✓ {patch_msg}")
        print(f"   {result['message']}")

    elif strategy == "llm_script":
        print(f"   {result['message']}")
        if result.get("filepath"):
            print(f"   File: {result['filepath']}")

    elif strategy == "llm_script_dry_run":
        print(f"   {result['message']}")

    else:
        report_incident(errors, diagnosis)
        print(f" ⚠ ESCALATED TO HUMAN -> {result['message']}\n")

    return result


def scan_cycle(auto=False):
    """One full pass of the agentic loop. Returns True if it took action.

    URL connectivity DOWNs are routed to the script-writer agent just like
    log incidents: the rule engine tries first, then the LLM writes a fix
    script to fixes/ for human review.
    """
    # --- URL connectivity check (every cycle) ---
    url_events = _handle_url_changes()
    downs = [ev for ev in url_events if ev.get("to") == "down"]

    lines = monitor.read_new_lines()
    errors = monitor.extract_errors(lines)

    if not errors and not downs:
        heartbeat = [ln for ln in lines if "INFO" in ln]
        if heartbeat:
            print(f"[{datetime.now():%H:%M:%S}] all clear "
                  f"(last: {heartbeat[-1][:80]})")
        return False

    # URL downs go straight to the script-writer (connectivity has no
    # local rule-based fix; the agent LLM-writes a fix script for review).
    if downs:
        url_errors = [f"ERROR connectivity: {ev['url']} is DOWN ({ev['detail']})"
                      for ev in downs]
        url_diagnosis = {
            "diagnosis": "One or more monitored URLs are unreachable (connectivity issue).",
            "action": "escalate_to_human",
            "confidence": 0.0,
            "error_line": url_errors[-1],
        }
        _handle_escalation(url_errors, url_diagnosis)

    if not errors:
        return True

    # ---- DIAGNOSE ---------------------------------------------------------
    diagnosis = diagnose.classify(errors)
    action = diagnosis["action"]

    # ---- SAFETY CHECK -----------------------------------------------------
    allowed, _reason = safety.check(action)

    if action == "escalate_to_human" or not allowed:
        _handle_escalation(errors, diagnosis)
        return True

    # ---- REMEDIATE (with human approval unless --auto) --------------------
    if not auto:
        report_incident(errors, diagnosis)
        answer = input(f" ▶ Apply '{action}' now? [y/N]: ").strip().lower()
        if answer != "y":
            print(" Skipped by operator. Will re-detect on next cycle.\n")
            return True

    outcome = remediate.apply(action)

    # ---- VERIFY -----------------------------------------------------------
    time.sleep(2)
    verification = remediate.verify()
    llm_text = diagnose.llm_explanation(errors, diagnosis)

    # If verification failed, try the script writer
    if not verification[0]:
        _handle_escalation(errors, diagnosis)

    report_incident(errors, diagnosis, outcome, verification, llm_text)
    return True


def _parse_args():
    parser = argparse.ArgumentParser(description="Self-Healing DevOps Agent")
    parser.add_argument("--auto", action="store_true",
                        help="auto-apply allowlisted fixes without asking")
    parser.add_argument("--once", action="store_true",
                        help="run one scan cycle and exit")
    parser.add_argument("--graph", action="store_true",
                        help="use the LangGraph engine "
                             "(cyclic state machine)")
    parser.add_argument("--check-urls", action="store_true",
                        help="run URL connectivity check and print status, then exit")
    return parser.parse_args()


def _handle_check_urls():
    events = url_checker.check_all()
    if events:
        print("\n URL state changes detected:")
        for ev in events:
            print(f"  [{ev['at']}] {ev['url']}: "
                  f"{ev['from']} -> {ev['to']} ({ev['detail']})")
    else:
        print("\n No URL state changes.")
    print(f"\n{url_checker.summary()}")


def _print_startup(args):
    engine = "LangGraph" if args.graph else "plain loop"
    if args.graph and not agent_graph.HAS_LANGGRAPH:
        print(" \u26a0 langgraph not installed - falling back to plain loop."
              "  (pip install langgraph)\n")
        args.graph = False
    mode = "AUTONOMOUS (allowlisted fixes auto-applied)" \
        if args.auto else "SUGGEST-ONLY (asks before each fix)"
    llm = ("LLM connected via env key (TokenRouter/OpenRouter)"
           if diagnose.llm_available() else "offline rule-based mode")
    from agent import notify
    configured = ", ".join(notify.channels()) or "none"
    monitored = ", ".join(url_checker.urls()) or "none"
    print(f" Mode:   {mode}\n Engine: {engine}"
          f"\n LLM:    {llm}\n Notify: {configured}"
          f"\n URLs:   {monitored}\n")


def _run_loop(args):
    while True:
        if args.graph:
            agent_graph.run_cycle(auto=args.auto)
        else:
            scan_cycle(auto=args.auto)
        if args.once:
            break
        time.sleep(3)


def main():
    args = _parse_args()
    _utf8_console()
    print(BANNER)

    if args.check_urls:
        _handle_check_urls()
        return

    _print_startup(args)
    _run_loop(args)


if __name__ == "__main__":
    main()
