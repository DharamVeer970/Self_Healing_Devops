"""
SELF-HEALING DEVOPS AGENT - main entry point (100% Docker-free).

Agentic loop (same nodes you'd model in LangGraph, done in plain Python):
    MONITOR -> DIAGNOSE -> SAFETY CHECK -> (REMEDIATE -> VERIFY)* -> REPORT

Usage:
    python main.py            # continuous watch, asks y/n before each fix
    python main.py --auto     # continuous watch, auto-applies allowlisted fixes
    python main.py --once     # single scan cycle, then exit (great for testing)

Run the fake broken server in a second terminal first:
    python service/flaky_app.py
"""

import argparse
import sys
import time
from datetime import datetime

from agent import diagnose, graph as agent_graph, monitor, \
    remediate, reporting, safety


def _utf8_console():
    """Prevent UnicodeEncodeError crashes on non-UTF-8 consoles (cp1252).

    Symbols like 'HEALTHY check-mark' or 'warning' in reports must never
    kill the agent loop; unsupported glyphs degrade gracefully instead.
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

BANNER = r"""
  ____  _____ _   _ _____ _   _ _    _ _____   _____ _    _  ____   _   _  ___  _   _
 / ___|| ____| \ | | ____| | | | |  | | ____| |  ___/ \  | |/ /   / \ | |/ _ \| \ | |
 \___ \|  _| |  \| |  _| | |_| | |__| |  _|   | |_ / _ \ | ' /   / _ \| | | | |  \| |
  ___) | |___| |\  | |___|  _  |  __  | |___  |  _/ ___ \| . \  / ___ \ | |_| | |\  |
 |____/|_____|_| \_|_____|_| |_|_|  |_|_____| |_|/_/   \_\_|\_\/_/   \_\_|\___/|_| \_|
                    observe -> diagnose -> fix -> verify -> report
"""


def report_incident(errors, diagnosis, outcome=None, verification=None,
                    llm_text=None):
    """REPORT NODE - prints + delivers a human-readable incident summary.

    Delegates to agent.reporting so both engines stay pixel-identical.
    """
    reporting.emit(errors, diagnosis, outcome, verification, llm_text)


def scan_cycle(auto=False):
    """One full pass of the agentic loop. Returns True if it took action."""
    lines = monitor.read_new_lines()
    errors = monitor.extract_errors(lines)

    if not errors:
        heartbeat = [ln for ln in lines if "INFO" in ln]
        if heartbeat:
            print(f"[{datetime.now():%H:%M:%S}] all clear "
                  f"(last: {heartbeat[-1][:80]})")
        return False

    # ---- DIAGNOSE ---------------------------------------------------------
    diagnosis = diagnose.classify(errors)
    action = diagnosis["action"]

    # ---- SAFETY CHECK -----------------------------------------------------
    allowed, reason = safety.check(action)

    if action == "escalate_to_human" or not allowed:
        report_incident(errors, diagnosis)
        print(f" ⚠ ESCALATED TO HUMAN -> {reason}\n")
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
    report_incident(errors, diagnosis, outcome, verification, llm_text)
    return True


def main():
    parser = argparse.ArgumentParser(description="Self-Healing DevOps Agent")
    parser.add_argument("--auto", action="store_true",
                        help="auto-apply allowlisted fixes without asking")
    parser.add_argument("--once", action="store_true",
                        help="run one scan cycle and exit")
    parser.add_argument("--graph", action="store_true",
                        help="use the LangGraph engine "
                             "(cyclic state machine)")
    args = parser.parse_args()

    _utf8_console()
    print(BANNER)
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
    print(f" Mode:   {mode}\n Engine: {engine}"
          f"\n LLM:    {llm}\n Notify: {configured}\n")

    while True:
        if args.graph:
            agent_graph.run_cycle(auto=args.auto)
        else:
            scan_cycle(auto=args.auto)
        if args.once:
            break
        time.sleep(3)


if __name__ == "__main__":
    main()
