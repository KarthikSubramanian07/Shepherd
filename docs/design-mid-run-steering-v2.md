# Design: Mid-Run Steering (Final)

## Problem Statement

Every dispatch through the Command Center creates a **new task**. There is no mechanism to amend an in-flight autonomous run's goal, resume from a halt with context, or steer the agent without restarting from scratch.

---

## Three Operator Verbs

| Verb | When Available | What It Does |
|------|---------------|-------------|
| **New Task** | Always | Halt current (if any) → discard context → start fresh |
| **Steer** | Running or Suspended | Amend goal; if running, consumed at next step boundary with fresh screenshot. If suspended, triggers resume with amended goal. |
| **Halt** | Running | Pause the agent. State preserved. Machine occupied until resume or new task. |

---

## What Agent S Sees Per Step (Today)

```
┌─────────────────────────────────────────────────────────────────┐
│ SCREENSHOT (fresh capture, base64 PNG)                           │
├─────────────────────────────────────────────────────────────────┤
│ GOAL: "{goal}"                         ← FROZEN for entire run  │
│ PLAN_HINT: (LLM roadmap, optional)     ← FROZEN                │
│ MEMORY_HINT: (milestone recall)        ← FROZEN                │
│ CHAIN HISTORY:                                                  │
│   turn 0: "opened Chrome"             ← grows each turn        │
│   turn 1: "clicked Apply"                                       │
│ RULES: (chaining limits, etc.)                                  │
└─────────────────────────────────────────────────────────────────┘
```

Turn-to-turn, only the screenshot and chain_history change. The goal is immutable.

---

## How Steer Changes What Agent S Sees

**Steer is injected into BOTH the goal AND chain_history:**

After steer `"also upload resume.pdf"` is consumed:
```
GOAL: "Fill out the job application form on LinkedIn

[OPERATOR STEER]: also upload resume.pdf from Desktop"

CHAIN HISTORY: [
    "turn 0: navigated to LinkedIn Jobs page",
    "turn 1: clicked Apply button",
    "turn 2: filled in name and email fields",
    ">>> USER INTERVENED (IMPORTANT): also upload resume.pdf from Desktop",
]
```

| Location | Purpose | What breaks if missing |
|----------|---------|----------------------|
| Goal | Instruction — tells agent what to do | Agent ignores steer (history is retrospective) |
| Chain history | Temporal marker — tells agent when told | Agent may try to backtrack thinking it "should have" done this earlier |

Multiple steers accumulate in both locations.

---

## Engine State Machine

```
              ┌────────────────────┐
              │       IDLE         │  (no task, awaiting dispatch)
              └────────┬───────────┘
                       │ new task
                       ▼
              ┌────────────────────┐
    steer →   │      RUNNING       │  ← steer consumed at step boundary
              └────────┬───────────┘
                       │ halt / fail
                       ▼
              ┌────────────────────┐
    steer →   │    SUSPENDED       │  ← steer here triggers resume
              │  (state preserved) │
              └────────┬───────────┘
                       │ new task (discards context)
                       ▼
              ┌────────────────────┐
              │       IDLE         │
              └────────────────────┘
```

**SuspendedTask persists until a "New Task" is dispatched.** No timeout. The physical machine IS in that state — the screen is wherever the agent left it. The machine is occupied by the suspended task; no other task can run.

---

## Architecture: Both Topologies

### All-in-one mode (dashboard in-process)

```
UI (Control Hub) → POST /api/steer → dashboard/server.py → engine._steer_queue
                 → POST /api/intent → remote_intents queue → main.py loop
```

Dashboard gets engine reference via `register_engine(engine)` (same pattern as existing `register_intent_queue`). No new sidecar — dashboard is a daemon thread in the same process with direct access to the engine.

### Remote mode (coordinator relay)

```
UI (Cmd Center) → WS → Coordinator (relay) → WS → relay_client → engine._steer_queue
                                                                 → remote_intents queue
```

Coordinator remains pure plumbing — relays `steer` command without interpretation. Only tracks new `"suspended"` status from events.

---

## Implementation Details

### 1. Engine changes (`engine/engine.py`)

#### New state:

