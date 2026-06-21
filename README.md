# The Shepherd

**The trust layer between humans and AI agents.**

> *AI agents can do anything. The Shepherd decides what they're allowed to.*

You give the agent a task. It executes autonomously. The Shepherd watches every step — blocking dangerous actions, verifying decisions with a second AI, logging everything to a tamper-proof trail, and holding the blast radius inside a configurable sandbox. When something looks wrong, a human decides.

---

## The Core Problem

AI desktop agents — things like Agent S, Claude Computer Use, or any future autonomous worker — can click, type, navigate, and submit. They can fill out a job application, send an email, enter payment details, or export your data. They do this without asking.

**The Shepherd is the oversight layer that makes agents safe to run.** Not by slowing them down, but by putting a transparent, configurable governance layer between their intent and your keyboard.

---

## How It Works

```
Voice (Deepgram STT)  OR  typed input
  ↓
ShepherdIntentRouter — Redis vector search (semantic) + keyword fallback
  ↓
ShepherdExecutionEngine
  ├── LIVE:   Agent S reads screenshot → plans action → pyautogui actuates
  └── LOCKED: pre-mapped steps → pyautogui actuates (fully deterministic, offline)
  ↓  (at high-stakes step boundaries only — never mid-click)
┌─────────────────────────────────────────────────────────────────────────────┐
│                        3-Layer Oversight Stack                              │
│                                                                             │
│  Layer 1: Policy Engine (rule-based, <1ms, always-on)                       │
│    → Evaluates data/policy.yaml against OCR'd screen content                │
│    → Planted triggers: credential / captcha / phishing / payment            │
│    → App containment: only allowed apps may be launched                     │
│    → Domain containment: only allowlisted URLs may be visited               │
│    → Rate limiting: max N actions/min, max M steps/run                      │
│                                                                             │
│  Layer 2: Independent Verifier (Haiku second opinion, on "flag" only)       │
│    → Separate model, separate context, separate prompt                      │
│    → Upgrades flag→halt when confident; downgrades flag→ok on false alarms  │
│    → Never second-guesses a "halt" from Layer 1 — that's already certain    │
│                                                                             │
│  Layer 3: Human Approval Gate                                               │
│    → Any surviving flag blocks execution, streams to Control Hub            │
│    → Human approves, halts, or overrides with a natural-language instruction│
└─────────────────────────────────────────────────────────────────────────────┘
  ↓
Tamper-Evident Audit Log
  → Hash-chained JSONL — any edit to any past entry breaks the chain
  → GET /api/audit/verify returns chain status instantly
  ↓
RoutineEvolution — tracks per-step stats, auto-promotes risky steps to monitored
TaskGraphStore   — learns milestones across runs; tells Agent S what's been done
ShepherdTelemetry → Arize Phoenix (per-step spans with deviation + timing attrs)
  ↓
Dashboard WebSocket → Control Hub  http://localhost:8765
```

---

## What makes this different

| Capability | Shepherd | Typical agent |
|---|---|---|
| Intercepts dangerous steps | ✅ sub-step boundaries | ❌ runs to completion |
| Policy-as-code governance | ✅ `data/policy.yaml` | ❌ hardcoded or none |
| Two independent oversight layers | ✅ rules + Haiku verifier | ❌ single model |
| Tamper-evident audit trail | ✅ SHA-256 hash chain | ❌ logs (mutable) |
| App + domain containment | ✅ allowlists + rate limits | ❌ unrestricted |
| Works fully offline | ✅ LOCKED mode | ❌ API-dependent |
| Deterministic halt path | ✅ zero LLMs in halt path | ❌ model-dependent |
| Human override with instruction | ✅ override → Agent S replans | ❌ binary stop/go |

---

## Quick Start

