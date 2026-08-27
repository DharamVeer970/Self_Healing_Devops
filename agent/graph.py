"""
LANGGRAPH ENGINE - the agentic loop as a real cyclic state machine.

    monitor -> diagnose -> safety -> remediate -> verify -+
        ^                                                 |
        |  (still broken, retries remain)                 |
        +-------------------------------------------------+
                        | healthy / escalation / out of retries
                        v
                     report (console + Slack/email via agent.notify)

The plain-Python loop in main.py stays the default; `python main.py --graph`
switches to this LangGraph implementation. Both share agent.reporting.

Requires the optional dependency: pip install langgraph
"""

import time
import warnings
from typing import TypedDict

from agent import diagnose, monitor, remediate, safety, reporting


def _import_langgraph():
    """Import langgraph while suppressing a noisy pending-deprecation warning.

    langgraph 0.6.x installs its own warning filters during import that
    override a plain `warnings.filterwarnings(...)` call. Redirecting the
    low-level display hook (`_showwarnmsg_impl`) around just the import is
    the only guaranteed way to keep that single message off the console.
    """
    global END, START, StateGraph

    _real = warnings._showwarnmsg_impl if hasattr(
        warnings, "_showwarnmsg_impl") else None

    def quiet(record):
        if "allowed_objects" not in str(record.message):
            if _real is not None:
                _real(record)

    if _real is not None:
        warnings._showwarnmsg_impl = quiet

    try:
        from langgraph.graph import END, START, StateGraph
    finally:
        if _real is not None:
            warnings._showwarnmsg_impl = _real


try:
    _import_langgraph()
    HAS_LANGGRAPH = True
except ImportError:                       # langgraph not installed
    HAS_LANGGRAPH = False

VERIFY_DELAY = 2       # seconds to wait before a post-fix health check
MAX_ATTEMPTS = 3       # remediation retries before giving up


def _now_short():
    from datetime import datetime
    return datetime.now().strftime("%H:%M:%S")


class AgentState(TypedDict, total=False):
    auto: bool
    lines: list
    errors: list
    diagnosis: dict
    fresh_diagnosis: bool   # True only when node_diagnose actually ran THIS pass
    allowed: bool
    reason: str
    attempt: int
    outcome: str
    verification: tuple
    action_taken: bool
    declined: bool


# ---------------------------------------------------------------------------
# Nodes - thin wrappers over the existing agent modules (monkeypatch-friendly)
# ---------------------------------------------------------------------------

def node_monitor(state):
    """OBSERVE - tail the log and collect ERROR/FATAL lines.

    fresh_diagnosis resets to False on every scan; it only flips True inside
    node_diagnose, so a scan that skips diagnose (no new errors) is never
    mistaken for one that produced an up-to-date diagnosis.
    """
    lines = monitor.read_new_lines()
    return {"lines": lines, "errors": monitor.extract_errors(lines),
            "fresh_diagnosis": False}


def node_diagnose(state):
    """DIAGNOSE - classify errors; None diagnosis means nothing to act on."""
    errors = state.get("errors") or []
    diagnosis = diagnose.classify(errors) if errors else None
    return {"diagnosis": diagnosis, "fresh_diagnosis": diagnosis is not None,
            "verification": None, "outcome": None, "declined": False}


def node_safety(state):
    """SAFETY - allowlist/blocklist gate."""
    allowed, reason = safety.check(state["diagnosis"]["action"])
    return {"allowed": allowed, "reason": reason}


def node_remediate(state):
    """HEAL - apply the fix (with human approval unless --auto)."""
    if not state.get("allowed"):
        return {"action_taken": True}              # escalation path only

    attempt = state.get("attempt", 0) + 1
    if not state.get("auto"):
        print(reporting.format_report(state["errors"], state["diagnosis"]))
        answer = input(f" \u25b6 Apply "
                       f"'{state['diagnosis']['action']}' now? [y/N]: "
                       ).strip().lower()
        if answer != "y":
            print(" Skipped by operator. Will re-detect on next cycle.\n")
            return {"action_taken": True, "declined": True,
                    "outcome": "Skipped by operator.",
                    "attempt": attempt}

    outcome = remediate.apply(state["diagnosis"]["action"])
    return {"outcome": outcome, "attempt": attempt, "action_taken": True,
            "declined": False}


