# Shepherd — Structure & Functionality

Shepherd is a **local oversight and governance layer for AI desktop agents**. You author a
task by demonstrating it once, watch an agent execute it live on your real desktop, have a
monitor catch dangerous steps and halt, and replay any past run. The agent is the
untrusted part; Shepherd is the layer that makes it trustworthy.

This document describes what each part of the system does and how it works. The end-to-end
flow is:

```
Voice / typed intent
  → Router        (deterministic keyword match → routine_id)
  → Engine        (LIVE: Agent S plans against a demonstration | LOCKED: verbatim replay)
      ├─ Monitor   (boundary-only safety checks → flag / halt)
      └─ Task graph (persistent per-task memory, grown each run)
  → Telemetry     (Arize Phoenix spans)
  → Memory        (Redis replay store)
  → Dashboard     (live WebSocket Control Hub at http://localhost:8765)
```

A guiding rule runs through the whole codebase: **the click path is sacred.** Nothing async,
networked, or ML-based runs inside a routine's step sequence. Every integration
(voice, monitor, telemetry, browser, multi-agent messaging) is invoked only *between* steps,
at routine boundaries, so a network hiccup or model stall can never strand the mouse
mid-action.

---

## 1. Entry point & orchestration

### `main.py`
The main loop that wires everything together.

- Parses `--mode LIVE|LOCKED` (overrides `EXECUTION_MODE`).
- Initializes Sentry, telemetry, memory, routines cache, coordinate map, router, and engine.
- Starts the dashboard server in a daemon thread, and the Overshoot vision stream in another
  (both never block the engine).
- Loops forever: get an intent (voice or typed) → resolve to a routine → execute → record
  telemetry + memory → print result.
- `_get_intent_text()` arms the spoken-"stop" halt listener and records voice via Deepgram,
  falling back to `input()` when voice is off or fails.
- Band start/complete notifications are published in fire-and-forget threads around
  `engine.execute()`, never inside it.

### `config.py`
Central configuration loaded from `.env` (via `python-dotenv`).

- Reads all API keys and the `EXECUTION_MODE` / `DASHBOARD_PORT` settings.
- Builds the `FEATURES` dict — every integration is **feature-flagged**, mostly auto-enabled
  by the presence of its API key. With all flags off, core automation + dashboard run fully
  offline. `arize`, `redis`, and `agent_s` default on; `context` and `fieldguide` are hard-off
  pending unpublished criteria.

### `shepherd_types.py`
All shared dataclasses — the type vocabulary used across every module. No logic.

- **Input/routing:** `Intent`, `ResolvedRoutine`
- **Routine definition:** `RoutineStep`, `RecordedStep`, `RoutineDefinition`
- **Execution results:** `StepRecord`, `ExecutionResult`, `ReplayRecord`
- **Persistent memory:** `TaskGraphNode`, `TaskGraph`

---

## 2. Router — intent → routine (`router/`)

Turns free-form intent text into a specific routine to run. **Selection is always
deterministic — never ML or vector search**, because a wrong match here moves the real mouse.

### `router/registry.py`
The static registry: a dict mapping each `routine_id` to its keyword set, variable-extraction
regex patterns, and variable defaults. `CONFIDENCE_THRESHOLD` (0.3) is the minimum match score
to accept a route. Three routines are registered: `ROUTINE_FORM_FILL`,
`ROUTINE_BROWSER_SHOWPIECE`, `ROUTINE_LOCKED_FALLBACK`.

### `router/router.py`
`ShepherdIntentRouter.resolve()`:
1. Lowercases the intent text and scores each routine by `matched_keywords / total_keywords`.
2. Picks the highest scorer; returns `None` if below threshold.
3. Extracts variables from the raw text using the routine's regex patterns, falling back to
   defaults (e.g. applicant name/email, search query).
4. Returns a `ResolvedRoutine` with id, variables, confidence, and matched keywords.

