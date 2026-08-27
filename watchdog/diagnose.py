"""REMOTE DIAGNOSE NODE - maps monitor findings to an action.

Conservative by design: an action may only restart when a restart can
actually help. Upstream provider problems (rate-limit, provider outage)
are escalated, never "fixed" with a blind restart.
"""

LAST_PROBES = ""                        # filled by classify() for reporting


def _rule(error_line, diagnosis, action, confidence):
    return {"error_line": error_line, "diagnosis": diagnosis,
            "action": action, "confidence": confidence}


def classify(server_status, chat_code):
    """Return a diagnosis dict compatible with agent.reporting.emit.

    server_status: 'awake' | 'sleep_recovered' | 'down'
    chat_code:     200 | 429 | 502 | None (not probed / unknown)
    """
    global LAST_PROBES
    chat = chat_code if chat_code is not None else 0
    LAST_PROBES = f"server={server_status} chat={chat or 'n/a'}"

    if server_status == "down":
        return _rule(
            LAST_PROBES,
            "Server is DOWN: GET / returned 503/timeout on every retry - "
            "not a cold start. A redeploy should revive it.",
            "restart_service", 0.95)

    if chat == 502:
        return _rule(
            LAST_PROBES,
            "Chatbot 502: the server is up but the chat/retrieval provider "
            "failed. Trying one restart; if the upstream itself is down, "
            "verification will report it honestly.",
            "restart_service", 0.60)

    if chat == 429:
        return _rule(
            LAST_PROBES,
            "Chatbot 429: upstream provider rate-limited. A restart would "
            "not help - waiting is the only fix.",
            "escalate_to_human", 0.90)

    if chat and chat != 200:
        return _rule(
            LAST_PROBES,
            f"Chatbot returned unexpected code {chat}. Needs a human look.",
            "escalate_to_human", 0.50)

    # awake/sleep_recovered + chat 200/None
    note = ("All checks passed - server healthy."
            if server_status == "awake" else
            "Server was sleeping (cold start) and woke up - normal for the "
            "free tier, no action needed.")
    return _rule(LAST_PROBES, note, "no_action", 1.0)