def node_verify(state):
    """VERIFY - post-fix health check (waits briefly for effects to land)."""
    time.sleep(VERIFY_DELAY)
    return {"verification": remediate.verify()}


def node_report(state):
    """REPORT - human-readable summary + Slack/email delivery."""
    declined = state.get("declined", False)

    if declined:
        print("=" * 70 + "\n")
        return state

    if not state.get("fresh_diagnosis"):
        # Retry found the machine still broken, so report the problems and exit.
        _, problems, _state = (state.get("verification")
                               or (False, ["unknown"], {}))
        print(f" [{_now_short()}] STILL BROKEN -> {', '.join(problems)}"
              f"  (attempts used: {state.get('attempt', 0)})\n")
        return {"action_taken": True}

    reporting.emit(
        state["errors"], state["diagnosis"],
        state.get("outcome"),
        state.get("verification"),
        diagnose.llm_explanation(state["errors"], state["diagnosis"]),
    )
    escalating = (not state.get("allowed", True)
                  or state["diagnosis"]["action"] == "escalate_to_human")
    if escalating:
        print(f" \u26a0 ESCALATED TO HUMAN -> {state['reason']}\n")
        return {"action_taken": True}
    return state


def node_clear(state):
    """All-clear path: no errors seen in this scan."""
    from datetime import datetime
    heartbeat = [ln for ln in state.get("lines", []) if "INFO" in ln]
    if heartbeat:
        print(f"[{datetime.now():%H:%M:%S}] all clear "
              f"(last: {heartbeat[-1][:80]})")
    return state


# ---------------------------------------------------------------------------
# Routing - conditional edges
# ---------------------------------------------------------------------------

def route_after_monitor(state):
    if state.get("errors"):
        return "diagnose"
    # Retry pass with no fresh errors: exit quietly on first scan,
    # but report machine status when a healing cycle is in progress.
    return "clear" if not state.get("attempt") else "report"


def route_after_diagnose(state):
    """No fresh classification -> decide between quiet exit and status report."""
    if state.get("fresh_diagnosis"):
        return "safety"
    return "report" if state.get("attempt") else "clear"


def route_after_safety(state):
    blocked = not state.get("allowed")
    unknown = state["diagnosis"].get("confidence") == 0.0
    if blocked or unknown:
        return "report"                            # escalate to human
    return "remediate"


def route_after_remediate(state):
    return "report" if state.get("declined") else "verify"


def route_after_verify(state):
    ok = state.get("verification", (False, ["no check ran"], {}))[0]
    if ok or state.get("attempt", 0) >= MAX_ATTEMPTS:
        return "report"
    return "monitor"                               # heal failed -> try again


_APP = None


def build():
    """Compile the state graph (cached)."""
    global _APP
    if _APP is not None:
        return _APP
    if not HAS_LANGGRAPH:
        raise ImportError(
            "langgraph is not installed. Run: pip install langgraph")

    g = StateGraph(AgentState)
    for name, fn in (("monitor", node_monitor), ("clear", node_clear),
                     ("diagnose", node_diagnose), ("safety", node_safety),
                     ("remediate", node_remediate),
                     ("verify", node_verify), ("report", node_report)):
        g.add_node(name, fn)

    g.add_edge(START, "monitor")
    g.add_conditional_edges(
        "monitor", route_after_monitor,
        {"diagnose": "diagnose", "clear": "clear", "report": "report"})
    g.add_edge("clear", END)
    g.add_conditional_edges(
        "diagnose", route_after_diagnose,
        {"safety": "safety", "clear": "clear", "report": "report"})
    g.add_conditional_edges(
        "safety", route_after_safety,
        {"remediate": "remediate", "report": "report"})
    g.add_conditional_edges(
        "remediate", route_after_remediate,
        {"verify": "verify", "report": "report"})
    g.add_conditional_edges(
        "verify", route_after_verify,
        {"monitor": "monitor", "report": "report"})
    g.add_edge("report", END)

    _APP = g.compile()
    return _APP


def run_cycle(auto=False):
    """Run one full agentic pass. Returns True when an action was taken."""
    result = build().invoke(
        {"auto": auto},
        config={"recursion_limit": 4 * MAX_ATTEMPTS + 8},
    )
    return bool(result.get("action_taken"))