```python
import queue as _queue

@dataclass
class SuspendedTask:
    """Everything needed to resume a halted task."""
    run_id: str
    task_key: str
    goal: str                          # includes any prior steers
    plan_hint: str
    memory_hint: str
    step_index: int
    variables: dict[str, str]
    executed: list[RoutineStep]
    chain_history: list[str]           # Agent S's turn-by-turn memory
    interventions: list[InterventionEvent]
    graph: "TaskGraph"
    halted_at: float
```

```python
# In ShepherdExecutionEngine.__init__:
self._steer_queue: _queue.Queue[tuple[str, bool]] = _queue.Queue()  # (text, remember)
self._suspended_task: Optional[SuspendedTask] = None
```

#### New methods:

```python
def request_steer(self, text: str, remember: bool = True) -> None:
    """Inject a goal amendment. Consumed at the next step boundary."""
    self._steer_queue.put((text, remember))

def is_suspended(self) -> bool:
    return self._suspended_task is not None
```

#### Modified `_execute_autonomous_reactive`:

```python
def _execute_autonomous_reactive(self, goal: str, plan_hint: str = "",
                                  resume_ctx: Optional[SuspendedTask] = None) -> ExecutionResult:
    # ── Initialization ────────────────────────────────────────────
    if resume_ctx:
        # Resume from suspended state — restore context
        goal = resume_ctx.goal
        plan_hint = resume_ctx.plan_hint
        memory_hint = resume_ctx.memory_hint
        self._agent_s._chain_history = list(resume_ctx.chain_history)
        executed = list(resume_ctx.executed)
        self._interventions = list(resume_ctx.interventions)
        graph = resume_ctx.graph
        self._active_graph = graph
        run_id = resume_ctx.run_id  # continue same run
        task_key = resume_ctx.task_key
        variables = resume_ctx.variables
        # Do NOT call reset_autonomous() — preserve chain memory
        event_bus.emit("execution.resumed", {
            "run_id": run_id, "step_index": resume_ctx.step_index,
            "amended_goal": goal,
        })
    else:
        # Fresh start
        self._halt_flag.clear()
        self.last_step_records = []
        self._interventions = []
        self._agent_s.reset_autonomous()
        # Drain any stale steers from a previous task
        while not self._steer_queue.empty():
            try: self._steer_queue.get_nowait()
            except: break
        run_id = str(uuid.uuid4())[:8]
        # ... (existing initialization: task_key, graph, memory_hint, etc.)

    # ── Hot loop ──────────────────────────────────────────────────
    for i in range(max_steps):
        # 1. Halt check
        if self._halt_flag.is_set():
            self._halt_flag.clear()
            self._suspended_task = SuspendedTask(
                run_id=run_id, task_key=task_key, goal=goal,
                plan_hint=plan_hint, memory_hint=memory_hint,
                step_index=i, variables=variables,
                executed=list(executed),
                chain_history=list(self._agent_s._chain_history),
                interventions=list(self._interventions),
                graph=graph, halted_at=time.time(),
            )
            status = "suspended"
            event_bus.emit("execution.suspended", {
                "run_id": run_id, "step_index": i,
                "goal": goal, "steps_completed": steps_done,
            })
            break

        # 2. Drain steer queue (NON-BLOCKING — ~0.1μs if empty)
        try:
            while True:
                steer_text, remember = self._steer_queue.get_nowait()
                goal = f"{goal}\n\n[OPERATOR STEER]: {steer_text}"
                self._agent_s._chain_history.append(
                    f">>> USER INTERVENED (IMPORTANT): {steer_text}"
                )
                event_bus.emit("execution.steered", {
                    "run_id": run_id, "step_index": i, "steer": steer_text,
                })
                flag = "save_as_rule" if remember else "one_off"
                self._interventions.append(InterventionEvent(
                    step_index=i, trigger="steer", decision="override",
                    instruction=steer_text, flag=flag,
                    node_key="", scenario="operator steer", ts=time.time(),
                ))
        except _queue.Empty:
            pass

        # 3. Monitor check (existing)
        verdict = self._check_monitor(...)
        if verdict == "halt": ...

        # 4. Predict (API call — ~3s)
        result = self._agent_s.predict_autonomous(goal, i, memory_hint, plan_hint)

        # 5. Post-predict steer check: if a steer arrived during the API call,
        #    the plan is based on the old goal. Discard it and re-predict.
        if not self._steer_queue.empty():
            # Drain steers, amend goal, skip execution, advance to next iteration
            # (burns one step budget tick — acceptable, human-triggered)
            try:
                while True:
                    steer_text, remember = self._steer_queue.get_nowait()
                    goal = f"{goal}\n\n[OPERATOR STEER]: {steer_text}"
                    self._agent_s._chain_history.append(
                        f">>> USER INTERVENED (IMPORTANT): {steer_text}"
                    )
                    event_bus.emit("execution.steered", {
                        "run_id": run_id, "step_index": i, "steer": steer_text,
                    })
                    flag = "save_as_rule" if remember else "one_off"
                    self._interventions.append(InterventionEvent(
                        step_index=i, trigger="steer", decision="override",
                        instruction=steer_text, flag=flag,
                        node_key="", scenario="operator steer", ts=time.time(),
                    ))
            except _queue.Empty:
                pass
            continue  # re-loop: next iteration re-predicts with amended goal

        # 6. Handle terminal outcomes
        if result.outcome == "done": ...
        if result.outcome == "fail":
            # Save suspended task on fail too — operator can steer past failure
            self._suspended_task = SuspendedTask(...)
            status = "suspended"
            event_bus.emit("execution.suspended", {
                "run_id": run_id, "step_index": i,
                "goal": goal, "reason": "agent_reported_fail",
            })
            break

        # 7. Execute action (existing)
        self._exec_agent_code(result.code)
        # ... record step, emit events ...
```

