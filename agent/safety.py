"""
SAFETY NODE - deterministic guardrails. The LLM/rules may DECIDE what to do;
this module decides WHETHER the agent is ALLOWED to do it.

This separation is what makes autonomous agents production-safe.
"""

ALLOWED_ACTIONS = {
    # action name      description shown to the human
    "rotate_logs":     "Rotate/compress oversized logs to free disk space",
    "restart_service": "Restart the crashed application service",
}

DANGEROUS_ACTIONS = {
    "rm_rf", "drop_database", "flush_all_data",
    "modify_firewall", "reboot_machine",
}


def check(action):
    """
    Returns (allowed: bool, reason: str).
    'escalate_to_human' is always 'allowed' but handled specially upstream.
    """
    if action in DANGEROUS_ACTIONS:
        return False, f"'{action}' is on the permanent blocklist."
    if action == "escalate_to_human":
        return True, "Escalation always permitted."
    if action in ALLOWED_ACTIONS:
        return True, "Action is on the approved allowlist."
    return False, f"Action '{action}' is not recognized by the allowlist."


def describe(action):
    return ALLOWED_ACTIONS.get(action, "No human-readable description.")
