# The Shepherd

> *The agent is the part you can't trust. The Shepherd is the layer that lets you trust it anyway.*

AI desktop agents can click, type, fill forms, send emails, and submit data — without asking. **The Shepherd is the governance layer that makes them safe to deploy.** Not by slowing them down. By putting configurable oversight, a tamper-proof audit trail, and a human decision gate between their intent and your machine.

**Author an agent by demonstrating a task once. Watch it run live. Catch it when it strays. Replay exactly what it did while you were gone.**

---

## The Demo in 90 Seconds

```
You say: "fill out the application form"
  ↓
Deepgram transcribes your voice → Shepherd builds Intent
  ↓
Router matches "fill out application" → ROUTINE_FORM_FILL (0.886 confidence)
  ↓
Agent S reads the screen, plans which field gets which value
  ↓
pyautogui fills 6 fields — deliberate, human-legible speed
  ↓
Step 4: monitor detects "Password" field via OCR
  ↓
  ┌── Policy Engine (rule-based, <1ms): HALT ──────────────────────────┐
  │   screen_rule "no-credentials" matched                             │
  │   "Credential field detected"                                      │
  └────────────────────────────────────────────────────────────────────┘
  ↓
Control Hub lights up red. Judge sees: reason, step, verifier verdict.
  ↓
Judge approves or stops.
  ↓
Every action — including the halt — signed into the hash-chain audit log.
```

**Change a rule in `policy.yaml`. Re-run. Behavior changes immediately. That's the difference between a demo and a product.**

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              INPUT LAYER                                    │
│   Voice → Deepgram STT → transcript                                         │
│   Text  → typed directly                                                    │
│                  ↓ Intent { raw_text, timestamp }                           │
└─────────────────────────────────────────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                            INTENT ROUTER                                    │
│   1. Redis VSET semantic search — BAAI/bge-small-en-v1.5 (local, no API)   │
│   2. Keyword fallback (deterministic — a wrong route moves the real mouse)  │
│                  ↓ ResolvedRoutine { routine_id, variables, confidence }    │
└─────────────────────────────────────────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                           EXECUTION ENGINE                                  │
│   LIVE mode:   Agent S screenshots → plans batch_fill → pyautogui acts     │
│   LOCKED mode: deterministic pre-mapped steps → pyautogui acts             │
│   Both: pyautogui.PAUSE=0.3s — deliberate, watchable, human-legible        │
└─────────────────────────────────────────────────────────────────────────────┘
                   ↓ (at high-stakes step boundaries only — never mid-click)
┌─────────────────────────────────────────────────────────────────────────────┐
│                         3-LAYER OVERSIGHT STACK                             │
│                                                                             │
│  Layer 1 — Policy Engine (rule-based, always-on, <1ms)                     │
│    · Evaluates data/policy.yaml OCR rules on each screenshot                │
│    · Planted triggers: credential / captcha / phishing / payment            │
│    · App containment: only allowed_apps may launch                          │
│    · Domain containment: only allowed_domains may be visited                │
│    · Rate limiting: max_actions_per_minute, max_steps_per_run               │
│                                                                             │
│  Layer 2 — Independent Verifier (Claude Haiku, on "flag" only)             │
│    · Separate model, separate context, separate prompt                      │
│    · Upgrades flag→halt when confident; downgrades flag→ok on false alarms │
│    · Never overrides a "halt" from Layer 1 — that verdict is already final  │
│                                                                             │
│  Layer 3 — Human Approval Gate                                             │
│    · Any surviving flag blocks execution, streams alert to Control Hub      │
│    · Human approves, halts, or overrides with a natural-language note       │
│    · Spoken "stop" via Deepgram fires the same halt path                   │
└─────────────────────────────────────────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TAMPER-EVIDENT AUDIT LOG                            │
│   SHA-256 hash chain — every action appended to data/audit.jsonl           │
│   Each entry stores hash(prev_entry || this_entry)                          │
│   Modify one byte anywhere → GET /api/audit/verify pinpoints the entry     │
└─────────────────────────────────────────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                        OBSERVABILITY + MEMORY                               │
│   Arize Phoenix  — OTel spans at routine.execute → action.N level          │
│   Redis          — vector routing · replay memory · semantic cache          │
│   RoutineEvolution — auto-promotes risky steps to monitored over time       │
└─────────────────────────────────────────────────────────────────────────────┘
                   ↓
