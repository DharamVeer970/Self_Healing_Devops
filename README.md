# 🩺 Self-Healing DevOps Agent

An **agentic AI** project: the agent watches a service, detects failures,
figures out *why* they happened, repairs them, verifies the repair worked, and
reports to a human — on repeat, forever.

Runs entirely on a low-spec laptop. **No Docker. No hardcoded secrets.**

---

## 🧠 What does "self-healing" mean?

A **self-healing system** recovers from faults on its own, without a human
pressing buttons:

| Stage | Question it answers |
|---|---|
| **Observe** | "Is anything wrong right now?" |
| **Diagnose** | "*Why* is it wrong?" |
| **Decide** | "Is it safe for me to fix this myself?" |
| **Heal** | "Apply the fix." |
| **Verify** | "Did the fix actually work?" (if not → diagnose again) |
| **Report** | "Tell a human what happened." |

Real-world examples: Kubernetes restarting dead containers, cloud autoscalers
replacing unhealthy VMs. This project implements that cycle as an **AI agent** —
the LLM writes the root-cause report; deterministic code does the rest.

---

## 🏗️ Architecture

The agent is a loop of small nodes. The LLM *suggests*; deterministic code
*decides and acts*. That separation is what makes AI autonomy safe.

```
            +-------------------------------------+
            |        service/flaky_app.py        |
            |  (fake server -> logs/app.log)      |
            +------------------+------------------+
                               |
                               | writes log lines
                               v
            +-------------------------------------+
            |              MONITOR                |
            |          tails logs/app.log         |
            +------------------+------------------+
                               |
                               | new ERROR / FATAL lines
                               v
            +-------------------------------------+
            |              DIAGNOSE               |
            |       rule engine + LLM summary     |
            +------------------+------------------+
                               |
                               v
            +-------------------------------------+
            |               SAFETY                |
            |       allowlist / blocklist gate    |
            +------------------+------------------+
                               |
             allowed?          |          blocked
              +----------------+----------------+
              |                                 |
              v                                 v
            +--------------+            +--------------+
            |  REMEDIATE   |            |   ESCALATE   |
            | restart /    |            | (to a human) |
            | rotate logs  |            +--------------+
            +------+-------+
                   |
                   v
            +-------------------------------------+
            |               VERIFY                |
            |       post-fix health check         |
            +------------------+------------------+
                               |
              fixed?           |        still broken
              +----------------+----------------+
              |                                 |
              v                                 v
            +--------------+            (retry: go
            |    REPORT    |             back to
            | human summary|             DIAGNOSE)
            +--------------+
```

### File map

```
Self_Healing_Devops/
├── main.py                 # agent entry point — wires the loop together
├── agent/
│   ├── __init__.py         # auto-loads .env into environment variables
│   ├── monitor.py          # OBSERVE : tails logs/app.log, extracts errors
│   ├── diagnose.py         # DIAGNOSE: rule classifier + env-key LLM report
│   ├── safety.py           # DECIDE  : action allowlist / permanent blocklist
│   ├── remediate.py        # HEAL + VERIFY: fixes and post-fix health check
│   ├── graph.py            # LANGGRAPH engine (--graph): cyclic state machine
│   ├── reporting.py        # REPORT: shared incident summary for both engines
│   └── notify.py           # DELIVER: Slack webhook + SMTP email push
├── service/flaky_app.py    # the monitored app (simulated production service)
├── logs/                   # app.log (+ .offset for tail position)
├── .github/workflows/ci.yml# GitHub Actions: compile-check on push/PR
├── requirements.txt        # langgraph + notification deps (runtime = stdlib)
├── .env.example            # template: LLM keys + notification channels
└── .env                    # YOUR API keys live here (never commit/publish)
```

### Key design rule

> **`diagnose.py` decides WHAT to do · `safety.py` decides WHETHER it's
> allowed.** An unrecognized or dangerous action never executes — the agent
> escalates to a human instead.


---

## 🚀 Running it

Open two terminals in the project folder:

**Terminal 1 — start the fake broken server**
(it leaks memory until it crashes, all by itself):

```bash
python service/flaky_app.py
```

**Terminal 2 — start the agent:**

| Command | Behavior |
|---|---|
| `python main.py` | Suggest-only mode: asks `[y/N]` before every fix |
| `python main.py --auto` | Autonomous mode: applies allowlisted fixes silently |
| `python main.py --once` | Run exactly one scan cycle and exit |
| `python main.py --graph` | Use the **LangGraph** engine (`agent/graph.py`) |

`--graph` combines freely with the flags above, e.g.
`python main.py --graph --auto --once`.
If `langgraph` is not installed it falls back to the plain loop.

---

## 🕸️ LangGraph engine