**Performance on happy path (no steer):** Two `get_nowait()` calls per step (~0.2μs total) against steps that take 3000-5000ms. Unmeasurable overhead.

### 2. Relay client changes (`services/relay_client.py`)

```python
def _apply_command(self, command: Optional[str], payload: dict) -> None:
    from engine.approvals import set_decision, set_override

    if command == "intent":
        text = (payload.get("text") or "").strip()
        if text:
            self._remote_intents.put(text)
            event_bus.emit("remote.intent", {"text": text, "source": "command-center"})

    elif command == "steer":
        text = (payload.get("text") or "").strip()
        remember = payload.get("remember", True)
        if not text:
            return
        if self._engine._suspended_task is not None:
            # Agent is suspended — amend goal and trigger resume
            self._engine._suspended_task.goal += f"\n\n[OPERATOR STEER]: {text}"
            self._engine._agent_s._chain_history.append(
                f">>> USER INTERVENED (IMPORTANT): {text}"
            )
            flag = "save_as_rule" if remember else "one_off"
            self._engine._interventions.append(InterventionEvent(
                step_index=self._engine._suspended_task.step_index,
                trigger="steer", decision="override",
                instruction=text, flag=flag,
                node_key="", scenario="operator steer (on resume)", ts=time.time(),
            ))
            self._remote_intents.put("__RESUME__")
            event_bus.emit("remote.steer", {"text": text, "source": "command-center", "resumed": True})
        else:
            # Agent is running — inject into live goal
            self._engine.request_steer(text, remember)
            event_bus.emit("remote.steer", {"text": text, "source": "command-center", "resumed": False})

    elif command == "new_task":
        # Explicit new task — halt + dispatch (compound)
        text = (payload.get("text") or "").strip()
        set_decision("halt")
        try:
            self._engine.request_halt()
        except Exception:
            pass
        if text:
            self._remote_intents.put(text)
            event_bus.emit("remote.intent", {"text": text, "source": "command-center"})

    elif command in ("halt", "stop"):
        set_decision("halt")
        try:
            self._engine.request_halt()
        except Exception:
            pass

    # ... existing: approve, override, mode, workflow.*, promote ...
```

### 3. Dashboard changes (`dashboard/server.py`)

