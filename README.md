# The Shepherd

**Local oversight and governance layer for AI desktop agents.**

> *The agent is the part you can't trust. The Shepherd is the layer that lets you trust it anyway.*

Author a task by demonstrating it once → watch the agent execute live → catch dangerous steps before they happen → replay any past run.

---

## What it does

An AI agent operating your desktop can click, type, and submit forms without supervision. The Shepherd runs alongside it and:

- **Intercepts high-stakes steps** — credential fields, payment forms, irreversible actions — and blocks for human approval
- **Detects deviation** — if the agent does something different from the recorded demonstration, it flags or halts
- **Learns over time** — steps that deviate or halt repeatedly are automatically added to the monitored set
- **Never blocks the click path** — monitoring runs at step *boundaries*, not inside sequences
- **Falls back gracefully** — if Agent S is unavailable, LOCKED mode replays pre-mapped steps deterministically

---

## Quick Start

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
# 1. Clone & enter
cd shepherd

# 2. Install dependencies
uv sync

# 3. Configure
cp .env.example .env
# Edit .env — set OPENAI_API_KEY at minimum for LIVE mode

# 4. Start Redis
brew install redis && brew services start redis

# 5. Run
uv run python main.py

# 6. Open Control Hub
open http://localhost:8765
```

### Optional extras

```bash
uv sync --extra voice     # Deepgram mic input (pyaudio)
uv sync --extra agent_s   # Agent S LIVE planner (gui-agents)
uv run playwright install # Browserbase cloud browser steps
```

### Agent S — free local setup (Ollama)

Agent S requires a vision LLM. The free path uses Ollama + Qwen2.5-VL locally — no API key, works offline.

```bash
# Install Ollama
brew install ollama

# Pull the vision model (~4 GB, do this before the venue)
ollama pull qwen2.5-vl:7b

# Install Agent S dependencies
uv sync --extra agent_s

# Install Tesseract OCR (required by Agent S)
brew install tesseract
```

Then in `.env`:
```
AGENT_S_ENGINE_TYPE=openai
AGENT_S_MODEL=qwen2.5-vl:7b
AGENT_S_BASE_URL=http://localhost:11434/v1
OPENAI_API_KEY=ollama
```

Ollama must be running (`ollama serve`) before starting Shepherd.

---

## Local Arize Phoenix (tracing)

No account needed. Phoenix runs locally and receives OpenTelemetry spans — one per step, with deviation and timing attributes.

```bash
# Terminal 1
./scripts/serve_phoenix.sh       # → http://localhost:6006

# Terminal 2
uv run python main.py
```

Run a routine, then open **http://localhost:6006** → project **shepherd** → **Traces** to see `routine.execute` → `step.N` → `routine.summary`.

The same spans also appear live in the **Control Hub** center panel → **Traces** tab (`http://localhost:8765`) as a nested graph with duration bars — no window switching during demos.

---

## macOS Permissions

Grant in **System Settings → Privacy & Security** for the terminal running Shepherd:

| Permission | Required for |
|---|---|
| **Accessibility** | pyautogui mouse + keyboard |
| **Screen Recording** | screenshots for monitor + Agent S |
| **Microphone** | Deepgram voice input |

---

## Recording a Demonstration

```bash
python main.py --record ROUTINE_FORM_FILL
# Controls: Cmd+Shift+M = mark step  |  Cmd+Shift+Q = stop
```

Saves the recorded steps into `data/routines.json`. In LIVE mode, Agent S plans against this recording.

---

## Execution Modes

| Mode | Description |
|---|---|
| `LIVE` | Agent S plans each action from a screenshot against the recorded demo |
| `LOCKED` | Deterministic verbatim replay — works fully offline |

```bash
python main.py --mode LOCKED
```

---

## Demo Routines

| Routine | Trigger phrases | What happens |
|---|---|---|
| `ROUTINE_FORM_FILL` | *"fill form"*, *"apply"*, *"fill out"* | Fills a credential form; Shepherd halts at the password field |
| `ROUTINE_BROWSER_SHOWPIECE` | *"open browser"*, *"search"*, *"look up"* | Browserbase cloud browser action |
| `ROUTINE_LOCKED_FALLBACK` | *"demo"*, *"safe mode"* | Deterministic TextEdit routine — offline floor |

---

## Coordinate Calibration

Coordinates are display-specific. After moving to a new monitor:

```bash
python -c "from engine.coords import calibration_helper; calibration_helper()"
```

Update `data/coords.demo.json` with the printed values.

---

## Project Layout