┌─────────────────────────────────────────────────────────────────────────────┐
│                          CONTROL HUB  :8765                                 │
│   Next.js frontend — live WebSocket state, approval gate, replay panel     │
│   Command Center · Routines · Runs · Interventions · Audit Log · Policy    │
│   Mode toggle LIVE/LOCKED/AUTONOMOUS — sidebar, no restart needed          │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Why Demonstration, Not a Node-Graph

Every comparable tool — n8n, Zapier, Make — asks you to build a flowchart. **We ask you to just do the task once.**

The agent works in the exact same workspace the human does. Demonstrating the task once *is* the authoring step — the demonstration IS the routine. We render the result as a DAG for oversight and replay, but the human authors by doing, not by diagramming.

```bash
# Record a demonstration:
python main.py --record ROUTINE_FORM_FILL
# Cmd+Shift+M to mark each step boundary. Cmd+Shift+Q to stop.
# The recorded run becomes RoutineDefinition.demonstration.
# Agent S uses it in LIVE mode; LOCKED mode ignores it and uses verbatim steps.
```

**Judge Q&A answer:** "Other tools make you build the flowchart; we let you just do the task once and the agent learns it."

---

## Sponsor Integrations

### Arize Phoenix — Observability Track

Every routine run emits a full OpenTelemetry span tree. Spans at `routine.execute → action.N` level. Attributes: `routine.id`, `action.type`, `action.target`, `deviation`, `duration_ms`, `routine.status`.

```bash
uv run phoenix serve   # → http://localhost:6006
uv run python main.py  # spans appear as they execute
```

**Evidence it improved the build:** Phoenix traces caught a mis-mapped coordinate (`submit_button` resolving to off-screen `{x:0, y:0}`) that caused silent click failures. The span showed `action.target=submit_button`, `duration_ms=0`, `deviation=null` — the timing anomaly flagged the coordinate bug before it could be triggered in a live demo.

View in Phoenix or the **Traces** tab in the Control Hub. Phoenix is the developer-side instrument; the Control Hub is the user-facing product — keep them visually separate during judging.

---

### Sentry — Error Monitoring Track

Any exception inside `engine.execute()` is captured with full context (routine_id, step_index, action type, variables) before returning `status="failed"`. The engine **never raises** — Sentry captures, the demo continues.

```python
# telemetry/sentry_init.py
sentry_sdk.init(dsn=SENTRY_DSN, traces_sample_rate=0.0, send_default_pii=False)
```

Add `SENTRY_DSN` to `.env` → every caught failure shows in your Sentry dashboard with the full step context attached.

---

### Deepgram — Voice AI Track

```bash
# Voice input
"fill out the application form"
→ Deepgram STT (nova-2 model) → transcript → Intent → router

# Spoken halt
"stop"
→ Deepgram listen_for_stop_command() → halt_flag set → engine stops at next boundary
```

Voice is called **only before Intent is built** — never inside or between routine steps. Typed input fallback is always active. Gate: `FEATURES["deepgram"]` — if mic permission is missing or key is absent, Shepherd falls through to keyboard input with zero disruption.

macOS: grant **Microphone** in System Settings → Privacy & Security.

---

### Band — Multi-Agent Track

Three agents collaborating via Band boundary messages:

| Agent | Role |
|---|---|
| **Router agent** | Publishes `routine.start` with `ResolvedRoutine` to Band room |
| **Engine agent** | Subscribes, receives ONE start message, executes ENTIRE click sequence locally with zero Band involvement, publishes `routine.complete` with `ExecutionResult` |
| **Telemetry agent** | Subscribes to completion messages, logs to Redis + Arize |

**Design constraint (non-negotiable):** zero Band calls inside the click sequence. Latency there could fire a click at the wrong moment on a live screen. Fire-and-forget at boundaries only.

Gate: `FEATURES["band"]` — toggled off, the demo runs identically.

---

### Browserbase — Browser Automation Track

The `ROUTINE_BROWSER_SHOWPIECE` routine uses Browserbase to provision a cloud browser session and perform a real web action (navigate, click, read back a value) — a genuine net-new capability the agent wouldn't otherwise have.

```python
# services/browserbase_routine.py
bb = Browserbase(api_key=BROWSERBASE_API_KEY)
session = bb.sessions.create(...)
# Playwright drives the remote session
page.goto(url); page.click(selector)
```

Fallback: if `FEATURES["browserbase"]` is False or network is down, the routine swaps to a local equivalent. **This is stated honestly during the demo** — the fallback is the floor, not the product.

---

