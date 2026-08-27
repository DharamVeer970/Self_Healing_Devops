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
            |        demo_env/flaky_app.py        |
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
├── tests/                  # hermetic pytest suite (sandboxed, offline-safe)
├── demo_env/flaky_app.py   # simulated flaky server (replaces Docker)
├── logs/                   # app.log (+ .offset for tail position)
├── .github/workflows/ci.yml# GitHub Actions: pytest on push/PR
├── requirements.txt        # langgraph + test deps (runtime core = stdlib)
├── .env.example            # template: LLM keys + notification channels
├── .env                    # YOUR API keys live here (never commit/publish)
└── seed_test.py            # instantly fabricate an incident for testing
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
python demo_env/flaky_app.py
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

**Shortcuts:** `python seed_test.py 1100` force-seeds an OOM incident;
`python stop_demo.py` kills leftover background demo processes.

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

## ✅ Testing

The test suite lives in `tests/` and is fully hermetic — it runs against a
temporary sandbox (no real logs touched, `.env` keys stripped, no network),
so you can run it anytime:

```bash
python -m pytest                 # whole suite (~0.4s)
python -m pytest tests/test_safety.py    # one module
python -m pytest -k "escalat"    # tests matching a keyword
```

What each module covers:

| Test file | Verifies |
|---|---|
| `tests/test_diagnose.py` | every log signature maps to the right fix + confidence |
| `tests/test_diagnose_llm.py` | provider selection, offline fallback (never calls out) |
| `tests/test_monitor.py` | byte-exact tailing, partial-line safety, truncation reset |
| `tests/test_remediate.py` | fixes mutate only simulated state; verify() honesty |
| `tests/test_agent_loop.py` | full MONITOR → REPORT cycle incl. approve/decline/escalate |
| `tests/test_graph.py` | LangGraph engine: healing, escalation, retry-loop exit |
| `tests/test_notify.py` | Slack/email delivery + failure degradation |

For coverage (`pip install pytest-cov`):

```bash
python -m pytest --cov=agent --cov=main --cov-report=xml --cov-report=term
```

A quick end-to-end smoke run:

```bash
python seed_test.py          # fabricate disk-full incident
python main.py --once --auto # agent must heal + report
```

---

## 🔍 CI (GitHub Actions)

`.github/workflows/ci.yml` runs on every push and pull request:

1. install `requirements.txt`
2. compile-check all sources
3. `pytest` with coverage (`--cov`) + upload of `coverage.xml` artifact

No static-analysis gate is configured for now — only real tests decide
green/red.

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