```
frontend/       Control Hub UI (HTML/CSS/JS)
engine/         Execution engine — LIVE + LOCKED modes, Agent S adapter
router/         Deterministic keyword intent router
services/       Deepgram, Browserbase, Monitor, Overshoot, Band — first-class runtime services
telemetry/      Arize Phoenix spans, Redis replay memory, routine evolution tracking
dashboard/      FastAPI WebSocket server — streams events to Control Hub
data/           Routines JSON, coordinate maps, demo form, screenshots
```

---

## Feature Flags

All services are feature-flagged. With all flags off, core automation + dashboard runs fully offline.

| Flag | Enabled when |
|---|---|
| `deepgram` | `DEEPGRAM_API_KEY` set |
| `arize` | always (local Phoenix) |
| `sentry` | `SENTRY_DSN` set |
| `redis` | always (local Redis) |
| `browserbase` | `BROWSERBASE_API_KEY` set |
| `band` | `BAND_API_KEY` + `BAND_ROOM_KEY` set |
| `overshoot` | `OVERSHOOT_API_KEY` set |
| `orkes` | `ORKES_SERVER_URL` + `ORKES_API_KEY` set |

---

## Architecture

```
Voice (Deepgram STT)  OR  typed input
  ↓
ShepherdIntentRouter  — deterministic keyword matching → routine_id
  ↓
ShepherdExecutionEngine
  ├── LIVE:   Agent S screenshots + plans → pyautogui actuates
  └── LOCKED: pre-mapped steps → pyautogui actuates
  ↓  (at step boundaries only — never mid-click)
MonitorAgent  — OCR scans for credential / captcha / phishing / stuck
  ↓ FLAG / HALT → blocks engine; human approves or halts via Control Hub
RoutineEvolution  — tracks per-step stats; auto-promotes risky steps to monitored
  ↓
TaskGraphStore — loads this task's persistent graph as a reference, then
                 collapses executed clicks into milestones and merges them
                 back in (matched vs appended). Per-click detail → Agent S.
  ↓
ShepherdTelemetry → Arize Phoenix (per-step spans with deviation + timing)
ExecutionMemory   → Redis (Replay panel)
  ↓
Dashboard WebSocket → Control Hub  http://localhost:8765
```

---

## Task Graph Memory

Every task keeps **one durable graph** that accumulates across runs (stored in
`data/task_graphs.json`, keyed by resolved routine — so the same or a similar
request reuses the same graph).

The graph is **coarse on purpose**: it records *milestones* — the level a human
reasons about — not individual clicks. Many fine actions collapse into one node,
e.g. the 13 clicks of `ROUTINE_FORM_FILL` become:

```
Open Safari → Scan results → Navigate to localhost:8765 → Enter details → Submit
```

Milestone kinds: `open · navigate · search · scan · fill · submit · interact`
(search/navigate nodes capture their payload — e.g. `Search: AI agent safety`).

- On each run the engine **loads the prior graph as a reference**, so it knows
  what's already been done at the milestone level.
- As the task runs, the executed clicks are **collapsed into milestones at the
  routine boundary** and merged back in: a milestone the graph has seen is
  *matched* (`times_seen` ticks up, once per run); a milestone the task performs
  that the graph doesn't have yet is *appended*. The graph grows to cover whatever
  the task actually does.
- **Per-click detail is still fed to Agent S** — every click gets its own
  `plan_action` call (enriched with which milestone it belongs to and how often
  that milestone has run before). Only the persisted graph is coarse.
- The Control Hub badges each click live with its milestone — `↺ <milestone> ·N×`
  when recalled, `＋ <milestone>` when new — and the log shows the milestone chain
  (`Recalled task graph · Open Safari → … → Submit`).

Inspect a task's accumulated graph any time:

```bash
curl http://localhost:8765/api/task-graph/ROUTINE_FORM_FILL
```

Graph reads/writes happen only at routine boundaries — never inside the click sequence.

---

## Day-Of Checklist

- [ ] `ROUTINE_LOCKED_FALLBACK` runs flawlessly offline
- [ ] Coordinates re-calibrated on venue display
- [ ] Accessibility + Screen Recording permissions granted
- [ ] Arize Phoenix running in separate browser tab
- [ ] Monitor halts on planted credential step (step 11 in `ROUTINE_FORM_FILL`)
- [ ] Spoken "stop" halts at next step boundary
- [ ] Replay panel loads past runs from Redis
- [ ] Control Hub screenshot panel shows live screen
- [ ] LIVE mode: `ollama serve` running, `qwen2.5-vl:7b` pulled, Agent S initialized
- [ ] 5-minute run-of-show rehearsed twice