---

## 3. Engine — the execution core (`engine/`)

The only code allowed to actuate the mouse and keyboard. Synchronous and self-contained.

### `engine/engine.py`
`ShepherdExecutionEngine.execute(resolved)` runs a resolved routine end to end:

- **Two modes:**
  - `LIVE` — for each step, asks Agent S to plan the action against the recorded
    demonstration; falls back to the routine's pre-defined step if Agent S is unavailable or
    returns `None`.
  - `LOCKED` — deterministic verbatim replay of the pre-mapped steps (the offline demo floor).
- **Halt control:** a `threading.Event` halt flag is checked at every step boundary (never
  mid-click). It is set by the monitor or by a spoken "stop" — `request_halt()`.
- **Monitor checks** run only at the routine's `high_stakes_steps` boundaries; a `halt`
  verdict aborts the run.
- **Step dispatch** (`_dispatch`) handles the action vocabulary: `move`, `click`,
  `double_click`, `type`, `hotkey`, `open_app`, `wait`, `browser`. `{VARIABLE}` placeholders
  in targets/text are substituted at dispatch time. `browser` is the only boundary action that
  may reach the network (Browserbase), with a local fallback.
- **Task graph integration:** loads the task's persistent graph as a reference before the run,
  records each executed step into it, and emits events when a known task grows a new step.
- Emits a rich stream of events to the dashboard at every stage (`execution.start`,
  `step.start/complete/error`, `monitor.alert`, `execution.complete/halted`, `task.graph.*`),
  wraps each step in a telemetry span, and returns an `ExecutionResult`.

`pyautogui.FAILSAFE` is on (slam the mouse to a corner to abort) and `PAUSE = 0.3` makes
motion deliberate and watchable.

### `engine/agent_s_adapter.py`
`AgentSAdapter` — thin wrapper around Simular's Agent S, the LIVE-mode planner.
`plan_action(instruction, step_index, demonstration_context)` returns a dict of `RoutineStep`
fields or `None` to fall back to the defined step. **The actual Agent S import path / API is a
placeholder to be verified at the event**; until then `available` is `False` and LIVE mode just
runs the defined steps.

### `engine/recorder.py`
`DemonstrationRecorder` — captures a human performing a task into a list of `RecordedStep`s
("author by demonstration"). Uses `pynput` to listen for mouse clicks and keystrokes;
**Cmd+Shift+M** marks a step boundary (capturing a screenshot and optional spoken narration via
Deepgram), **Cmd+Shift+Q** stops. The result populates a routine's `demonstration` field.

### `engine/routines.py`
Pure data loader: reads `data/routines.json` into `RoutineDefinition` objects (cached). No
control logic. `get_routine(id)` fetches one by id.

### `engine/coords.py`
Loads the display-specific coordinate map (`data/coords.demo.json`) of logical points.
`get(key)` resolves a named UI target to `(x, y)`; `calibration_helper()` is an interactive
tool that prints the live mouse position so you can re-map coordinates for a new display.

### `engine/task_graph.py`
**Persistent per-task memory.** Each task (keyed by `routine_id`) has one durable graph,
stored as JSON in `data/task_graphs.json`, that accumulates across runs.

- A new run loads the prior graph as a *reference*, executes against it, and *appends* any step
  the task performs that the graph hasn't seen yet (steps are deduped by an
  `action::target::description` signature).
- `record_step` increments `times_seen` for known steps or appends new nodes; `node_for`
  feeds prior outcomes back to Agent S as planning context.
- Writes are atomic (temp file + `os.replace`) so a crash never leaves a half-written graph.
- Complements the Redis replay store: the graph is a *merged* view of what a task has ever
  done; Redis keeps *individual* run history. Read/written at boundaries only.