Requires [uv](https://docs.astral.sh/uv/).

```bash
# Clone & enter
cd shepherd

# Install
uv sync

# Configure
cp .env.example .env     # set ANTHROPIC_API_KEY

# Start Redis (needed for semantic routing + replay memory)
brew install redis && brew services start redis

# Run
uv run python main.py

# Control Hub
open http://localhost:8765
```

Say or type: **"fill out the application form"** — watch it run, and halt.

---

## The Governance Policy

Edit `data/policy.yaml` to change what the agent is allowed to do. No restart needed — the policy reloads on every evaluation.

```yaml
screen_rules:
  - name: no-credentials
    match_text: ["password", "api key", "ssn"]
    action: halt
    reason: "Credential field detected"

  - name: suspicious-payment
    match_text: ["confirm payment", "credit card number"]
    action: flag           # flag → Haiku verifier → human gate
    reason: "Payment authorization detected"

containment:
  allowed_apps: ["Google Chrome", "Safari"]
  allowed_domains: ["localhost", "workbridge.com"]
  max_actions_per_minute: 60
  max_steps_per_run: 100
```

**This is the demo moment:** change a rule, re-run, see behavior change. That's the difference between a demo and a product.

---

## Audit Log

Every action is logged to `data/audit.jsonl` with a SHA-256 hash chain. Tamper with any entry and the chain breaks.

```bash
# View recent entries
curl http://localhost:8765/api/audit

# Verify the entire chain
curl http://localhost:8765/api/audit/verify
# → {"valid": true, "entries": 47, "tampered_at": null, "reason": "chain intact"}
```

The chain verification is O(n) — it walks every entry, checks the hash, checks `prev_hash` matches the prior entry. Modify one byte and it pinpoints exactly which entry was tampered.

---

## Semantic Intent Routing

The router uses **Redis 8 VSET** (native vector sets) with **BAAI/bge-small-en-v1.5** embeddings (local, no API key, 384-dim) to match what you say to the right routine. Keyword matching is the fallback.

```bash
"submit my application"   → ROUTINE_FORM_FILL      (similarity: 0.886)
"fill out the intake form" → ROUTINE_FORM_FILL      (similarity: 0.871)
"open a browser"          → ROUTINE_BROWSER_SHOWPIECE (similarity: 0.793)
```

---

## Execution Modes

| Mode | Description |
|---|---|
| `LIVE` | Agent S takes a screenshot and plans each action. One Claude call for batch_fill (vision-plans which field gets which value). |
| `LOCKED` | Deterministic replay of pre-mapped steps — zero API calls, works at a dead venue. |

Switch at runtime via the Control Hub or API:
```bash
curl -X POST http://localhost:8765/api/mode/LOCKED
curl -X POST http://localhost:8765/api/mode/LIVE
```

---

## Demo Routines

| Routine | Trigger phrases | What happens |
|---|---|---|
| `ROUTINE_FORM_FILL` | *"fill form"*, *"apply"*, *"fill out the application"* | Fills 7 fields via Chrome JS injection; policy halts at step 4 |
| `ROUTINE_BROWSER_SHOWPIECE` | *"open browser"*, *"search for..."* | Browserbase cloud browser action |
| `ROUTINE_LOCKED_FALLBACK` | *"demo"*, *"safe mode"* | Fully offline floor routine |

---

## macOS Permissions

Grant in **System Settings → Privacy & Security** for the terminal running Shepherd:

| Permission | Required for |
|---|---|
| **Accessibility** | pyautogui mouse + keyboard |
| **Screen Recording** | screenshots for monitor + Agent S |
| **Microphone** | Deepgram voice input (optional) |

Chrome also needs **View → Developer → Allow JavaScript from Apple Events** — Shepherd auto-enables this when filling forms.

---

## Project Layout

```
engine/
  engine.py            Execution engine — LIVE + LOCKED, containment, audit wiring
  agent_s_adapter.py   Agent S wrapper + plan_batch_fill_mapping (Claude vision)
  routines.py          Routine loader (BatchField deserialization)
  task_graph.py        Milestone-level persistent task memory
  approvals.py         Human approval gate (blocking)

router/
  router.py            Semantic vector search + keyword fallback
  vector_router.py     Redis 8 VSET (VADD/VSIM) + fastembed embeddings
  registry.py          Routine keyword/variable registry

services/
  monitor_agent.py     Rule-based step monitor (OCR + planted triggers)
  policy_engine.py     Loads + evaluates data/policy.yaml at runtime
  verifier.py          Independent Haiku second-opinion verifier
  deepgram_input.py    Voice STT input

telemetry/
  audit_log.py         Tamper-evident hash-chain audit log
  telemetry.py         Arize Phoenix OTel spans
  evolution.py         Per-step stats + auto-promote risky steps
  memory.py            Redis replay memory

dashboard/
  server.py            FastAPI WebSocket + REST API
  events.py            Event bus (broadcast to Control Hub)

data/
  policy.yaml          Governance policy (edit to change behavior)
  routines.json        Routine definitions
  audit.jsonl          Tamper-evident action log (auto-created)
  demo_form.html       Local demo form served at localhost:8765/demo-form

frontend/
  index.html           Control Hub UI
```

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /api/status` | Engine state (mode, step, run_id) |
| `POST /api/mode/{LIVE\|LOCKED}` | Switch execution mode |
| `POST /api/control/{approve\|halt\|override}` | Human decision at approval gate |
| `GET /api/audit` | Most recent audit log entries |
| `GET /api/audit/verify` | Verify hash chain integrity |
| `GET /api/policy` | Current governance policy (parsed YAML) |
| `GET /api/runs` | Recent execution history |
| `GET /api/runs/{run_id}` | Single run detail |
| `GET /api/routines/{id}` | Routine definition + step list |
| `GET /api/task-graph/{id}` | Accumulated milestone graph |
| `WS /ws` | Live event stream to Control Hub |

---

## Day-Of Checklist

- [ ] Redis running (`brew services start redis`)
- [ ] `ANTHROPIC_API_KEY` set in `.env`
- [ ] Accessibility + Screen Recording permissions granted
- [ ] Chrome has "Allow JavaScript from Apple Events" (auto-enabled on first run)
- [ ] `ROUTINE_FORM_FILL` in LOCKED mode runs to halt cleanly
- [ ] `GET /api/audit/verify` returns `{"valid": true}`
- [ ] `GET /api/policy` returns current rules
- [ ] Control Hub opens at `http://localhost:8765`
- [ ] Coordinates calibrated on venue display (`data/coords.demo.json`)
- [ ] LIVE mode: `ANTHROPIC_API_KEY` valid, Agent S initialized
- [ ] 5-minute run-of-show rehearsed twice

---

## Setup for LIVE Mode (Agent S + Ollama, free, offline)

```bash
brew install ollama
ollama pull qwen2.5-vl:7b          # ~4 GB — do this before the venue
uv sync --extra agent_s
brew install tesseract
```

In `.env`:
```
AGENT_S_ENGINE_TYPE=openai
AGENT_S_MODEL=qwen2.5-vl:7b
AGENT_S_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```

Then `ollama serve` before starting Shepherd.

---

## Local Arize Phoenix (tracing)

```bash
./scripts/serve_phoenix.sh    # → http://localhost:6006
uv run python main.py
```

Every routine run produces OTel spans at `routine.execute → action.N` level with `action.type`, `deviation`, `duration_ms` attributes. View in Phoenix or in the Control Hub **Traces** tab.