### Redis — Memory + Semantic Routing

**Semantic routing:** Redis 8 VSET (`VADD`/`VSIM`) with BAAI/bge-small-en-v1.5 embeddings (384-dim, local, no API key). Every routine intent is vectorized on startup; every query is matched at sub-millisecond latency.

```
"submit my application"    → ROUTINE_FORM_FILL       (0.886)
"fill out the intake form" → ROUTINE_FORM_FILL       (0.871)
"open a browser tab"       → ROUTINE_BROWSER_SHOWPIECE (0.793)
```

**Replay memory:** every run is stored as a full `ReplayRecord` (step timing, variables, status, errors) in Redis. The Control Hub replay panel loads any past run and reconstructs the execution DAG node by node.

```bash
brew install redis && brew services start redis
```

---

### Orkes / Agentspan — Workflow Orchestration

Track is judged on Agentspan. Build only after VERIFY Saturday — if Agentspan maps cleanly to wrapping the agent run, a thin `services/orkes_workflow.py` wrapper is ready to wire. If it doesn't map cleanly, this integration is dropped entirely per the spec. No Conductor workflow is built.

---

### Anthropic / Claude Code — AI Dev Tools Track

The entire codebase was built with Claude Code. Session history is curated in `.claude/`. The router optionally uses Claude as a reasoning layer for fuzzy intent resolution — gated behind a flag, with the deterministic keyword matcher always retained as the demo path and fallback.

---

### Cognition / Devin — AI Engineer Track

Enter only if a teammate genuinely used Devin to build a self-contained piece (test harness, coordinate-calibration tool, rollback tooling) with session history to show. Do not claim usage you can't demonstrate.

---

## What Makes This Different

| Capability | Shepherd | Typical agent framework |
|---|---|---|
| Intercepts at sub-step boundaries | ✅ before actuation | ❌ runs to completion |
| Policy-as-code governance | ✅ `data/policy.yaml`, hot-reload | ❌ hardcoded or none |
| Two independent oversight layers | ✅ rules + Haiku verifier | ❌ single model |
| Tamper-evident audit trail | ✅ SHA-256 hash chain | ❌ mutable logs |
| App + domain containment sandbox | ✅ allowlists + rate limits | ❌ unrestricted |
| Works fully offline | ✅ LOCKED mode, zero API calls | ❌ API-dependent |
| Deterministic halt path | ✅ zero LLMs in halt path | ❌ model-dependent |
| Human override with instruction | ✅ note → Agent S replans | ❌ binary stop/go |
| Authored by demonstration | ✅ record once, agent runs | ❌ build flowchart |
| Replay any past run | ✅ Redis DAG replay | ❌ logs only |

---

## Governance Policy

Edit `data/policy.yaml`. No restart needed — the policy reloads on every evaluation.

```yaml
screen_rules:
  - name: no-credentials
    match_text: ["password", "api key", "ssn"]
    action: halt
    reason: "Credential field detected"

  - name: suspicious-payment
    match_text: ["confirm payment", "credit card number"]
    action: flag        # → Haiku verifier → human gate
    reason: "Payment authorization detected"

triggers:
  credential: halt      # planted demo trigger — 100% reliable
  captcha:    halt
  payment:    flag

containment:
  allowed_apps: ["Google Chrome", "Safari"]
  allowed_domains: ["localhost", "workbridge.com"]
  max_actions_per_minute: 60
  max_steps_per_run: 100
```

---

## Audit Log

```bash
# Verify the entire chain
curl http://localhost:8765/api/audit/verify
# → {"valid": true, "entries": 47, "tampered_at": null, "reason": "chain intact"}

# View recent entries
curl http://localhost:8765/api/audit
```

Every entry stores `hash(prev_hash || this_entry_json)`. Modify one byte anywhere — the verify endpoint pinpoints the exact entry. O(n) walk, no external dependency.

---

## Execution Modes

| Mode | Description |
|---|---|
| `LIVE` | Agent S screenshots → plans batch_fill → pyautogui acts. One Claude vision call. |
| `LOCKED` | Deterministic pre-mapped steps. Zero API calls. Works at a dead venue. |
| `AUTONOMOUS` | No human gate — runs to completion unless policy halts. |

```bash
# Switch at runtime (no restart):
curl -X POST http://localhost:8765/api/mode/LOCKED
curl -X POST http://localhost:8765/api/mode/LIVE
# Or use the mode toggle in the Control Hub sidebar.
```

---

## Quick Start