- **Taught layer.** Beyond observed milestones, a node can carry a `procedure` and
  `conditionals` (`if <when> → do <do>`) baked from human interventions, plus `requires`
  and `source` (`observed` | `taught`). `set_procedure` / `add_conditional` mutate these
  idempotently. This turns the passively-observed graph into a self-improving *workflow*.

### `engine/llm.py`
**Provider-agnostic LLM layer** for the COLD-path crystallization calls (milestone
segmentation + EDIT-mode patches) — never the click path. One `complete()` over httpx
selects `gemini` (Gemma/Gemini, dev default) or `anthropic` purely from `.env`
(`LLM_PROVIDER`, `GEMINI_MODEL`, `LLM_ANTHROPIC_MODEL`). Normalizes provider quirks (Gemma
"thought" parts, code-fenced output) and exposes `parse_json_array()` for robust extraction.

### `engine/workflow_edit.py`
**Teaching loop — EDIT-mode coalescing.** When a run traces an existing workflow and a
human resolves a block with the `save_as_rule` flag, `build_patch()` emits a PATCH (ops
referencing existing node keys: `add_conditional`, `set_procedure`, `add_node`, `add_branch`,
`noop`) and `apply_patch()` bakes it in — patch, never rebuild, so keys stay stable. Uses the
LLM when a key is configured; falls back to a deterministic heuristic otherwise. The engine
then injects these saved clauses on the next run instead of re-blocking.

---

## 4. Monitor — the safety layer (`integrations/monitor_agent.py`)

Rule-based failure-mode detection, run at `high_stakes_steps` boundaries only — never inside a
click sequence. `check_step(step, state)` returns `{"verdict": "ok"|"flag"|"halt", "reason": ...}`:

1. **Planted trigger** — if a step carries a `monitor_trigger` (`credential`, `captcha`,
   `phishing`, `stuck`), returns the mapped verdict. 100% reliable, used for the demo beat
   (e.g. the form-fill halts at the planted credential step).
2. **Screenshot + OCR rules** — captures the screen, OCRs it (`pytesseract`), and pattern-matches
   for credential/password fields, CAPTCHAs, and prompt-injection/phishing text → `halt`; plus a
   "stuck state" detector (screen unchanged for >8s) → `flag`.

The rule-based path is always on; an LLM upgrade is optional, gated, and never the sole path.

---

## 5. Telemetry & memory (`telemetry/`)

Passive observers of execution — never in the router or the click path.

### `telemetry/telemetry.py`
`ShepherdTelemetry` — Arize Phoenix OpenTelemetry spans. Provides a `span(name)` context
manager (used to wrap the routine and each action) and `record(result)` for a run summary.
Every Phoenix call is wrapped in try/except and degrades to a no-op span, so missing telemetry
never crashes a run. Phoenix is a dev instrument, viewed in a separate browser window.

### `telemetry/memory.py`
`ExecutionMemory` — Redis-backed store powering the dashboard's Replay panel. `store()` writes
each completed run as a `ReplayRecord` (full step list + variables + confidence) under several
keys (`shepherd:executions` list, `shepherd:run:{id}`, `shepherd:last:{routine}`,
`shepherd:var:{name}`). `recent()`, `get_run()`, and `last_value()` read it back. Read/write
only; absent Redis is non-fatal.

### `telemetry/sentry_init.py`
`init_sentry()` — initializes the Sentry SDK for error capture when `SENTRY_DSN` is set; no-op
otherwise. The engine and main loop capture exceptions to Sentry when the flag is on.

---

## 6. Dashboard — the Control Hub (`dashboard/`)

A live web UI (default `http://localhost:8765`) that visualizes everything in real time. Fully
offline; the engine and dashboard communicate only through a local event bus.

### `dashboard/events.py`
`EventBus` — the single channel between engine and dashboard. The engine emits events
synchronously and thread-safely from any thread; emits are buffered into a bounded history
(500) and dispatched to async subscribers via the dashboard's event loop. No network dependency.

### `dashboard/server.py`
FastAPI + Uvicorn server (run as a daemon thread from `main.py`):

