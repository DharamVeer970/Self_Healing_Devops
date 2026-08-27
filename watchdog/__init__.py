"""PORTFOLIO WATCHDOG - the Self-Healing Agent pointed at a remote service.

Same MONITOR -> DIAGNOSE -> SAFETY -> REMEDIATE -> VERIFY -> REPORT loop as
the local agent, but the "senses" are remote:

    local agent:   tail logs/app.log       -> fix via subprocess
    watchdog:      HTTP probes + Render API -> restart via Render API

What it can heal by itself (allowlisted):
    * server crashed (persistent 503 / timeouts)  -> Render redeploy
    * chatbot 502 right after a server restart    -> Render redeploy
What it will NOT do (safety gate + honesty):
    * restart on rate-limit (429) - restart never helps, escalate instead
    * pretend a fix worked - VERIFY polls until the app really answers 200

Env (loaded from .env by agent/__init__.py - never hardcoded):
    RENDER_API_KEY     Render API token        (secret)
    RENDER_SERVICE_ID  srv-... of the service  (not secret)
"""
