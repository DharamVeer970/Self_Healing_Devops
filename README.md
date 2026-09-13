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
            |        service/flaky_app.py         |
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
            +--------------+            +------------------+
            |  REMEDIATE   |            |    ESCALATE      |
            | restart /    |            | (script-writer   |
            | rotate logs  |            |  -> fixes/, then |
            +------+-------+            |  human review)   |
                   |                    +------------------+
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
            +--------------+               (retry: go
            |    REPORT    |                back to
            | human summary|               DIAGNOSE)
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
│   ├── script_writer.py    # ESCALATE: LLM-authored fixes -> fixes/ (plus
│   │                       #           opt-in source patching, CI-safe)
│   ├── url_checker.py      # OBSERVE+: URL connectivity state-change watcher
│   ├── graph.py            # LANGGRAPH engine (--graph): cyclic state machine
│   ├── reporting.py        # REPORT: shared incident summary for both engines
│   └── notify.py           # DELIVER: Slack webhook + SMTP email push
├── service/flaky_app.py    # the monitored app (simulated production service)
├── watchdog/               # Portfolio Watchdog: same loop -> real Render app
│   ├── monitor.py          # HTTP probes (retries, cold-start awareness)
│   ├── diagnose.py         # decision rules (restart / escalate / no action)
│   ├── remediate.py        # Render API restart + deploy verification
│   ├── render_logs.py      # pulls + renders the Render log stream
│   └── run.py              # one cycle: probe -> diagnose -> (restart) -> report
├── tests/                  # hermetic pytest suite (tmp dirs, no secrets/network)
├── fixes/                  # LLM-generated fix scripts land here for review
├── logs/                   # app.log (+ .offset for tail position)
├── .github/workflows/      # ci.yml, watchdog.yml, self-heal.yml, heal-agent.yml
├── requirements.txt        # langgraph + notification deps (runtime = stdlib)
├── pytest.ini              # pytest config (offline, sandboxed)
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
| `python main.py --check-urls` | Run one URL connectivity check, print status, exit |

`--graph` combines freely with the mode flags, e.g.
`python main.py --graph --auto --once`.
(`--check-urls` is standalone — it exits immediately.)
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

## ✍️ Script-writer agent (escalation handler)

When the rule engine can't fix an issue (unknown action, blocked action, or a
failed verification), the script-writer agent (`agent/script_writer.py`) takes
over instead of giving up:

1. **LLM-generated fix scripts** — a custom, idempotent Python fix script is
   written to `fixes/` for human review. Nothing runs automatically.
2. **Opt-in source patching** — locally only, the agent can insert a logging
   setup block directly into unlogged modules. Off by default everywhere and
   **forced off on GitHub Actions runners** — a CI run must never dirty the
   repo or rewrite tracked `.py` files.

| Variable | Default | Meaning |
|---|---|---|
| `SCRIPT_FIX_DIR` | `<repo>/fixes` | where generated fix scripts land |
| `SCRIPT_DRY_RUN` | `0` | `1` = show what *would* be written, write nothing |
| `SCRIPT_PATCH_SOURCE` | `0` | `1` = allow direct source patching **locally** |
| `ALLOW_SOURCE_PATCH_IN_CI` | `0` | `1` = CI override for throwaway sandbox jobs |

No LLM key? It degrades to a deterministic offline patch and still writes
fix scripts — the agent never crashes on a missing key.

---

## 🌐 URL connectivity checker

Every cycle the agent re-probes the URLs configured in `.env`
(`WATCHDOG_APP_URL`, `WATCHDOG_FRONTEND_URL`,
`WATCHDOG_PORTFOLIO_BACKEND_URL`) and reports state changes — DOWN and
RECOVERED — through the normal report channels. Tune with `CHECK_INTERVAL`
(seconds between checks) and `CHECK_TIMEOUT`, or run a standalone check:

```bash
python main.py --check-urls
```

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

`.github/workflows/ci.yml` runs on every push and pull request:

1. install `requirements.txt` (+ `pytest` as a dev dependency)
2. compile-check all sources (`python -m compileall`)
3. run the full hermetic test suite (`python -m pytest tests/ -q`)

The tests are sandboxed (tmp dirs, no secrets, no network), so CI never
touches real services or publishes anything. Three more workflows exist:
`watchdog.yml` (scheduled Portfolio Watchdog), `self-heal.yml` (runs the
agent itself every 6 hours), and `heal-agent.yml` (its reusable runner).

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
| `POST /chat` returns 502 (only if chat probe enabled) | one restart attempt; verification reports honestly if the upstream provider itself is down |
| `POST /chat` returns 429 (only if chat probe enabled) | **escalate only** — a restart never fixes rate-limiting |
| Everything healthy | one-line "all clear" |

**Token-aware by default.** The server liveness check (`GET /`) is free and runs
every cycle. The `/chat` probe bills your chat + reranker providers, so it is
**off unless you set `WATCHDOG_CHAT_PROBE=1`** (in `.env`, or as the repo secret
the workflow forwards). Leave it off and the watchdog still detects every crash
and heals it — it just won't spend tokens confirming the chatbot on calm cycles.


Files: `watchdog/monitor.py` (HTTP probes), `watchdog/diagnose.py`
(decision rules), `watchdog/remediate.py` (Render API restart + deploy
verification), `watchdog/render_logs.py` (Render log stream),
`watchdog/run.py` (cycle + reporting through `agent.reporting` /
`agent.notify`).

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
2. ✅ Port the loop to **LangGraph** (cyclic state machine, conditional edges)
3. ✅ Slack/email delivery in the REPORT node
4. ✅ Point the loop at a real deployed service (Portfolio Watchdog on Render)
5. ✅ Script-writer agent: LLM-authored fix scripts on escalation
6. Metrics-based detection (latency %iles, error rates), not just log text
7. Fix-script auto-review: syntax-check + dry-run LLM-generated scripts before
   a human opens them