- Serves the Control Hub UI (`static/index.html`), the demo form (`/demo-form`), and a
  Browserbase local-fallback stub (`/demo-web`).
- `/ws` WebSocket: on connect, replays event history so late joiners catch up, then streams
  live events broadcast from the event bus.
- REST API: `/api/runs` and `/api/runs/{id}` (replay history from Redis),
  `/api/routines/{id}` (routine definition), `/api/task-graph/{id}` (accumulated task graph).

### `dashboard/static/index.html`
The single-page Control Hub. Connects to `/ws` and renders, from the event stream:
- **Intent panel** — raw intent, resolved routine, confidence bar, matched keywords, variables.
- **Vision panel** — live Overshoot screen-description stream.
- **Task-graph (DAG) panel** — the steps the task knows, marking which are already-learned vs
  newly appended this run.
- **Monitor panel** — verdict/reason/trigger when the monitor flags or halts.
- **Replay panel** — past runs loaded from Redis.
- **Log strip** — the raw event feed, plus a status dot/mode badge/run id in the header.

---

## 7. Optional integrations (`integrations/`)

All feature-flagged and boundary-only. Each degrades gracefully (or no-ops) when its flag is
off, so the core product runs without any of them. Several use placeholder API calls marked
**VERIFY at event** until the real SDK/endpoint is confirmed.

| File | Purpose | Invoked |
|---|---|---|
| `deepgram_input.py` | Voice STT: records mic → Deepgram → transcript string; also a background "stop" command listener. | Before an `Intent` is built; never between steps. |
| `browserbase_routine.py` | Runs a web action in a Browserbase cloud browser (Playwright over CDP); local stub fallback. | Only as the `browser` action at a routine boundary. |
| `overshoot_vision.py` | Passive screen-vision audit: parallel daemon polls the screen, POSTs to Overshoot, emits `vision.update` events. | A daemon thread; never inside `engine.execute()`. |
| `band_boundary.py` | Multi-agent messaging: publishes routine start/complete to a Band room. | Fire-and-forget threads around `execute()`. |
| `monitor_agent.py` | The safety monitor (see §4). | At `high_stakes_steps` boundaries. |
| `orkes_workflow.py` | Orkes Agentspan wrapper around an agent run — VERIFY Saturday; may be dropped. | Wraps an agent run if enabled. |
| `context_adapter.py` | Context integration — disabled, criteria unpublished. | n/a |
| `fieldguide_adapter.py` | Fieldguide audit-record submission — disabled, criteria unpublished. | n/a |

---

## 8. Data & assets (`data/`)

- **`routines.json`** — the three demo routines (form-fill with a planted credential halt,
  Browserbase showpiece, locked TextEdit fallback): their steps, variables, `high_stakes_steps`,
  per-step instructions, and mode.
- **`coords.demo.json`** — display-specific logical coordinate map (re-calibrate per display).
- **`demo_form.html`** — the local job-application form the form-fill routine targets.
- **`task_graphs.json`** — generated at runtime; the persistent per-task graphs.
- **`screenshots/`** — generated by the recorder at each step boundary.

---

## Execution modes & demo routines (quick reference)

| Mode | Behavior |
|---|---|
| `LIVE` | Agent S plans each action against the recorded demonstration; pyautogui actuates. |
| `LOCKED` | Deterministic verbatim replay — the offline floor / demo fallback. |

| Routine | Trigger phrases | What it shows |
|---|---|---|
| `ROUTINE_FORM_FILL` | "fill form", "apply", "job" | Form authored by demonstration; monitor **halts** at the planted credential step. |
| `ROUTINE_BROWSER_SHOWPIECE` | "open browser", "search" | A live web action via a Browserbase cloud browser. |
| `ROUTINE_LOCKED_FALLBACK` | "demo", "test" | Deterministic offline TextEdit routine — the safe floor. |
