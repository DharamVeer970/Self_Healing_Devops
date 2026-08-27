"""
REPORTING NODE - single source of truth for the human-readable incident
summary shared by BOTH engines (plain loop in main.py, LangGraph in
agent/graph.py). Prints to the console and hands off to agent.notify.
"""

from agent import notify, safety


def format_report(errors, diagnosis, outcome=None, verification=None,
                  llm_text=None):
    """Return the multi-line incident summary (without notification footer)."""
    sep = "=" * 70
    out = [
        "", sep,
        f" INCIDENT REPORT  |  {_now()}",
        sep,
        f" Error captured : {diagnosis['error_line'][:100]}",
        f" Diagnosis      : {diagnosis['diagnosis']}",
        f" Confidence     : {diagnosis['confidence']:.0%}",
        f" Proposed fix   : {diagnosis['action']} "
        f"({safety.describe(diagnosis['action'])})",
    ]
    if outcome:
        out.append(f" Action taken   : {outcome}")
    if verification:
        ok, problems, state = verification
        status = "HEALTHY \u2714" if ok else \
            f"BROKEN \u2718 ({', '.join(problems)})"
        out.append(f" Verification   : {status}")
        out.append(f" Machine state  : {state}")
    if llm_text:
        out.append("\n LLM root-cause analysis:")
        out.extend(f"   {para}" for para in llm_text.split("\n"))
    out.append(sep)
    return "\n".join(out)


def emit(errors, diagnosis, outcome=None, verification=None, llm_text=None):
    """Print the report and push it to any configured notification channel."""
    from datetime import datetime

    block = format_report(errors, diagnosis, outcome, verification, llm_text)
    print(block)

    deliveries = notify.deliver(
        title=f"[Self-Healing Agent] {diagnosis['action']}",
        body=(f"time     : {datetime.now():%Y-%m-%d %H:%M:%S}\n"
              f"error    : {diagnosis['error_line'][:100]}\n"
              f"diagnosis: {diagnosis['diagnosis']}\n"
              f"fix      : {diagnosis['action']}"
              + (f"\noutcome  : {outcome}" if outcome else "")
              + (f"\nverified : {'HEALTHY' if verification[0] else verification[1]}"
                 if verification else "")),
    )
    if deliveries:
        status = ", ".join(f"{k}: {v}" for k, v in deliveries.items())
        print(f" Delivered     : {status}\n")


def _now():
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
