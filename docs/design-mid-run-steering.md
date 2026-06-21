# Design: Mid-Run Steering for Autonomous Tasks

## Problem Statement

Today, every dispatch through the Command Center creates a **new task** — it goes through the intent queue → vector router → new execution. There is no mechanism to amend an in-flight autonomous run's goal without halting first and starting from scratch.

**Workflow-based runs** have `workflow_control.py` intervention points at milestone boundaries (pause → steer → resume), but **autonomous runs** only offer:
- `halt` (hard stop at next step boundary — engine's `_halt_flag`)
- `override` (only applies when blocked at a monitor gate — sets `_pending_override` for the next single step)

The user wants: *"If you halt something that's mid-turn, you can then prompt in the current flow to let it continue with updated intent."*

---

## Current Architecture (Summary)

### Execution Loop (autonomous reactive)

```
main.py loop:
  raw = remote_intents.get()   ← blocks (CLI, frontend, coordinator all feed here)
  engine.execute_autonomous(raw)   ← SYNCHRONOUS, blocking until done/halted/failed
  idle.set()
```

**Critical point:** The main loop is blocked during execution. A new intent arriving in the queue while a run is active simply queues behind the current run and executes *after* it completes.

### Engine's `_execute_autonomous_reactive()` (the hot loop)

```python
for i in range(max_steps):
    if self._halt_flag.is_set():       # ← checked at EVERY step boundary
        emit("execution.halted")
        break
    verdict = self._check_monitor(...)  # policy + verifier + approval gate
    if verdict == "halt": break
    result = self._agent_s.predict_autonomous(goal, i, memory_hint, plan_hint)
    self._exec_agent_code(result.code)
```

Key observations:
1. `goal` is a local variable — immutable once the loop starts.
2. `plan_hint` is a local variable — immutable once the loop starts.
3. `_pending_override` only applies to routine-based step instructions (`_build_instruction`), NOT autonomous turns.
4. `_halt_flag` is the only externally-settable boolean the loop checks.
5. The loop has no `steer` check at step boundaries.

### What Happens on Halt

1. `relay_client._apply_command("halt")` → `set_decision("halt")` + `engine.request_halt()`
2. Engine sets `_halt_flag` (a `threading.Event`)
3. At the **next step boundary** (top of the for-loop), the flag is detected
4. Loop breaks with status="aborted"
5. `execution.halted` event is emitted
6. `submit_trace(RunTrace(...))` persists the run for coalescing
7. `main.py` sets `idle`, and the next intent in the queue (if any) starts a **new** run

**State preserved after halt:**
- The task graph (nodes/edges accumulated so far) is persisted via the coalescer
- `last_step_records` has the step-by-step history
- The `RunTrace` (with interventions) is journaled
- The physical screen state is unchanged (whatever the agent did remains)

**State NOT preserved:**
- The `goal` variable
- The `plan_hint` (the LLM-drafted plan)
- The step index (re-running starts from step 0)
- Any in-memory variables extracted during the run

---

## Proposed Design: "Resume with Amended Intent"

### Core Idea

After a halt, allow the operator to **resume** the existing run context with an amended goal, rather than starting from scratch. The screen state is already where the agent left off; the plan can be re-derived from the current position.

### Phase A — "Steer" (amend goal mid-run, no halt needed)

**New engine primitive: `request_steer(new_goal: str)`**

```python
# engine.py
self._steer_queue: queue.Queue[str] = queue.Queue()  # external producers
self._steer_text: str = ""                            # consumed per-step

def request_steer(self, text: str) -> None:
    """Inject a goal amendment. Consumed at the next step boundary."""
    self._steer_queue.put(text)
```

**In the autonomous reactive loop, after the halt check:**

```python
# Steer check — update the goal from external sources (Command Center)
try:
    steer = self._steer_queue.get_nowait()
    goal = f"{goal}\n\n[OPERATOR STEER at step {i}]: {steer}"
    # Also re-derive plan_hint if using planned mode
    event_bus.emit("execution.steered", {
        "run_id": run_id, "step_index": i, "steer": steer,
    })
    # Record as an intervention for the coalescer/task graph
    self._interventions.append(InterventionEvent(
        step_index=i, trigger="steer", decision="override",
        instruction=steer, flag="save_as_rule",
        node_key=self._step_ms.get(i, {}).get("key", ""),
        scenario="operator amended goal mid-run", ts=time.time(),
    ))
except queue.Empty:
    pass
```

**Relay client wiring:**

```python
# relay_client.py
elif command == "steer":
    text = (payload.get("text") or "").strip()
    if text:
        self._engine.request_steer(text)
        event_bus.emit("remote.steer", {"text": text, "source": "command-center"})
```

**Frontend:**
- When a run is active (`c.selected?.status === "running"`), the dispatch bar changes behavior:
  - **"Steer"** button (primary) — sends `command: "steer"` to amend the current run
  - **"New task"** link (secondary) — halts + dispatches as a new intent (current behavior)

### Phase B — "Resume from Halt" (continue from where we left off)

**Problem:** After a halt, the synchronous loop has already exited. The run is complete (status="aborted"). To "resume," we need to start a new run but **carry forward context**.

**Design:**

1. **Preserve halt context** in the engine:
```python
@dataclass
class HaltContext:
    task_key: str
    goal: str
    plan_hint: str
    step_index: int
    variables: dict[str, str]
    executed: list[RoutineStep]
    graph: TaskGraph  # current state (not yet coalesced)
    interventions: list[InterventionEvent]
```

2. **On halt, save the context:**
```python
# In _execute_autonomous_reactive, on halt:
self._last_halt_context = HaltContext(
    task_key=task_key, goal=goal, plan_hint=plan_hint,
    step_index=i, variables=variables,
    executed=list(executed), graph=graph,
    interventions=list(self._interventions),
)
```

3. **New command: `resume`**
```python
# relay_client.py
elif command == "resume":
    text = (payload.get("text") or "").strip()
    if text:
        self._remote_intents.put(f"__RESUME__:{text}")
```

4. **In main.py, detect the resume sentinel:**
```python
raw = remote_intents.get()
if raw.startswith("__RESUME__:"):
    amended_goal = raw[len("__RESUME__:"):]
    result = engine.resume_autonomous(amended_goal)
else:
    # normal dispatch...
```

5. **`engine.resume_autonomous(amended_goal)`:**
```python
def resume_autonomous(self, amended_goal: str) -> ExecutionResult:
    ctx = self._last_halt_context
    if ctx is None:
        return self.execute_autonomous(amended_goal)  # no context → fresh start
    
    # Merge the amendment with the original goal
    full_goal = f"{ctx.goal}\n\n[RESUMED at step {ctx.step_index}]: {amended_goal}"
    
    # Re-derive plan if using planned mode
    # Start the reactive loop from step 0 but with the screen already
    # at the state left by the previous run (no reset needed — the physical
    # screen IS the context). The agent sees the current screen + the
    # merged goal and acts from there.
    
    # Feed the prior graph so the agent knows what's already been done
    memory_hint = f"Already completed steps: {[s.description for s in ctx.executed]}"
    
    return self._execute_autonomous_reactive(
        full_goal, plan_hint=memory_hint,
    )
```

### Phase C — Proactive Checkpoint Steers (auto-pause for long runs)

**Idea:** Every N steps (configurable, e.g., 5), the autonomous loop checks a "steer queue" and emits a `step.checkpoint` event. The UI shows a subtle "open to steer" indicator.

This is essentially Phase A but with explicit UI affordance at checkpoint boundaries, and optionally a brief pause (100ms) where the engine drains the steer queue.

---

## Task Graph / Workflow Recording Implications

### How Steers Are Recorded

All phases record steers as `InterventionEvent` objects on the `RunTrace`:

```python
InterventionEvent(
    step_index=i,
    trigger="steer",          # new trigger type
    decision="override",
    instruction="also fill in the Projects field",
    flag="save_as_rule",      # or "one_off" if the operator didn't check "remember"
    node_key="fill::...",     # milestone this steer attaches to
    scenario="operator amended goal mid-run",
    ts=time.time(),
)
```

### Coalescer Behavior

The coalescer already handles `InterventionEvent` objects — it bakes `flag="save_as_rule"` interventions into the task graph as conditionals:

```
node: "Enter details"
  conditional: if "operator amended goal mid-run" → "also fill in the Projects field"
```

For **resume** (Phase B), the `RunTrace` would be marked with a `resumed_from` field:

```python
@dataclass
class RunTrace:
    ...
    resumed_from: str = ""  # run_id this was resumed from (empty = fresh)
```

The coalescer merges the continued run's milestones into the same task graph as the original halted run, building a richer picture of the full task.

### Workflow Promotion

When a task graph is promoted into a dispatchable Workflow:
- Steers become conditional clauses on their respective milestone nodes
- A milestone with a steer becomes a "taught" node that auto-resolves the steer in future runs (the existing `_taught_resolution` mechanism in the engine's monitor check)

---

## Command Additions Summary

| Command | Relay Client | Engine | When Available |
|---------|-------------|--------|----------------|
| `steer` | `engine.request_steer(text)` | Amends `goal` at next step boundary | During a running autonomous task |
| `resume` | `remote_intents.put("__RESUME__:text")` | `engine.resume_autonomous(text)` | After a halted autonomous task |

---

## UI Changes (Remote Command Center)

### Dispatch Bar States

| Agent State | Primary Action | Secondary Action |
|-------------|---------------|-----------------|
| Idle | **Dispatch** (new task) | — |
| Running (autonomous) | **Steer** (amend goal) | "Halt + New task" |
| Running (workflow) | *(existing Pause/Resume/Intervene)* | — |
| Halted (context available) | **Resume** (continue with amended goal) | "New task" |
| Halted (no context) | **Dispatch** (new task) | — |

### Steer Feedback

When a steer is applied, the coordinator receives `execution.steered` event and the UI shows:
- Toast: "Steer applied at step {N}"
- Activity log entry: `execution.steered → "{text}"`

---

## Open Questions

1. **Plan re-derivation on steer:** Should a mid-run steer trigger re-planning (re-call the planner LLM with the updated goal + already-executed steps)? This would be more powerful but adds latency at the steer point.

2. **Multi-steer:** Can an operator steer multiple times within a run? (Yes — the queue allows it, but UI should make it clear the prior steer was consumed.)

3. **Resume vs. fresh:** Should resume be automatic (any intent after a halt uses the halt context) or explicit (a separate "Resume" button)? Explicit is safer — avoids accidentally extending a halted run.

4. **Workflow runs:** For workflow-based runs, the existing `workflow.intervene` + pause/resume is sufficient. No changes needed there. Phase A's `steer` only applies to autonomous runs.

5. **Task graph merging on resume:** If a run is halted at step 5 and resumed with a new goal, the coalescer receives two RunTraces (one for steps 0-5 "aborted", one for the resumed run). Should they be merged into one graph, or is two entries fine? Proposal: link them via `resumed_from` and let the coalescer optionally merge post-hoc.

---

## Implementation Priority

| Phase | Effort | Backend Changes | Frontend Changes |
|-------|--------|----------------|-----------------|
| A (Steer) | ~4-6 hrs | `engine.py` + `relay_client.py` + coordinator event | Dispatch bar mode switch |
| B (Resume) | ~6-8 hrs | `engine.py` + `main.py` + `relay_client.py` | Resume button + halt context indicator |
| C (Checkpoints) | ~2-3 hrs | Engine loop + event | Subtle UI badge |

Phase A is the highest-value, lowest-risk change. Phase B is the user's explicit ask ("halt then reprompt in the current flow"). Phase C is polish.