```python
_engine_ref = None

def register_engine(engine) -> None:
    global _engine_ref
    _engine_ref = engine

@app.post("/api/steer")
async def steer_task(request: Request) -> JSONResponse:
    """Steer a running/suspended autonomous task."""
    if _engine_ref is None:
        return JSONResponse({"error": "no engine registered"}, status_code=503)
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
        remember = body.get("remember", True)
    except Exception:
        text, remember = "", True
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    if _engine_ref._suspended_task is not None:
        _engine_ref._suspended_task.goal += f"\n\n[OPERATOR STEER]: {text}"
        _engine_ref._agent_s._chain_history.append(
            f">>> USER INTERVENED (IMPORTANT): {text}")
        flag = "save_as_rule" if remember else "one_off"
        _engine_ref._interventions.append(InterventionEvent(
            step_index=_engine_ref._suspended_task.step_index,
            trigger="steer", decision="override",
            instruction=text, flag=flag,
            node_key="", scenario="operator steer (on resume)", ts=time.time(),
        ))
        if _intent_queue is not None:
            _intent_queue.put("__RESUME__")
        return JSONResponse({"ok": True, "action": "resumed", "text": text})
    else:
        _engine_ref.request_steer(text, remember)
        return JSONResponse({"ok": True, "action": "steered", "text": text})
```

### 4. Main loop changes (`main.py`)

```python
while True:
    try:
        raw = remote_intents.get()
        if not raw:
            continue

        # ── Resume from suspended task ────────────────────────────
        if raw == "__RESUME__":
            ctx = engine._suspended_task
            if ctx is None:
                continue  # stale resume signal, ignore
            engine._suspended_task = None
            idle.clear()
            result = engine._execute_autonomous_reactive(
                ctx.goal, plan_hint=ctx.plan_hint, resume_ctx=ctx)
            _after_run(engine, telemetry, memory, result, confidence=1.0)
            continue

        # ── New task — discard any suspended state ────────────────
        engine._suspended_task = None
        idle.clear()
        # ... existing dispatch logic (intent → router → execute) ...
```

### 5. Coordinator changes (`coordinator/server.py`)

Minimal — only event tracking:

```python
elif t == "execution.suspended":
    conn.status = "suspended"
    conn.block = {"type": "suspended", "step_index": d.get("step_index"),
                  "goal": d.get("goal"), "reason": d.get("reason")}
elif t == "execution.steered":
    # Activity log entry, don't change status
    pass
elif t == "execution.resumed":
    conn.status = "running"
    conn.block = None
```

The `steer` and `new_task` commands are relayed through `_relay_command` unchanged (already works — coordinator just forwards `{type:"command", command:"...", payload:{...}}`).

### 6. Frontend UI (Remote Command Center)

Three-state dispatch bar:

| State | Input placeholder | Enter action | Primary button | Secondary |
|-------|------------------|-------------|---------------|-----------|
| **Idle** | "Describe a new task..." | New Task | ▶ Dispatch | — |
| **Running** | "Steer: amend the task..." | Steer | ⇢ Steer | ✋ Halt / ⊘ New Task |
| **Suspended** | "Instruction before resuming..." | Resume | ▶ Resume | ⊘ New Task |

"Remember this" checkbox (default: checked) accompanies the steer input.

---

## Steer Command Payload

```json
{"command": "steer", "payload": {"text": "also upload resume.pdf", "remember": true}}
{"command": "new_task", "payload": {"text": "open Slack instead"}}
```

---

## Task Graph Recording

Steers are recorded as `InterventionEvent(trigger="steer")` on the `RunTrace`. The coalescer handles them identically to workflow interventions:

- `flag="save_as_rule"` (remember=true) → baked into task graph as conditional clause → promoted workflow inherits
- `flag="one_off"` (remember=false) → audit-only, not baked

For resumed tasks: `RunTrace` gets a `resumed_from` field linking to the prior halted trace. Coalescer merges both into the same task graph.

---

## Performance Guarantee

On the critical path (agent running, no steers):
- Per-step overhead: **~0.2μs** (two `Queue.get_nowait()` raising `Empty`)
- Against step duration of 3000-5000ms, this is **0.000004%** overhead
- No new threads, no new network calls, no new allocations on hot path
- `SuspendedTask` creation only on halt/fail (off hot path)

---

## Testing Plan

1. **Unit tests:** Engine steer inject + drain + goal mutation; SuspendedTask save/restore; resume with context
2. **Integration:** relay_client command routing (running vs suspended); dashboard /api/steer endpoint
3. **E2E:** Full coordinator → relay → engine → steer → resumed flow (cloudflared multi-machine)
