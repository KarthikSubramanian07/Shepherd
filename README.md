# The Shepherd

Local oversight and governance layer for AI desktop agents.

**The agent is the part you can't trust. The Shepherd is the layer that lets you trust it anyway.**

Author a task by demonstrating it once → watch the agent execute live → catch dangerous steps and halt → replay any past run.

---

## Quick Start

Requires [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

```bash
# 1. Clone & enter
cd shepherd

# 2. Create venv and install dependencies
uv sync

# 3. Configure
cp .env.example .env
# Edit .env — at minimum add DEEPGRAM_API_KEY if using voice

# 4. Start Redis (for Replay panel)
brew install redis && brew services start redis

# 5. Run
uv run python main.py

# 6. Open Control Hub
open http://localhost:8765
```

### Optional extras

```bash
uv sync --extra voice     # mic recording for Deepgram (pyaudio)
uv run playwright install # Browserbase browser steps
```

---

## Local Arize Phoenix (tracing)

No account or API key required. Phoenix runs locally and receives OpenTelemetry spans from Shepherd.

**Terminal 1 — start Phoenix:**

```bash
./scripts/serve_phoenix.sh
# UI → http://localhost:6006
```

**Terminal 2 — start Shepherd:**

```bash
cp .env.example .env   # includes PHOENIX_COLLECTOR_ENDPOINT=http://localhost:6006
uv run python main.py
```

Run a routine (`demo`, `fill form`, etc.), then open **http://localhost:6006** → project **shepherd** → **Traces**.

You should see nested spans: `routine.execute` → `action.*` → `routine.summary`.

Startup log should show:

```
[arize] Phoenix tracer active — project: shepherd → http://localhost:6006/v1/traces
```

If traces don't appear, confirm Phoenix is running in Terminal 1 before starting Shepherd.

---

## macOS Permissions Required

Grant these in **System Settings → Privacy & Security** for the terminal/IDE running Shepherd:

| Permission | Required for |
|---|---|
| **Accessibility** | pyautogui mouse + keyboard control |
| **Screen Recording** | pyautogui screenshot (monitor + Overshoot) |
| **Microphone** | Deepgram voice input |

Without Accessibility + Screen Recording, pyautogui calls will **silently fail**.

---

## Coordinate Calibration

Coordinate maps are display-specific. After moving to a new monitor, re-calibrate:

```bash
python -c "from engine.coords import calibration_helper; calibration_helper()"
```

Move the mouse to each UI target. The script prints logical coordinates (physical_px / 2 on Retina).  
Update `data/coords.demo.json` with the printed values.

---

## Execution Modes

| Mode | Description |
|---|---|
| `LIVE` | Agent S plans actions against the recorded demonstration |
| `LOCKED` | Deterministic verbatim replay — offline floor / demo fallback |

```bash
python main.py --mode LOCKED   # force LOCKED mode
```

---

## Demo Routines

| Routine ID | Description |
|---|---|
| `ROUTINE_FORM_FILL` | Fill a job application form; monitor halts at credential step |
| `ROUTINE_BROWSER_SHOWPIECE` | Browserbase cloud browser web action |
| `ROUTINE_LOCKED_FALLBACK` | Deterministic TextEdit routine — offline floor |

Trigger phrases:
- **"fill form"** → `ROUTINE_FORM_FILL`
- **"open browser"** or **"search"** → `ROUTINE_BROWSER_SHOWPIECE`
- **"demo"** → `ROUTINE_LOCKED_FALLBACK`

---

## Recording a Demonstration

```bash
python -c "
from engine.recorder import DemonstrationRecorder
r = DemonstrationRecorder()
r.start()
# perform the task now...
# press Cmd+Shift+M to mark each step boundary
# press Cmd+Shift+Q to stop
"
```

The resulting `list[RecordedStep]` goes into the routine's `demonstration` field in `data/routines.json`.

---

## Feature Flags

All integrations are feature-flagged in `.env`. With all flags off, core automation + dashboard runs fully offline.

| Flag | Enabled when |
|---|---|
| `deepgram` | `DEEPGRAM_API_KEY` set |
| `arize` | always (local Phoenix via `PHOENIX_COLLECTOR_ENDPOINT`) |
| `sentry` | `SENTRY_DSN` set |
| `redis` | always (local Redis) |
| `browserbase` | `BROWSERBASE_API_KEY` set |
| `band` | `BAND_API_KEY` + `BAND_ROOM_KEY` set |
| `overshoot` | `OVERSHOOT_API_KEY` set |
| `orkes` | `ORKES_SERVER_URL` + `ORKES_API_KEY` set — VERIFY Saturday |
| `context` | disabled — VERIFY criteria Saturday |
| `fieldguide` | disabled — VERIFY criteria Saturday |

---

## Architecture

```
Voice (Deepgram STT)
  ↓
ShepherdIntentRouter  — deterministic keyword matching → routine_id
  ↓
ShepherdExecutionEngine
  ├── LIVE: Agent S plans against recorded demonstration; pyautogui actuates
  └── LOCKED: deterministic verbatim replay
  ↓ (parallel, boundary-only)
MonitorAgent  — captcha / credential / phishing / stuck → flag or halt
  ↓
ShepherdTelemetry → Arize Phoenix (dev dashboard, separate window)
  ↓
ExecutionMemory → Redis (Replay panel)
  ↓
Dashboard WebSocket → Control Hub (http://localhost:8765)
```

---

## Lane Owners

| Lane | Owner | Files |
|---|---|---|
| A — Engine | Jean | `engine/`, `integrations/monitor_agent.py`, `data/` |
| B — Dashboard | Karthik | `dashboard/` |
| C — Telemetry | Rohan | `telemetry/`, `integrations/overshoot_vision.py`, `integrations/band_boundary.py` |
| D — Integrations + Pitch | Leon | `integrations/deepgram_input.py`, `integrations/browserbase_routine.py`, `main.py` |

**Cross-lane rule:** Nobody's code runs inside `engine.execute()`'s click sequence except Lane A.

---

## VERIFY at Event

These integrations use placeholder API calls — confirm the real SDK/endpoint before implementing:

- **Band** — no confirmed public Python SDK; check hackathon materials
- **Browserbase** — verify current SDK + Playwright pairing
- **Overshoot** — JS SDK only; confirm REST API at docs.overshoot.ai
- **Deepgram** — check developers.deepgram.com for current mic streaming API
- **Orkes** — track judged on Agentspan, NOT Conductor; VERIFY Saturday before touching

---

## Day-Of Checklist

- [ ] `ROUTINE_LOCKED_FALLBACK` runs flawlessly offline
- [ ] Coordinates re-calibrated on demo display at venue resolution
- [ ] Accessibility + Screen Recording permissions granted
- [ ] Arize Phoenix running in separate browser window
- [ ] Sentry initialized and verified (trigger one test error)
- [ ] Browserbase beat works; local fallback confirmed
- [ ] Deepgram voice input tested; typed fallback confirmed
- [ ] Monitor halts on planted credential step (step 11 in ROUTINE_FORM_FILL)
- [ ] Spoken "stop" halts at next boundary
- [ ] Replay panel loads past runs from Redis
- [ ] 5-minute run-of-show rehearsed twice