The same agentic loop modeled as a real cyclic state machine
(`pip install langgraph`, included in `requirements.txt`):

```
monitor -> diagnose -> safety -> remediate -> verify -+
    ^                                                 |
    +--------- (still broken, retries remain) --------+
                  | healthy / escalation / retries spent
                  v
               report  (console + Slack/email)
```

- State is a typed dict (`AgentState`); every node is a small function,
  so each one is unit-testable in isolation.
- Conditional edges implement SAFETY escalation and the VERIFY retry loop
  (`MAX_ATTEMPTS = 3`), then a final honest report.
- Reports are produced by `agent.reporting`, shared verbatim with the
  plain engine — both engines always look identical to a human.

---

## 📣 Slack / email delivery

The REPORT node pushes every incident to humans via `agent.notify`:

| Channel | Required `.env` variables |
|---|---|
| Slack   | `SLACK_WEBHOOK_URL` |
| Email   | `EMAIL_SMTP_HOST`, `EMAIL_SMTP_PORT`, `EMAIL_USERNAME`, `EMAIL_PASSWORD`, `EMAIL_TO` (+ optional `EMAIL_FROM`) |

See `.env.example` for a template. Nothing configured → delivery is
skipped; network/auth failures degrade to a printed note instead of ever
crashing the agent loop.

---

## 🔍 CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every push and pull request, and
compile-checks all source files so a syntax regression fails the build:

1. install `requirements.txt`
2. compile-check all sources (`python -m compileall`)

No static-analysis or test gate is configured for now.

---

## 🛡️ Portfolio Watchdog (real-world healing)

The same agent loop, pointed at a **real deployed service** — the FastAPI
backend behind [the portfolio](https://dharamveer970.github.io/Portfolio-my/)
(hosted on Render's free tier):

```
python -m watchdog.run --once --auto
```

What it monitors and decides:

| Finding | Action |
|---|---|
| `GET /` fails on every retry (503/timeout) | **restart via Render API** → verify deploy is `live` → verify the app answers 200 again |
| First probe fails but a retry succeeds | free-tier cold start (server was sleeping) — **no action**, logged as normal |
| `POST /chat` returns 502 | one restart attempt; verification reports honestly if the upstream provider itself is down |
| `POST /chat` returns 429 | **escalate only** — a restart never fixes rate-limiting |
| Everything healthy | one-line "all clear" |

Files: `watchdog/monitor.py` (HTTP probes), `watchdog/diagnose.py`
(decision rules), `watchdog/remediate.py` (Render API restart + deploy
verification), `watchdog/run.py` (cycle + reporting through
`agent.reporting` / `agent.notify`).

Run it on a schedule with `.github/workflows/watchdog.yml` (every 20
minutes, plus a manual "Run workflow" button). Repository secrets needed:
`RENDER_API_KEY`, `RENDER_SERVICE_ID`, and optionally the Slack/email keys
for delivery.

---

## 🔑 LLM setup (optional)

Credentials come **only from the environment** — nothing is hardcoded in
source. Keys are read from real environment variables, with the project
`.env` file (auto-loaded at startup) as a convenient fallback:

1. `TOKENROUTER_API_KEY` — used first if present
2. `OPENROUTER_API_KEY` — fallback; endpoint
   `https://openrouter.ai/api/v1/chat/completions`

Optional overrides: `LLM_MODEL` (default `openai/gpt-4o-mini`),
`TOKENROUTER_BASE_URL`.

Just put one line in your `.env` file and run — no shell setup needed:

```
TOKENROUTER_API_KEY=your_key_here
# or
# OPENROUTER_API_KEY=sk-or-v1-...
# LLM_MODEL=meta-llama/llama-3.3-70b-instruct:free
```

Without any key the agent still works 100% offline via its rule engine.
⚠️ Never commit or share your `.env`; add it to `.gitignore` if you use Git.

---

## 📋 Incident playbook

| Log signature | Diagnosis | Auto-fix | Confidence |
|---|---|---|---|
| `No space left on device` | Disk full | rotate_logs | 97% |
| `OutOfMemoryError` + crash | Memory leak killed the service | restart_service | 93% |
| `502 Bad Gateway` / upstream refused | Backend down | restart_service | 90% |
| Anything else | Unknown | **escalate to human** | — |

Verification re-checks service state after every fix; if something is still
broken, the report says so explicitly instead of pretending success.

---

## 🗺️ Roadmap

1. ✅ Observe → diagnose → heal → verify → report loop (this repo)
2. Port the loop to **LangGraph** (cyclic state machine, conditional edges)
3. Replace the simulator with a real target over SSH (`paramiko`) or a
   webhook into your own app
4. Slack/email delivery in the REPORT node
5. Metrics-based detection (latency %iles, error rates), not just log text