Requires [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd

# Install
uv sync

# Configure
cp .env.example .env
# Set ANTHROPIC_API_KEY at minimum. All other keys degrade gracefully if absent.

# Start Redis
brew install redis && brew services start redis

# Start Arize Phoenix (developer traces)
uv run phoenix serve &     # → http://localhost:6006

# Run Shepherd
uv run python main.py

# Control Hub
open http://localhost:3000   # Next.js frontend (npm run dev in frontend/)
# or:
open http://localhost:8765   # FastAPI dashboard
```

Say or type: **"fill out the application form"** — watch it run, and halt at the password field.

---

## Persistent Backend (dashboard separate from agents)

By default the dashboard/API runs **inside** the agent process, so it stops when the
agent exits. To keep the backend up across many agent runs — so you can browse graphs,
runs, replays, policy, and the audit log at any time — run it as its own process and
point agents at it with `BACKEND_URL`.

```bash
# Terminal 1 — persistent backend (stays up across agent runs)
uv run python -m dashboard.server          # → http://localhost:8765

# Terminal 2 — frontend (optional; live + historical views)
cd frontend && npm run dev                 # → http://localhost:3000

# Terminal 3+ — run agents, streaming their events to the backend
BACKEND_URL=http://localhost:8765 uv run python main.py
# (or set BACKEND_URL=http://localhost:8765 in .env)
```

- The agent **streams events** (start/step/halt/complete, task-graph nodes, monitor
  alerts) to `POST /api/ingest`, which re-broadcasts them to every connected dashboard.
- Forwarding is **off the click path** (queued + sent by a daemon worker) and
  **best-effort**: if the backend is down, the agent never blocks — the run still lands
  on disk (`data/task_graphs.json`, runs DB) and appears in the backend's REST views.
- When `BACKEND_URL` is set the agent does **not** bind port 8765 itself, so you can run
  several agents against one backend.
- Leave `BACKEND_URL` unset for the original all-in-one behavior (in-process dashboard).

| Layout | Command | When |
| --- | --- | --- |
| All-in-one | `uv run python main.py` | quick single run; dashboard dies with the agent |
| Persistent backend | `uv run python -m dashboard.server` + `BACKEND_URL=… uv run python main.py` | keep graphs/runs visible across many agent runs |

---

## Setup for LIVE Mode (Agent S + Ollama, fully offline)

```bash
# Install Ollama + vision model (~4 GB — do this before the venue)
brew install ollama
ollama pull qwen2.5-vl:7b

# Install Agent S extras
uv sync --extra agent_s

# Install Tesseract (OCR for monitor)
brew install tesseract
```

`.env`:
```
AGENT_S_ENGINE_TYPE=openai
AGENT_S_MODEL=qwen2.5-vl:7b
AGENT_S_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```

Then `ollama serve` before starting Shepherd.

---

## macOS Permissions

Grant in **System Settings → Privacy & Security** for the terminal running Shepherd:

| Permission | Required for |
|---|---|
| **Accessibility** | pyautogui mouse + keyboard control |
| **Screen Recording** | screenshots for monitor agent + Agent S vision |
| **Microphone** | Deepgram voice input (optional) |

Chrome also needs **View → Developer → Allow JavaScript from Apple Events** — Shepherd auto-enables this on first form-fill run.

---

## Project Layout

```
engine/
  engine.py            Execution engine — LIVE + LOCKED, containment, audit wiring
  agent_s_adapter.py   Agent S wrapper + Claude vision batch_fill planning
  routines.py          Routine loader
  recorder.py          Demonstration recorder (Cmd+Shift+M to mark steps)
  coords.py            Coordinate map loader + Retina scaling helpers
  task_graph.py        Milestone-level persistent task memory
  approvals.py         Human approval gate (blocking)

router/
  router.py            Semantic vector search + keyword fallback
  vector_router.py     Redis 8 VSET (VADD/VSIM) + fastembed local embeddings
  registry.py          Routine keyword/variable registry

services/
  monitor_agent.py     Rule-based step monitor (planted triggers + OCR + stuck-state)
  policy_engine.py     Loads + evaluates data/policy.yaml — hot reload
  verifier.py          Independent Haiku second-opinion verifier
  deepgram_input.py    Voice STT — transcribe + spoken-stop halt path
  embeddings.py        Shared local embedding model (bge-small)
  semantic_cache.py    Redis vectorset LLM response cache
  band_boundary.py     Band multi-agent boundary messaging
  browserbase_routine.py  Cloud browser action (Browserbase + Playwright)

telemetry/
  audit_log.py         Tamper-evident SHA-256 hash-chain audit log
  telemetry.py         Arize Phoenix OTel spans (register() pattern)
  memory.py            Redis ReplayRecord store + retrieval
  evolution.py         Per-step stats, auto-promote risky steps
  sentry_init.py       Sentry error capture init

dashboard/
  server.py            FastAPI — WebSocket + REST API + CORS
  events.py            Local event bus (no WiFi dependency)
  deepgram_routes.py   /api/deepgram/* endpoints

frontend/              Next.js Control Hub
  src/app/
    command-center/    Live execution + monitor alert + approval gate
    audit/             Hash-chain integrity viewer + entry table
    policy/            Live policy.yaml viewer
    interventions/     Pending + resolved intervention queue
    routines/          Routine list + detail
    runs/              Run history + per-step detail
    voice-lab/         Deepgram voice testing

data/
  policy.yaml          Governance policy (edit → hot reload)
  routines.json        Routine definitions + steps
  coords.demo.json     Coordinate map for demo display
  audit.jsonl          Tamper-evident action log (auto-created)
  demo_form.html       Local demo form at localhost:8765/demo-form
```

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /api/status` | Engine state: mode, step, run_id, status |
| `POST /api/mode/{LIVE\|LOCKED\|AUTONOMOUS}` | Switch execution mode (no restart) |
| `POST /api/control/{approve\|halt\|override}` | Human decision at approval gate |
| `GET /api/audit` | Most recent audit log entries |
| `GET /api/audit/verify` | Verify SHA-256 hash chain integrity |
| `GET /api/policy` | Current governance policy (parsed YAML) |
| `GET /api/runs` | Recent execution history |
| `GET /api/runs/{run_id}` | Single run detail with per-step timing |
| `GET /api/routines` | All routines |
| `GET /api/routines/{id}` | Routine definition + step list |
| `GET /api/interventions` | Pending + resolved interventions |
| `POST /api/interventions/{id}` | Resolve an intervention (approve/reject) |
| `GET /api/agents` | Active agent state |
| `WS /ws` | Live event stream to Control Hub |

---

## Day-Of Checklist

**Before you leave for the venue:**
- [ ] `ollama pull qwen2.5-vl:7b` completed (~4 GB)
- [ ] `ROUTINE_FORM_FILL` demonstration recorded (`python main.py --record ROUTINE_FORM_FILL`)
- [ ] LOCKED mode runs the full routine + policy halt cleanly, offline
- [ ] LIVE mode: Agent S initializes, batch_fill works on one real form
- [ ] `GET /api/audit/verify` returns `{"valid": true}`
- [ ] 5-minute run-of-show rehearsed end to end, twice

**At the venue:**
- [ ] Redis running (`brew services start redis`)
- [ ] `ANTHROPIC_API_KEY` in `.env`
- [ ] Accessibility + Screen Recording permissions granted for this terminal
- [ ] Chrome "Allow JavaScript from Apple Events" — auto-enabled on first run
- [ ] Coordinates re-calibrated on venue display resolution (`SCREEN_WIDTH`/`SCREEN_HEIGHT` in `.env`)
- [ ] `uv run phoenix serve` running at http://localhost:6006 (right monitor, dev-side)
- [ ] `npm run dev` running in `frontend/` at http://localhost:3000 (judge-facing)
- [ ] Test: say "fill out the application form" → confirm halt at step 4
- [ ] Test: modify one rule in `policy.yaml` → confirm behavior changes without restart
- [ ] Test: `GET /api/audit/verify` → `{"valid": true}`
- [ ] Hardware fallback recording ready if Agent S flakes

**Demo script (5 minutes):**
1. **(30s)** "The problem: agents can click, type, submit — without asking." Show the form.
2. **(60s)** Say the trigger phrase. Watch Agent S fill 3 fields live.
3. **(60s)** Monitor halts at password field. Show the Control Hub alert — reason, step, verifier verdict.
4. **(30s)** Show `policy.yaml`. Change a rule. Re-run. Behavior changes instantly.
5. **(30s)** Show `GET /api/audit/verify` → chain intact. Tamper one line → chain broken, exact entry pinpointed.
6. **(30s)** Show Phoenix traces on the right monitor — per-action spans with timing.
7. **(60s)** Q&A: "Why demonstration over a flowchart?" / "What happens when WiFi dies?" (LOCKED mode)
