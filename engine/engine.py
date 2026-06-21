"""
ShepherdExecutionEngine

LIVE mode       — Agent S plans actions against the recorded demonstration; pyautogui actuates.
                  Router generates WORKFLOW + ROUTINE candidates → LLM filter → autonomous fallback.
LOCKED mode     — Deterministic verbatim replay of pre-mapped steps (offline demo floor).
                  Router uses keyword-only routine resolution (zero-API, fully offline).
AUTONOMOUS mode — Agent S receives the raw user goal and loops until DONE/FAIL/max steps.
                  Router generates WORKFLOW candidates only (no routines); if a saved workflow
                  matches it is dispatched, otherwise free-form autonomous execution proceeds.

The click path is synchronous and sacred.
Nothing async, networked, or ML-based runs inside a routine's step sequence.
"""
import subprocess
import threading
import time
import uuid
import json
from typing import Optional

import pyautogui

import config as _cfg
from config import (
    FEATURES, EXECUTION_MODE, AUTONOMOUS_MAX_STEPS, AUTONOMOUS_PLAN_FIRST,
    AUTONOMOUS_USE_MEMORY,
    AGENT_S_MODEL, AGENT_S_ENGINE_TYPE, PLANNER_MODEL, PLANNER_ENGINE_TYPE,
)
from shepherd_types import (
    AUTONOMOUS_ROUTINE_ID,
    ExecutionResult,
    ResolvedRoutine,
    RoutineDefinition,
    RoutineStep,
    StepRecord,
    RunTrace,
    InterventionEvent,
)
from engine.coords import get as get_coord
from engine.text_input import enter_text, hotkey as do_hotkey
from engine.routines import get_routine
from engine.agent_s_adapter import AgentSAdapter
from engine.routine_planner import RoutinePlanner, normalize_open_app_step
from engine.task_graph import TaskGraphStore, summarize, milestone_key
from engine.coalescer import submit as submit_trace
from dashboard.events import event_bus
from telemetry import audit_log
from telemetry import request_log as rlog
from telemetry.telemetry import current_trace_id
from telemetry.sentry_init import capture as sentry_capture
from services import policy_engine
from telemetry.agent_trace import (
    apply_chain_span,
    apply_llm_plan_span,
    apply_tool_act_span,
    summarize_agent_code,
)

pyautogui.FAILSAFE = True   # slam mouse to top-left corner to abort
pyautogui.PAUSE    = 0.3    # deliberate, watchable motion — this is the wow factor

_APP_SETTLE = 2.0           # seconds to wait after open_app


def activate_app(name: str, settle: float = 1.2) -> None:
    """Deterministically bring a macOS app to the foreground and give it focus.

    Exposed to autonomous chained-action code. Use this before typing so keystrokes
    land in the intended window — Spotlight launches are unreliable about stealing
    focus, which is what sends text into Spotlight/the wrong field. `open -a` both
    launches the app (if needed) and activates it (brings a running app to front),
    so it covers both cases without the AppleScript `activate` — which would require
    macOS Automation (TCC) permission and could stall on a consent prompt."""
    try:
        subprocess.run(["open", "-a", name], check=False, timeout=10)
    except Exception as e:  # never let focus-setting abort the run
        print(f"[engine] activate_app({name!r}) failed (non-fatal): {e}")
    time.sleep(settle)


def type_text(text: str) -> None:
    """Reliable text entry for the autonomous agent — paste via the macOS clipboard
    (pbcopy + AppleScript Cmd+V) instead of typing character-by-character.

    pyautogui.typewrite holds Shift to make capitals; at low intervals the release
    lags, so letters after a capital stay uppercase ('Introduction' -> 'INTRODUCTION')
    until it catches up. Pasting sends no per-character keystrokes, so capitalization,
    punctuation and newlines come out exactly as written. Falls back to typewrite if
    the clipboard path fails. The focused field must already be active (click/select
    first), same as typewrite."""
    if not text:
        return
    try:
        subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
        time.sleep(0.1)
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using command down'],
            check=False, timeout=5,
        )
    except Exception as e:
        print(f"[engine] type_text paste failed ({e}); falling back to typewrite")
        pyautogui.typewrite(text, interval=0.05)


class ShepherdExecutionEngine:
    def __init__(
        self,
        coords: dict,
        telemetry,
        mode: str = EXECUTION_MODE,
        agent_s=None,
        evolution=None,
        planner=None,
    ) -> None:
        self._coords    = coords
        self._telemetry = telemetry
        self._mode      = mode
        self._agent_s   = agent_s if agent_s is not None else AgentSAdapter()
        self._planner   = planner if planner is not None else RoutinePlanner()
        self._evolution = evolution  # RoutineEvolution | None — injected by main.py
        self._graphs    = TaskGraphStore()
        self._active_graph = None   # task graph loaded as reference for the current run
        self._step_ms      = {}     # fine-step index → milestone reference for Agent S
        self.last_step_records: list[StepRecord] = []
        self.last_trace_id: Optional[str] = None  # Phoenix trace id of the most recent run
        self._interventions: list[InterventionEvent] = []  # human teaching this run → coalescer
        self._halt_flag = threading.Event()
        self._pending_override: str = ""
        # Rate-limit tracking (resets each run)
        self._run_action_times: list[float] = []
        self._run_variables: dict[str, str] = {}

    def request_halt(self) -> None:
        """Set by monitor_agent or spoken 'stop' command. Checked at each step boundary."""
        self._halt_flag.set()

    def effective_mode(self) -> str:
        """The legacy LIVE/LOCKED/AUTONOMOUS enum for this run. Compatibility shim:
        the canonical knobs are USE_ROUTER/ROUTINE_REPLAY (config derives the enum
        from them). A /api/mode runtime override still wins when set."""
        if _cfg._runtime_mode:
            return _cfg._runtime_mode.upper()
        return self._mode.upper()

    def execute_autonomous(self, goal: str) -> ExecutionResult:
        """
        Free-form goal execution.

        When AUTONOMOUS_PLAN_FIRST is enabled (default), an LLM drafts a
        routines.json-style step list, then each step is passed to Agent S
        via the standard LIVE execute loop. Otherwise falls back to the
        reactive loop (full goal every turn until DONE/FAIL).
        """
        if AUTONOMOUS_PLAN_FIRST:
            return self._execute_autonomous_planned(goal)
        return self._execute_autonomous_reactive(goal)

    def _execute_autonomous_planned(self, goal: str) -> ExecutionResult:
        event_bus.emit("routine.planning", {"goal": goal})
        print(f"[planner] Drafting routine for: {goal}")

        # Memory recall is opt-in (AUTONOMOUS_USE_MEMORY); off by default so the
        # planner drafts fresh rather than reusing this goal's prior milestone trail.
        task_key = self._autonomous_task_key(goal)
        prior_milestones: list = []
        if AUTONOMOUS_USE_MEMORY:
            mem = self._graphs.load(task_key, {})
            prior_milestones = [n.label for n in mem.nodes]
            if prior_milestones:
                print(f"[planner] recalled {len(prior_milestones)} milestone(s) from memory "
                      f"(run #{mem.run_count})")

        # Single parent span so the planning LLM call and the execution loop nest
        # under ONE trace in Phoenix instead of appearing as two separate roots.
        with self._telemetry.span("routine.run", oi_kind="CHAIN") as run_span:
            run_span.set_attribute("routine.goal", goal)
            apply_chain_span(run_span, input_text=goal, output_text="")

            plan_json = ""
            try:
                with self._telemetry.span("routine.plan", oi_kind="LLM") as plan_span:
                    routine, extracted = self._planner.draft(goal, prior_milestones)
                    plan_json = json.dumps(
                        [{"action": s.action, "description": s.description or s.action}
                         for s in routine.steps],
                        indent=2,
                    )
                    apply_llm_plan_span(
                        plan_span,
                        instruction=goal,
                        response=plan_json,
                        outcome="planned",
                        model=PLANNER_MODEL,
                        provider=PLANNER_ENGINE_TYPE,
                    )
            except Exception as e:
                print(f"[planner] Failed ({e}) — falling back to reactive Agent S loop")
                event_bus.emit("routine.plan_failed", {"goal": goal, "error": str(e)})
                return self._execute_autonomous_reactive(goal)

            for i, step in enumerate(routine.steps):
                print(f"[planner]   {i:02d} [{step.action}] {step.description or step.action}")

            event_bus.emit("routine.planned", {
                "goal":        goal,
                "description": routine.description,
                "total_steps": len(routine.steps),
                "steps": [
                    {
                        "index":       i,
                        "action":      s.action,
                        "description": s.description or s.action,
                    }
                    for i, s in enumerate(routine.steps)
                ],
            })

            # Hand the drafted plan to the reactive Agent S loop as guidance, then
            # execute it with screenshots at sensible intervals (chained), adapting
            # to the real screen — rather than blindly running a keyboard-only script.
            plan_hint = (
                "PLAN (your roadmap — follow it in order, but adapt to what is "
                "actually on screen each turn):\n"
                + "\n".join(f"  {i + 1}. {s.description or s.action}"
                            for i, s in enumerate(routine.steps))
            )
            return self._execute_autonomous_reactive(goal, plan_hint=plan_hint)

    def _execute_autonomous_reactive(self, goal: str, plan_hint: str = "") -> ExecutionResult:
        """
        Reactive loop — Agent S plans each action from the full intent
        and current screenshot until DONE, FAIL, halt, or step budget exhausted.
        """
        self._halt_flag.clear()
        self.last_step_records = []
        self.last_trace_id = None
        self._interventions = []
        self._agent_s.reset_autonomous()
        run_id = str(uuid.uuid4())[:8]
        started_at = time.time()
        max_steps = AUTONOMOUS_MAX_STEPS
        variables = {"GOAL": goal}

        # Per-goal memory graph: load this goal's prior milestones and feed them to
        # the planner (use); the executed trace is handed to the coalescer at the end
        # (generate) — same milestone+edge scheme as the planned path.
        task_key = self._autonomous_task_key(goal)
        graph = self._graphs.load(task_key, variables)
        self._active_graph = graph
        was_known = self._graphs.is_known(graph)
        # Memory recall is opt-in. By default the loop does a fresh Agent S run and
        # does NOT feed prior milestones to the planner.
        memory_hint = ""
        if AUTONOMOUS_USE_MEMORY and graph.nodes:
            memory_hint = RoutinePlanner._memory_hint([n.label for n in graph.nodes])
            print(f"[autonomous] recalled {len(graph.nodes)} milestone(s) from memory "
                  f"(run #{graph.run_count})")

        event_bus.emit("execution.start", {
            "run_id":      run_id,
            "routine_id":  AUTONOMOUS_ROUTINE_ID,
            "mode":        "AUTONOMOUS",
            "total_steps": max_steps,
            "variables":   variables,
            "goal":        goal,
            "steps":       [],
        })
        rlog.request_started(run_id, goal, "AUTONOMOUS", True, AUTONOMOUS_ROUTINE_ID)
        event_bus.emit("task.graph.loaded", {
            "run_id":     run_id,
            "routine_id": task_key,
            "known":      was_known,
            "run_count":  graph.run_count,
            "node_count": len(graph.nodes),
        })

        steps_done = 0
        error: Optional[str] = None
        status = "completed"
        executed: list[RoutineStep] = []   # one step per action turn → coalescer
        monitor_step = RoutineStep(action="agent_s", description=goal)

        try:
            with self._telemetry.span("routine.execute", oi_kind="CHAIN") as span:
                self.last_trace_id = current_trace_id()
                span.set_attribute("routine.id", AUTONOMOUS_ROUTINE_ID)
                span.set_attribute("routine.mode", "AUTONOMOUS")
                span.set_attribute("routine.goal", goal)

                for i in range(max_steps):
                    if self._halt_flag.is_set():
                        status = "aborted"
                        event_bus.emit("execution.halted", {
                            "run_id": run_id, "step_index": i, "reason": "halt_requested",
                        })
                        break

                    verdict = self._check_monitor(monitor_step, i, run_id, AUTONOMOUS_ROUTINE_ID)
                    if verdict == "halt":
                        status = "aborted"
                        event_bus.emit("execution.halted", {
                            "run_id": run_id, "step_index": i, "reason": "monitor_halt",
                        })
                        break

                    step_t0 = time.time()
                    event_bus.emit("step.start", {
                        "run_id":      run_id,
                        "index":       i,
                        "action":      "agent_s",
                        "description": goal,
                        "total":       max_steps,
                    })
                    event_bus.emit("step.agent_s_thinking", {
                        "run_id": run_id, "index": i, "description": goal,
                    })

                    apps, tools = None, None
                    with self._telemetry.span("agent_s.plan", oi_kind="LLM") as plan_span:
                        result = self._agent_s.predict_autonomous(
                            goal, i, memory_hint, plan_hint=plan_hint)
                        apps, tools = summarize_agent_code(result.code)
                        apply_llm_plan_span(
                            plan_span,
                            instruction=goal,
                            response=result.raw or "",
                            outcome=result.outcome,
                            model=AGENT_S_MODEL,
                            provider=AGENT_S_ENGINE_TYPE,
                            code=result.code,
                            apps=apps or None,
                            tools=tools or None,
                        )
                    rlog.agent_turn(run_id, i, self._agent_s.last_reasoning,
                                    result.code or "", result.outcome)
                    step_status = "completed"
                    step_error: Optional[str] = None

                    if result.outcome == "done":
                        rlog.note(run_id, f"Agent S reports DONE after {steps_done} actions")
                        steps_done += 1
                        dur_ms = int((time.time() - step_t0) * 1000)
                        self.last_step_records.append(StepRecord(
                            index=i, action="agent_s", target=None,
                            status="completed", started_at=step_t0, duration_ms=dur_ms,
                        ))
                        event_bus.emit("step.complete", {
                            "run_id": run_id, "index": i, "status": "completed", "duration_ms": dur_ms,
                        })
                        break

                    if result.outcome == "fail":
                        status = "failed"
                        error = result.raw or "Agent S reported failure"
                        step_status = "failed"
                        step_error = error
                        dur_ms = int((time.time() - step_t0) * 1000)
                        self.last_step_records.append(StepRecord(
                            index=i, action="agent_s", target=None,
                            status="failed", started_at=step_t0, duration_ms=dur_ms, error=error,
                        ))
                        event_bus.emit("step.error", {
                            "run_id": run_id, "index": i, "error": error,
                        })
                        break

                    if result.outcome == "wait":
                        dur_ms = int((time.time() - step_t0) * 1000)
                        self.last_step_records.append(StepRecord(
                            index=i, action="agent_s", target=None,
                            status="completed", started_at=step_t0, duration_ms=dur_ms,
                            deviation="wait",
                        ))
                        event_bus.emit("step.complete", {
                            "run_id": run_id, "index": i, "status": "completed",
                            "duration_ms": dur_ms, "deviation": "wait",
                        })
                        continue

                    if result.outcome != "action" or not result.code:
                        status = "failed"
                        error = "Agent S unavailable or returned no actionable code"
                        step_status = "failed"
                        step_error = error
                        dur_ms = int((time.time() - step_t0) * 1000)
                        self.last_step_records.append(StepRecord(
                            index=i, action="agent_s", target=None,
                            status="failed", started_at=step_t0, duration_ms=dur_ms, error=error,
                        ))
                        event_bus.emit("step.error", {
                            "run_id": run_id, "index": i, "error": error,
                        })
                        break

                    with self._telemetry.span("action.agent_s", oi_kind="TOOL") as s:
                        s.set_attribute("action.type", "agent_s")
                        s.set_attribute("action.index", i)
                        s.set_attribute("action.agent_s", True)
                        act_status = "ok"
                        try:
                            self._exec_agent_code(result.code)
                            steps_done += 1
                        except Exception as exc:
                            act_status = "error"
                            step_status = "failed"
                            step_error = str(exc)
                            error = step_error
                            status = "failed"
                            print(f"[engine] autonomous step {i} failed: {step_error}")
                            sentry_capture(exc, tags={
                                "routine_id": AUTONOMOUS_ROUTINE_ID,
                                "mode": "AUTONOMOUS",
                                "action": "agent_s",
                                "step_index": i,
                            }, context={
                                "run_id": run_id,
                                "goal": goal,
                                "step_index": i,
                                "code": result.code,
                                "variables": variables,
                            })
                            s.set_attribute("error.message", step_error)
                            event_bus.emit("step.error", {
                                "run_id": run_id, "index": i, "error": step_error,
                            })
                            apply_tool_act_span(
                                s, goal=goal, code=result.code,
                                apps=apps or None, tools=tools or None, status=act_status,
                            )
                            break
                        apply_tool_act_span(
                            s, goal=goal, code=result.code,
                            apps=apps or None, tools=tools or None, status=act_status,
                        )

                    dur_ms = int((time.time() - step_t0) * 1000)
                    rlog.step_result(run_id, i, step_status, dur_ms, step_error or "")
                    # Record what this turn did as one trace step; the coalescer
                    # LLM-segments the whole trace into milestones + edges at the end
                    # (same scheme as the planned path — no per-step node dump).
                    executed.append(RoutineStep(
                        action="agent_s",
                        description=self._agent_s.last_reasoning or "action",
                    ))
                    self.last_step_records.append(StepRecord(
                        index=i, action="agent_s", target=None,
                        status=step_status, started_at=step_t0, duration_ms=dur_ms, error=step_error,
                    ))
                    event_bus.emit("step.complete", {
                        "run_id": run_id, "index": i, "status": step_status, "duration_ms": dur_ms,
                    })

                else:
                    status = "failed"
                    error = f"Step budget exhausted ({max_steps} steps)"
                    rlog.note(run_id, error)
        except KeyboardInterrupt:
            # Ctrl-C → treat as a graceful abort: fall through to record/emit/persist
            # below so the run still shows on the frontend with what it did.
            status = "aborted"
            error = "interrupted by user (Ctrl-C)"
            event_bus.emit("execution.halted", {
                "run_id": run_id, "step_index": steps_done, "reason": "keyboard_interrupt",
            })
            rlog.note(run_id, "interrupted by user (Ctrl-C)")

            chain_out = f"status={status}, steps={steps_done}"
            if error:
                chain_out += f", error={error}"
            apply_chain_span(span, input_text=goal, output_text=chain_out)

        ended_at = time.time()
        result = ExecutionResult(
            routine_id=AUTONOMOUS_ROUTINE_ID,
            status=status,
            steps_completed=steps_done,
            error=error,
            duration_ms=int((ended_at - started_at) * 1000),
            variables=variables,
            started_at=started_at,
            ended_at=ended_at,
            run_id=run_id,
        )
        event_bus.emit("execution.complete", {
            "run_id":          run_id,
            "status":          status,
            "steps_completed": steps_done,
            "duration_ms":     result.duration_ms,
        })

        # Hand the executed trace to the coalescer (COLD PATH): it LLM-segments into
        # milestones + edges and persists under task_key — the same per-goal memory
        # graph the planner consulted. Runs async; survives interrupt via the journal.
        deviations = [
            {"step_index": r.index, "reason": r.deviation}
            for r in self.last_step_records if r.deviation
        ]
        submit_trace(RunTrace(
            run_id=run_id,
            routine_id=task_key,
            variables=variables,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            executed=executed,
            interventions=list(self._interventions),
            deviations=deviations,
            intent_text=goal,
        ))
        self._active_graph = None

        rlog.request_finished(run_id, status, steps_done, result.duration_ms,
                              [s.description for s in executed])
        return result

    @staticmethod
    def _autonomous_task_key(goal: str) -> str:
        slug = "".join(c if c.isalnum() else "_" for c in (goal or "").lower()).strip("_")
        return "AUTONOMOUS::" + (slug[:48] or "goal")

    def execute(
        self,
        resolved: ResolvedRoutine,
        *,
        routine: RoutineDefinition | None = None,
        mode_override: str | None = None,
        graph_key: str | None = None,
        intent_text: str = "",
    ) -> ExecutionResult:
        self._halt_flag.clear()
        self.last_step_records = []
        self.last_trace_id = None
        self._interventions = []
        self._run_action_times = []
        self._armoriq_denial = None   # set when ArmorIQ refuses the plan's intent
        self._armoriq_token = None    # signed intent token when authorized
        # Fresh Agent S trajectory per run — its reflection/trajectory state is
        # per-task and must not leak across runs.
        self._agent_s.reset()
        run_id     = str(uuid.uuid4())[:8]
        started_at = time.time()

        if mode_override:
            self._mode = mode_override.upper()
        elif _cfg._runtime_mode:
            self._mode = _cfg._runtime_mode.upper()

        routine   = routine if routine is not None else get_routine(resolved.routine_id)
        variables = resolved.variables
        self._run_variables = variables

        # The graph this run reads/writes. Defaults to the routine id, but an
        # autonomous goal passes a per-goal key so each distinct goal keeps its
        # own memory graph (instead of all autonomous runs sharing one).
        gkey = graph_key or resolved.routine_id

        # ── Load this task's persistent graph as a reference ────────────────────
        # The graph is coarse (milestones, not clicks). On a repeat run it tells us
        # (and Agent S) what's already been done; new milestones get appended below.
        graph = self._graphs.load(gkey, variables)
        self._active_graph = graph
        was_known   = self._graphs.is_known(graph)
        prior_keys  = {n.key for n in graph.nodes}
        prior_by_key = {n.key: n for n in graph.nodes}

        # Collapse this run's planned steps into milestones for the recall overlay
        # and Agent S reference. (Executed milestones are re-derived & saved at the end.)
        planned_ms, step_to_ms = summarize(routine.steps, variables)
        self._step_ms = {}
        for idx, mi in step_to_ms.items():
            m = planned_ms[mi]
            key = milestone_key(m["kind"], m["value"], m["label"])
            prior = prior_by_key.get(key)
            self._step_ms[idx] = {
                "key":        key,
                "label":      m["label"],
                "times_seen": prior.times_seen if prior else 0,
            }

        event_bus.emit("execution.start", {
            "run_id":      run_id,
            "routine_id":  resolved.routine_id,
            "mode":        self._mode,
            "total_steps": len(routine.steps),
            "variables":   variables,
            "steps": [
                {
                    "index":       i,
                    "action":      s.action,
                    "description": s.description or s.action,
                    "high_stakes": i in routine.high_stakes_steps,
                }
                for i, s in enumerate(routine.steps)
            ],
        })

        # ── ArmorIQ intent authorization (boundary, before any action) ──────────
        # Capture the plan and request a signed intent token gated by containment.
        # A denial trips the halt flag so the run aborts at the first boundary
        # check below (never mid-click); the token is recorded as cryptographic
        # proof of authorized intent. Fully no-op when ArmorIQ is off.
        if FEATURES["armoriq"]:
            try:
                from services import armoriq_guard
                auth = armoriq_guard.authorize_run(resolved.routine_id, routine.steps, variables)
                if auth is not None and auth["authorized"]:
                    self._armoriq_token = auth.get("token")
                    event_bus.emit("armoriq.authorized", {
                        "run_id": run_id, "plan_hash": auth.get("plan_hash"),
                        "reason": auth.get("reason"),
                    })
                elif auth is not None:
                    self._armoriq_denial = auth.get("reason") or "ArmorIQ denied the plan"
                    self._halt_flag.set()
                    event_bus.emit("armoriq.denied", {
                        "run_id": run_id, "reason": self._armoriq_denial,
                    })
            except Exception as e:
                print(f"[armoriq] gate skipped (non-fatal): {e}")

        # Tell the dashboard, per fine step, which milestone it belongs to and whether
        # that milestone is already in the graph (recalled) or new this run.
        loaded_steps = []
        for i, s in enumerate(routine.steps):
            m = planned_ms[step_to_ms[i]]
            key = milestone_key(m["kind"], m["value"], m["label"])
            loaded_steps.append({
                "index":          i,
                "milestone":      m["label"],
                "milestone_known": key in prior_keys,
                "times_seen":     getattr(prior_by_key.get(key), "times_seen", 0),
            })
        event_bus.emit("task.graph.loaded", {
            "run_id":     run_id,
            # The graph is stored under gkey (per-goal for autonomous runs); emit
            # that so the dashboard fetches the same graph the run reads/writes.
            "routine_id": gkey,
            "known":      was_known,
            "run_count":  graph.run_count,
            "node_count": len(graph.nodes),
            "milestones": [
                {"label": m["label"], "kind": m["kind"],
                 "known": milestone_key(m["kind"], m["value"], m["label"]) in prior_keys}
                for m in planned_ms
            ],
            "steps": loaded_steps,
        })

        steps_done = 0
        executed: list[RoutineStep] = []   # actually-performed steps → milestones at end
        error: Optional[str] = None
        status = "completed"

        # Dynamically extend monitored steps with evolution-flagged risky ones
        dynamic_risky: set[int] = set()
        if self._evolution:
            try:
                dynamic_risky = self._evolution.risky_steps(
                    resolved.routine_id, len(routine.steps)
                )
            except Exception:
                pass
        monitored = set(routine.high_stakes_steps) | dynamic_risky

        try:
            with self._telemetry.span("routine.execute", oi_kind="CHAIN") as span:
                self.last_trace_id = current_trace_id()
                span.set_attribute("routine.id",   resolved.routine_id)
                span.set_attribute("routine.mode", self._mode)
                for k, v in variables.items():
                    span.set_attribute(f"routine.variable.{k}", v)

                limits = policy_engine.get_limits()

                for i, step in enumerate(routine.steps):
                    # ── halt check (boundary, never mid-click) ──────────────────
                    if self._halt_flag.is_set():
                        status = "aborted"
                        if self._armoriq_denial:
                            error = self._armoriq_denial
                        event_bus.emit("execution.halted", {
                            "run_id": run_id, "step_index": i,
                            "reason": "armoriq_denied" if self._armoriq_denial else "halt_requested",
                        })
                        break

                    # ── containment: max steps per run ───────────────────────────
                    max_steps = limits.get("max_steps_per_run", 0)
                    if max_steps and i >= max_steps:
                        status = "aborted"
                        error = f"Containment: run exceeded max_steps_per_run={max_steps}"
                        print(f"[containment] {error}")
                        event_bus.emit("execution.halted", {
                            "run_id": run_id, "step_index": i, "reason": "containment_step_limit"
                        })
                        break

                    # ── containment: actions-per-minute rate limit ────────────────
                    max_apm = limits.get("max_actions_per_minute", 0)
                    if max_apm:
                        now_t = time.time()
                        self._run_action_times = [t for t in self._run_action_times if now_t - t < 60]
                        if len(self._run_action_times) >= max_apm:
                            status = "aborted"
                            error = f"Containment: rate limit {max_apm} actions/min exceeded"
                            print(f"[containment] {error}")
                            event_bus.emit("execution.halted", {
                                "run_id": run_id, "step_index": i, "reason": "containment_rate_limit"
                            })
                            break
                        self._run_action_times.append(now_t)

                    # ── monitor check at high-stakes + dynamically risky boundaries ─
                    if i in monitored:
                        verdict = self._check_monitor(
                            step, i, run_id, resolved.routine_id
                        )
                        if verdict == "halt":
                            status = "aborted"
                            event_bus.emit("execution.halted", {
                                "run_id": run_id, "step_index": i, "reason": "monitor_halt"
                            })
                            break

                    step_t0 = time.time()
                    event_bus.emit("step.start", {
                        "run_id":      run_id,
                        "index":       i,
                        "action":      step.action,
                        "target":      step.target,
                        "description": step.description or step.action,
                        "total":       len(routine.steps),
                    })

                    # In LIVE mode, ask Agent S to plan the action (returns executable code)
                    defined_step = step
                    if self._mode == "LIVE" and self._agent_s.available:
                        event_bus.emit("step.agent_s_thinking", {
                            "run_id": run_id, "index": i,
                            "description": step.description or step.action,
                        })
                    agent_code, step_instruction = self._live_execute(defined_step, i, routine)

                    # Deviation detection: compare code action type vs defined step
                    deviation_desc: Optional[str] = (
                        self._detect_code_deviation(defined_step, agent_code)
                        if agent_code else None
                    )
                    if deviation_desc:
                        event_bus.emit("step.deviation", {
                            "run_id":     run_id,
                            "step_index": i,
                            "expected":   defined_step.action,
                            "actual":     "agent_s",
                            "reason":     deviation_desc,
                        })

                    # Interruptability: a halt requested DURING planning (the slow LLM
                    # step) takes effect here — before any actuation — so a spoken/clicked
                    # "stop" never lands a click it could have prevented. Planning is done
                    # and nothing has actuated yet, so this is still a safe boundary.
                    if self._halt_flag.is_set():
                        status = "aborted"
                        event_bus.emit("execution.halted", {
                            "run_id": run_id, "step_index": i, "reason": "halt_requested",
                        })
                        break

                    step_status = "completed"
                    step_error: Optional[str] = None

                    span_name = "action.agent_s" if agent_code else f"action.{step.action}"
                    with self._telemetry.span(
                        span_name, oi_kind="TOOL" if agent_code else "CHAIN",
                    ) as s:
                        s.set_attribute("action.type",    "agent_s" if agent_code else step.action)
                        s.set_attribute("action.index",   i)
                        s.set_attribute("action.agent_s", bool(agent_code))
                        if step.target:
                            s.set_attribute("action.target", step.target)
                        act_status = "ok"
                        try:
                            if agent_code:
                                try:
                                    self._exec_agent_code(agent_code)
                                except Exception as agent_exc:
                                    # Agent S code failed to execute — fall back to the
                                    # deterministic defined step rather than aborting the run.
                                    print(f"[agent_s] exec failed (falling back to defined step): {agent_exc}")
                                    deviation_desc = (
                                        (deviation_desc or "agent_s") + " → fell back to defined step"
                                    )
                                    event_bus.emit("step.fallback", {
                                        "run_id": run_id, "index": i, "reason": str(agent_exc),
                                    })
                                    self._dispatch(step, variables)
                            else:
                                self._dispatch(step, variables)
                            steps_done += 1
                            executed.append(step)
                        except Exception as exc:
                            act_status = "error"
                            step_status = "failed"
                            step_error  = str(exc)
                            error       = step_error
                            status      = "failed"
                            print(f"[engine] step {i} failed: {step_error}")
                            sentry_capture(exc, tags={
                                "routine_id": resolved.routine_id,
                                "mode": self._mode,
                                "action": step.action,
                                "step_index": i,
                            }, context={
                                "run_id": run_id,
                                "step_index": i,
                                "step_description": step.description or step.action,
                                "instruction": step_instruction,
                                "variables": variables,
                            })
                            s.set_attribute("error.message", step_error)
                            event_bus.emit("step.error", {
                                "run_id": run_id, "index": i, "error": step_error
                            })
                            if agent_code:
                                apps, tools = summarize_agent_code(agent_code)
                                apply_tool_act_span(
                                    s, goal=step_instruction, code=agent_code,
                                    apps=apps or None, tools=tools or None, status=act_status,
                                )
                            else:
                                apply_chain_span(
                                    s,
                                    input_text=step_instruction,
                                    output_text=f"{step.action} → {step_status}",
                                )
                            break
                        if agent_code:
                            apps, tools = summarize_agent_code(agent_code)
                            apply_tool_act_span(
                                s, goal=step_instruction, code=agent_code,
                                apps=apps or None, tools=tools or None, status=act_status,
                            )
                        else:
                            apply_chain_span(
                                s,
                                input_text=step_instruction,
                                output_text=f"{step.action} → {step_status}",
                            )

                    dur_ms = int((time.time() - step_t0) * 1000)

                    # Timing deviation: step took >3× the historical average (≥2 s)
                    if self._evolution and deviation_desc is None:
                        try:
                            hist = self._evolution.get_stats(resolved.routine_id, i)
                            avg = hist.avg_duration_ms
                            if avg > 0 and dur_ms > avg * 3 and dur_ms > 2000:
                                timing_dev = f"timing: {dur_ms}ms vs avg {avg}ms"
                                deviation_desc = timing_dev
                                event_bus.emit("step.deviation", {
                                    "run_id": run_id, "step_index": i,
                                    "reason": timing_dev, "type": "timing",
                                })
                        except Exception:
                            pass

                    record = StepRecord(
                        index=i, action=step.action, target=step.target,
                        status=step_status, started_at=step_t0,
                        duration_ms=dur_ms, error=step_error,
                        deviation=deviation_desc,
                    )
                    self.last_step_records.append(record)

                    # Tamper-evident audit trail — hash-chained JSONL
                    audit_log.append(
                        run_id=run_id,
                        step_index=i,
                        action=step.action,
                        status=step_status,
                        duration_ms=dur_ms,
                        ts=step_t0,
                        target=step.target,
                        extra={"deviation": deviation_desc} if deviation_desc else None,
                    )

                    # Update evolution stats (non-blocking, best-effort)
                    if self._evolution:
                        try:
                            self._evolution.record_step(resolved.routine_id, record)
                        except Exception:
                            pass

                    event_bus.emit("step.complete", {
                        "run_id":     run_id,
                        "index":      i,
                        "status":     step_status,
                        "duration_ms": dur_ms,
                        "deviation":  deviation_desc,
                    })
        except KeyboardInterrupt:
            # Ctrl-C → graceful abort: fall through to record/emit/persist below so
            # the run still appears on the frontend with what it managed to do.
            status = "aborted"
            error = "interrupted by user (Ctrl-C)"
            event_bus.emit("execution.halted", {
                "run_id": run_id, "step_index": steps_done, "reason": "keyboard_interrupt",
            })

            chain_in = routine.description
            if variables:
                chain_in += "\n" + str(variables)
            chain_out = f"status={status}, steps={steps_done}/{len(routine.steps)}"
            if error:
                chain_out += f", error={error}"
            apply_chain_span(span, input_text=chain_in, output_text=chain_out)

        ended_at = time.time()
        result = ExecutionResult(
            routine_id=resolved.routine_id,
            status=status,
            steps_completed=steps_done,
            error=error,
            duration_ms=int((ended_at - started_at) * 1000),
            variables=variables,
            started_at=started_at,
            ended_at=ended_at,
            run_id=run_id,
        )
        event_bus.emit("execution.complete", {
            "run_id":          run_id,
            "status":          status,
            "steps_completed": steps_done,
            "duration_ms":     result.duration_ms,
        })

        # ── Hand the trace to the async coalescer (COLD PATH) ───────────────────
        # Crystallization (LLM milestone segmentation, graph merge, edge reconciliation)
        # runs OFF this thread so it never slows execution. The run is already finished
        # here, so we just journal the trace (cheap, durable) + enqueue, then return.
        deviations = [
            {"step_index": r.index, "reason": r.deviation}
            for r in self.last_step_records if r.deviation
        ]
        submit_trace(RunTrace(
            run_id=run_id,
            routine_id=gkey,
            variables=variables,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            executed=executed,
            interventions=list(self._interventions),
            deviations=deviations,
            intent_text=intent_text,
        ))
        self._active_graph = None
        self._step_ms = {}
        return result

    # ── workflow dispatch (phase 4/5): traverse a saved Workflow ─────────────
    def execute_workflow(
        self,
        workflow,
        goal: str = "",
        params: Optional[dict] = None,
        profile: Optional[dict] = None,
    ) -> ExecutionResult:
        """Execute a dispatched Workflow by running it **through the pre-existing
        agentic loop** (design §0.1) — NOT a separate per-milestone executor. The
        workflow supplies background intent (current milestone + next + conditionals);
        Agent S plans a batch of UI actions and self-routes in the SAME reply
        (single-message advance, §0.2). The click path stays sacred — actuation goes
        through the same restricted exec helper. Tracing/teaching are side-channel."""
        from engine import workflow_control

        self._halt_flag.clear()
        self._agent_s.reset()
        run_id = str(uuid.uuid4())[:8]
        started_at = time.time()

        # Parent span so Arize Phoenix traces THROUGH the workflow: each milestone's
        # workflow.node span nests under this workflow.execute span.
        with self._telemetry.span("workflow.execute") as _wspan:
            _wspan.set_attribute("workflow.id", workflow.id)
            _wspan.set_attribute("workflow.name", workflow.name or "")
            _wspan.set_attribute("workflow.goal", goal)
            wf_run = self._run_workflow_agentic(workflow, goal=goal, params=params, profile=profile)
            _wspan.set_attribute("workflow.status", wf_run.status)
            _wspan.set_attribute("workflow.steps", len(wf_run.path))

        # Teaching loop — bake any `remember`-flagged human steers into the
        # workflow so the branch/procedure is automatic next run, then persist the
        # bumped version. one_off steers are journal-only (never baked).
        try:
            applied = workflow_control.bake(workflow, wf_run.interventions, run_id)
            if applied:
                decision = workflow_control.await_finalize(workflow, applied)
                outcome = workflow_control.persist_baked(workflow, applied, decision)
                event_bus.emit("workflow.baked", {
                    "workflow_id": workflow.id, "version": workflow.version,
                    "ops": applied, **outcome,
                })
        except Exception as exc:  # noqa: BLE001
            print(f"[execute_workflow] bake failed (non-fatal): {exc}")

        ended_at = time.time()
        status = {"completed": "completed", "blocked": "aborted"}.get(wf_run.status, "aborted")
        result = ExecutionResult(
            routine_id=workflow.id,
            status=status,
            steps_completed=len(wf_run.path),
            error=wf_run.blocked_on if status != "completed" else None,
            duration_ms=int((ended_at - started_at) * 1000),
            variables=params or {},
            started_at=started_at,
            ended_at=ended_at,
            run_id=run_id,
        )
        return result

    def _run_workflow_agentic(self, workflow, goal="", params=None, profile=None):
        """Walk a dispatched workflow's milestone graph **through the batched agentic
        loop** (design §0). For each milestone the agent is handed the milestone
        instruction + the next milestone + the outgoing edges/conditionals as
        background intent, then in ONE call plans a batch of UI actions AND returns
        the milestone to advance to ("SAME" to stay) — no extra routing round-trip.

        Reuses the workflow's data helpers (`options_for`, `WS`, `WorkerTurn`) and the
        Control Hub gate / `InterventionEvent` so observability + teaching are
        unchanged; emits the same `workflow.*` event stream the executor did so the
        live trace graph and tests keep working. Returns a `WorkflowRun`."""
        from engine.workflow_executor import (
            WorkflowRun, WorkflowStepRecord, WorkerTurn, options_for, WorkflowExecutor,
        )
        from engine import workflow_store as WS
        from engine import workflow_control

        started = time.time()
        kb: dict[str, str] = {**(params or {}), **(profile or {})}
        path: list[WorkflowStepRecord] = []
        interventions: list = []

        start_key = workflow.start_key or WS.derive_start_key(workflow.nodes, workflow.edges)
        cur = WS.node_by_key(workflow, start_key)
        if cur is None:
            return WorkflowRun(workflow.id, "aborted", path, kb, blocked_on="no start node",
                               started_at=started, ended_at=time.time())

        event_bus.emit("workflow.start", {
            "workflow_id": workflow.id, "name": workflow.name,
            "start": start_key, "goal": goal,
        })

        status = "completed"
        blocked_on: Optional[str] = None
        visits: dict[str, int] = {}
        done_keys: set[str] = set()   # milestones already emitted as done (dedup replay)
        MAX_NODES = 50
        MAX_TURNS_PER_NODE = 15
        global_turn = 0
        seq = 0                      # monotonic step_no across milestones (incl. batched)
        self._agent_s.reset_autonomous()

        def _emit_passed(node) -> None:
            """A milestone the agent completed *within another milestone's batch*
            (single-message advance jumped over it). Re-emit its node.enter +
            workflow.step(done) so the live graph + trace stay complete — pure
            side-channel events, zero LLM calls."""
            nonlocal seq
            opts = options_for(workflow, node)
            payload = [{"key": o.key, "label": o.label, "via": o.via, "when": o.when}
                       for o in opts]
            event_bus.emit("workflow.node.enter", {
                "workflow_id": workflow.id, "step_no": seq,
                "node_key": node.key, "label": node.label, "kind": node.kind,
                "instruction": node.instruction or node.label, "missing": [],
                "conditionals": [{"when": c.when, "do": c.do, "goto": c.goto}
                                 for c in node.conditionals],
                "options": payload,
            })
            path.append(WorkflowStepRecord(
                step_no=seq, node_key=node.key, label=node.label, status="done",
                did="(batched with the previous milestone)", branch=None,
                chose_next="", extracted={},
            ))
            event_bus.emit("workflow.step", {
                "workflow_id": workflow.id, "step_no": seq,
                "node_key": node.key, "label": node.label, "kind": node.kind,
                "status": "done", "did": "(batched with the previous milestone)",
                "branch": None, "next": "", "extracted": [], "options": payload,
            })
            visits[node.key] = visits.get(node.key, 0) + 1
            done_keys.add(node.key)
            seq += 1

        def _replay_batched(skip: str = "") -> None:
            """Emit done events for the extra milestones the agent finished in this
            batched turn (it reported them in `completed`), in order — except `skip`
            (the node we're about to make the cursor) and any already recorded."""
            for pk in result_completed:
                if pk == cur.key or pk == skip or pk in done_keys:
                    continue
                node = WS.node_by_key(workflow, pk)
                if node is not None:
                    _emit_passed(node)

        for _guard in range(MAX_NODES):
            if self._halt_flag.is_set():
                status, blocked_on = "aborted", "halt_requested"
                break

            step_no = seq
            visits[cur.key] = visits.get(cur.key, 0) + 1
            resolved = {k: kb[k] for k in cur.requires if k in kb}
            missing = [k for k in cur.requires if k not in kb]
            options = options_for(workflow, cur)
            opt_payload = [{"key": o.key, "label": o.label, "via": o.via, "when": o.when}
                           for o in options]
            turn = WorkerTurn(goal=goal, step_no=step_no, node=cur,
                              resolved=resolved, missing=missing,
                              options=options, profile=dict(kb))

            # Announce the milestone + its forward preview BEFORE acting so the
            # Control Hub can highlight it and the human can pause / steer here.
            event_bus.emit("workflow.node.enter", {
                "workflow_id": workflow.id, "step_no": step_no,
                "node_key": cur.key, "label": cur.label, "kind": cur.kind,
                "instruction": turn.instruction, "missing": missing,
                "conditionals": [{"when": c.when, "do": c.do, "goto": c.goto}
                                 for c in cur.conditionals],
                "options": opt_payload,
            })

            # ── human gate: steer / pause / teach from the Control Hub ─────────
            iv = workflow_control.review(turn)
            forced, iv_event = self._wf_apply_intervention(iv, turn, options, cur, step_no)
            if iv_event is not None:
                interventions.append(iv_event)
                event_bus.emit("workflow.intervention", {
                    "workflow_id": workflow.id, "step_no": step_no,
                    "node_key": cur.key, "decision": iv_event.decision,
                    "instruction": iv_event.instruction, "scenario": iv_event.scenario,
                    "flag": iv_event.flag,
                })

            result_branch: Optional[str] = None
            extracted: dict[str, str] = {}
            result_completed: list[str] = []   # extra milestones the agent batched this turn

            if forced is not None:
                # Human fully steered this milestone (halt or forced branch) — no
                # agent turn needed; apply the chosen action/route directly.
                result_did = forced["did"]
                result_status = forced["status"]
                result_next = forced["next"]
                result_branch = forced["branch"]
                extracted = dict(forced["extracted"])
                if extracted:
                    kb.update(extracted)
                if result_status == "blocked" and not blocked_on:
                    blocked_on = "human halt"
            else:
                # ── batched agentic turns until the agent advances the milestone ──
                # The agent gets the WHOLE remaining milestone chain as background
                # intent and batches across as many as it can per screenshot, so a
                # workflow run costs no more LLM calls than the same goal with no
                # workflow (it then names the furthest milestone it reached in `next`).
                instruction = turn.instruction
                forward = self._wf_forward_chain(workflow, cur, options_for, WS)
                if forward:
                    # honour a human instruction override (Control Hub) — keep the
                    # plan's CURRENT milestone text in sync with what the planner sees.
                    forward[0]["instruction"] = instruction
                did_parts: list[str] = []
                result_status, result_next = "done", "SAME"
                node_turns = 0
                with self._telemetry.span("workflow.node") as nspan:
                    nspan.set_attribute("workflow.node.key", cur.key)
                    nspan.set_attribute("workflow.node.label", cur.label)
                    nspan.set_attribute("workflow.node.kind", cur.kind)
                    nspan.set_attribute("workflow.step_no", step_no)
                    while True:
                        if self._halt_flag.is_set():
                            result_status, result_next = "blocked", "END"
                            blocked_on = "halt_requested"
                            break
                        if global_turn >= AUTONOMOUS_MAX_STEPS:
                            result_status, result_next = "blocked", "END"
                            blocked_on = "turn budget exhausted"
                            break
                        if node_turns >= MAX_TURNS_PER_NODE:
                            # Couldn't finish grounding this milestone — advance along
                            # the default option rather than spin forever.
                            result_next = options[0].key if options else "END"
                            break

                        plan = self._agent_s.plan_workflow_chain(
                            goal=goal, step_no=step_no, instruction=instruction,
                            forward=forward,
                            options=[{"key": o.key, "label": o.label, "via": o.via,
                                      "when": o.when, "do": o.do} for o in options],
                            resolved=resolved, missing=missing,
                        )
                        global_turn += 1
                        node_turns += 1
                        if plan.branch:
                            result_branch = plan.branch
                        if plan.completed:
                            result_completed.extend(plan.completed)
                        if plan.extracted:
                            extracted.update(plan.extracted)
                            kb.update(plan.extracted)

                        if plan.outcome == "action" and plan.code:
                            try:
                                self._exec_agent_code(plan.code)
                            except Exception as exc:  # noqa: BLE001
                                result_status, result_next = "blocked", "END"
                                blocked_on = str(exc)
                                print(f"[execute_workflow] node {cur.key} action failed: {exc}")
                                break
                            if self._agent_s.last_reasoning:
                                did_parts.append(self._agent_s.last_reasoning)
                            nxt = (plan.next or "SAME").strip()
                            if nxt.upper() == "SAME":
                                continue          # more turns on this milestone
                            result_next = nxt
                            break
                        if plan.outcome == "done":
                            result_next = plan.next or "END"
                            break
                        if plan.outcome == "fail":
                            result_status, result_next = "blocked", "END"
                            blocked_on = plan.raw or "agent reported failure"
                            break
                        if plan.outcome == "wait":
                            nxt = (plan.next or "SAME").strip()
                            if nxt.upper() != "SAME":
                                result_next = nxt
                                break
                            continue
                        # unavailable — no API key / planner error → cannot proceed
                        result_status, result_next = "blocked", "END"
                        blocked_on = "agent unavailable"
                        break
                    nspan.set_attribute("workflow.node.status", result_status)
                    if result_branch:
                        nspan.set_attribute("workflow.node.branch", result_branch)
                result_did = " | ".join(did_parts) or "acted"

            rec_status = "blocked" if result_status == "blocked" else "done"
            rec = WorkflowStepRecord(
                step_no=step_no, node_key=cur.key, label=cur.label,
                status=rec_status, did=result_did, branch=result_branch,
                chose_next=result_next, extracted=dict(extracted),
            )
            path.append(rec)
            event_bus.emit("workflow.step", {
                "workflow_id": workflow.id, "step_no": step_no,
                "node_key": cur.key, "label": cur.label, "kind": cur.kind,
                "status": rec_status, "did": result_did,
                "branch": result_branch, "next": result_next,
                "extracted": list(extracted.keys()),
                "options": opt_payload,
            })
            done_keys.add(cur.key)
            seq += 1

            if rec_status == "blocked":
                status = "blocked"
                break

            # ── advance: with single-message advance the agent both acts AND names
            # where to go (`next`), reporting any extra milestones it finished in the
            # same batch (`completed`). Replay those as done so the graph stays whole,
            # then move the cursor — zero extra LLM calls.
            nxt = (result_next or "END").strip()
            if nxt.upper() == "END" or not options:
                _replay_batched()
                break
            chosen = WorkflowExecutor._resolve_next(nxt, options)
            if chosen is None:
                # Not a previewed option, but allow routing to any REAL milestone
                # (a taught/forced branch may target a node that isn't yet an edge).
                # A truly unknown ref falls back to the common path so the run never
                # strands on a fuzzy answer.
                chosen = nxt if WS.node_by_key(workflow, nxt) is not None else options[0].key
            _replay_batched(skip=chosen)
            if visits.get(chosen, 0) >= 3:
                status, blocked_on = "aborted", f"loop at {chosen}"
                break
            cur = WS.node_by_key(workflow, chosen)
            if cur is None:
                break
        else:
            status, blocked_on = "aborted", "max steps exceeded"

        event_bus.emit("workflow.done", {
            "workflow_id": workflow.id, "status": status,
            "steps": len(path), "blocked_on": blocked_on,
            "taught": sum(1 for iv in interventions if iv.flag == "save_as_rule"),
        })
        return WorkflowRun(workflow.id, status, path, kb, blocked_on,
                           interventions=interventions,
                           started_at=started, ended_at=time.time())

    def _wf_apply_intervention(self, iv, turn, options, node, step_no):
        """Turn a human Intervention from the Control Hub into
        (forced_result|None, InterventionEvent|None) for the agentic workflow loop.

        Mirrors the executor's gate semantics: a `halt` or a forced-branch fully
        steers the milestone (returns a result dict, no agent turn); a pure
        instruction injection just layers the human's text onto the milestone and
        lets the agent act (returns None). The InterventionEvent carries the teaching
        flag so a `remember` directive is baked into the workflow after the run."""
        if iv is None:
            return None, None
        from shepherd_types import InterventionEvent
        from engine.workflow_executor import WorkflowExecutor

        flag = "save_as_rule" if iv.remember else "one_off"
        scenario = iv.scenario or (turn.missing and f"missing {turn.missing}") or node.label
        ev = InterventionEvent(
            step_index=step_no, trigger="human", decision=iv.decision,
            instruction=iv.instruction, flag=flag, node_key=node.key,
            scenario=scenario, ts=time.time(),
        )

        if iv.decision == "halt":
            return ({"did": "[human] halted", "status": "blocked", "next": "END",
                     "branch": None, "extracted": {}}, ev)

        if not iv.next:
            # Pure instruction injection: augment the milestone, agent still acts.
            if iv.instruction:
                turn.override_instruction = (
                    f"{turn.node.instruction or turn.node.label}\n"
                    f"[human] {iv.instruction}"
                )
            return None, ev

        # Forced branch — the human triggers a specific next milestone in one message.
        chosen = WorkflowExecutor._resolve_next(iv.next, options) or iv.next
        ev.goto = chosen
        return ({"did": f"[human] {iv.instruction or 'steered to ' + chosen}",
                 "status": "done", "next": chosen, "branch": scenario,
                 "extracted": dict(iv.extracted)}, ev)

    @staticmethod
    def _wf_node_dict(node) -> dict:
        """A milestone rendered for the planner's background-intent plan."""
        return {
            "key": node.key, "label": node.label,
            "instruction": node.instruction or node.label,
            "conditionals": [{"when": c.when, "do": c.do, "goto": c.goto}
                             for c in node.conditionals],
        }

    @staticmethod
    def _wf_default_path(workflow, node, options_for, WS, depth: int = 12) -> list[str]:
        """The linear chain of milestone keys reachable from `node` by following the
        single unconditional (default) edge at each step — the fast/common path the
        agent batches across in one turn. Stops AT (and includes) the first decision
        node (one carrying taught conditionals) so a branch gets evaluated with that
        node as the cursor; also stops at a fork (>1 plain edge), a cycle, or `depth`.
        Excludes `node` itself."""
        keys: list[str] = []
        seen = {node.key}
        cur = node
        if cur.conditionals:        # cur is itself a decision point — don't batch past it
            return keys
        while len(keys) < depth:
            plain = [o for o in options_for(workflow, cur) if o.via == "edge"]
            if len(plain) != 1:
                break
            nxt = WS.node_by_key(workflow, plain[0].key)
            if nxt is None or nxt.key in seen:
                break
            keys.append(nxt.key)
            seen.add(nxt.key)
            if nxt.conditionals:    # include the decision node, then stop
                break
            cur = nxt
        return keys

    def _wf_forward_chain(self, workflow, node, options_for, WS, depth: int = 12) -> list[dict]:
        """`node` + its default-path successors (up to the next decision point) as the
        remaining milestone plan the agent batches across (design §0.2). Conditional
        successors are NOT walked into — they ride along as guards on each node so the
        agent can divert if a `when` fires, but the linear plan stays the fast path."""
        chain = [self._wf_node_dict(node)]
        for k in self._wf_default_path(workflow, node, options_for, WS, depth):
            nxt = WS.node_by_key(workflow, k)
            if nxt is not None:
                chain.append(self._wf_node_dict(nxt))
        return chain

    # ── step dispatcher — SYNCHRONOUS, no async, no network ─────────────────

    def _build_instruction(self, step: RoutineStep, index: int, routine) -> str:
        """Compose the per-step instruction string passed to Agent S."""
        if self._pending_override:
            inst = self._pending_override
            self._pending_override = ""
            return inst
        if routine.step_instructions and index in routine.step_instructions:
            return routine.step_instructions[index]
        base = step.description or step.action
        goal = self._run_variables.get("GOAL", "")
        if goal and routine.routine_id == AUTONOMOUS_ROUTINE_ID:
            return f"Overall goal: {goal}\n\nStep {index + 1}: {base}"
        return base

    def _resolved_step_text(self, step: RoutineStep) -> str:
        """Substitute {VARIABLES} in step.text from the current run."""
        if not step.text:
            return ""
        text = step.text
        for k, v in self._run_variables.items():
            text = text.replace(f"{{{k}}}", v)
        return text.strip()

    def _build_demo_context(self, routine) -> str:
        """Format the recorded demonstration as a readable context string for Agent S."""
        if not routine.demonstration:
            return ""
        lines = []
        for s in routine.demonstration:
            instr = getattr(s, "instruction", None) or getattr(s, "action", "")
            idx   = getattr(s, "index", "?")
            lines.append(f"  step {idx}: {instr}")
        return "Recorded demonstration:\n" + "\n".join(lines)

    def _live_execute(
        self, step: RoutineStep, index: int, routine
    ) -> tuple[Optional[str], str]:
        """
        Ask Agent S to plan this step. Returns (executable Python/pyautogui code, instruction).
        Code is None when falling back to _dispatch(defined step).

        batch_fill steps use plan_batch_action — one API call for all fields.
        All other steps use plan_action — one API call per step.
        """
        instruction = self._build_instruction(step, index, routine)
        if self._mode != "LIVE" or not self._agent_s.available:
            return None, instruction
        demo_ctx = self._build_demo_context(routine)
        ms = self._step_ms.get(index)
        if ms and ms["times_seen"] > 0:
            demo_ctx += (
                f"\n[task-graph reference] This click is part of milestone "
                f"'{ms['label']}', performed {ms['times_seen']}x before.")

        # Teaching loop — hand Agent S the taught procedure/conditionals saved on
        # this milestone so it can apply them in-context (no separate predicate eval).
        taught = self._taught_resolution(index)
        if taught:
            demo_ctx += f"\n[taught] For this milestone: {taught}"

        # batch_fill, wait, hotkey, open_app all use _dispatch directly —
        # batch_fill uses JS injection (more reliable than Agent S Tab code),
        # the rest don't benefit from vision planning. type steps with resolved
        # text are deterministic — no vision needed.
        if step.action in ("batch_fill", "wait", "hotkey", "open_app"):
            return None, instruction
        if step.action == "type" and self._resolved_step_text(step):
            return None, instruction

        with self._telemetry.span("agent_s.plan", oi_kind="LLM") as plan_span:
            code = self._agent_s.plan_action(
                instruction, index, demo_ctx,
                action=step.action,
                type_text=self._resolved_step_text(step) or None,
            )
            apps, tools = summarize_agent_code(code)
            apply_llm_plan_span(
                plan_span,
                instruction=instruction,
                response=code or "(no actionable code — using defined step)",
                outcome="action" if code else "fallback",
                model=AGENT_S_MODEL,
                provider=AGENT_S_ENGINE_TYPE,
                code=code,
                apps=apps or None,
                tools=tools or None,
            )
        return code, instruction

    def _exec_agent_code(self, code: str) -> None:
        """
        Execute Agent S-generated Python/pyautogui code in a restricted namespace.
        Agent S returns strings like: "pyautogui.click(760, 300)".

        `activate_app` is exposed so the agent can deterministically bring a target
        app to the foreground (instead of gambling that a Spotlight launch took
        focus); without guaranteed focus, keystrokes land in the wrong window.
        """
        exec(  # noqa: S102
            code,
            {
                "__builtins__": __builtins__,
                "pyautogui": pyautogui,
                "time": time,
                "activate_app": activate_app,
                "type_text": type_text,
            },
        )

    def _dispatch(self, step: RoutineStep, variables: dict) -> None:
        def sub(t: Optional[str]) -> Optional[str]:
            if t is None:
                return None
            for k, v in variables.items():
                t = t.replace(f"{{{k}}}", v)
            return t

        a = step.action

        if a == "move":
            x, y = get_coord(step.target, self._coords)
            pyautogui.moveTo(x, y, duration=0.4)

        elif a == "click":
            x, y = get_coord(step.target, self._coords)
            pyautogui.click(x, y)

        elif a == "double_click":
            x, y = get_coord(step.target, self._coords)
            pyautogui.doubleClick(x, y)

        elif a == "type":
            text_val = sub(step.text) or ""
            press_return = text_val.endswith("\n")
            text_body = text_val.rstrip("\n")
            if text_body:
                # Replace any leftover chars (e.g. "/" from YouTube search focus shortcut)
                do_hotkey(["cmd", "a"])
                time.sleep(0.05)
                enter_text(text_body)
            if press_return:
                pyautogui.press("return")

        elif a == "hotkey":
            do_hotkey(step.keys or [])

        elif a == "open_app":
            normalize_open_app_step(step)
            app_name = sub(step.target) or ""
            url = sub(step.text) or ""
            # Containment check — app + URL against policy allowlists
            blocked = policy_engine.check_containment("open_app", app_name)
            if not blocked and url:
                blocked = policy_engine.check_containment("open_app", url)
            if blocked:
                raise ValueError(f"[containment] {blocked['reason']}")
            cmd = ["open", "-a", app_name]
            if url:
                cmd.append(url)   # opens app directly at the URL, avoids Cmd+L issues
            subprocess.Popen(cmd)
            time.sleep(_APP_SETTLE)
            subprocess.run(
                ["osascript", "-e", f'tell application "{app_name}" to activate'],
                check=False,
            )

        elif a == "wait":
            time.sleep(step.seconds or 1.0)

        elif a == "batch_fill":
            # Build the fill plan. LIVE mode: Claude reads the form screenshot and
            # decides the mapping (genuine vision planning). LOCKED / fallback: the
            # routine's hardcoded fields. Either way, actuation is reliable JS injection.
            plan = None
            if self._mode == "LIVE" and self._agent_s.available:
                try:
                    import io as _io
                    import subprocess as _sp0
                    # Bring Chrome to front so Claude's screenshot shows the form, not another window.
                    _sp0.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
                    time.sleep(0.4)
                    shot = pyautogui.screenshot()
                    _b = _io.BytesIO()
                    shot.save(_b, format="PNG")
                    resolved_fields = [
                        type(bf)(tabs=bf.tabs, text=sub(bf.text), description=bf.description,
                                 html_name=getattr(bf, "html_name", None))
                        for bf in (step.fields or [])
                    ]
                    plan = self._agent_s.plan_batch_fill_mapping(resolved_fields, _b.getvalue())
                except Exception as e:
                    print(f"[engine] batch_fill vision planning failed (using hardcoded fields): {e}")

            if plan is None:
                # Hardcoded fields from the routine
                plan = [
                    {"html_name": bf.html_name, "value": sub(bf.text)}
                    for bf in (step.fields or [])
                    if getattr(bf, "html_name", None) and bf.text
                ]

            self._js_fill(plan)

        elif a == "browser":
            # Invoked only at routine boundaries, never mid-sequence.
            # Substitute {VARS} in the URL FIRST (e.g. github.com/{GITHUB_USER})
            # so containment checks and Browserbase see the resolved URL, not the
            # literal placeholder.
            bstep = dict(step.browser_step or {})
            if bstep.get("url"):
                bstep["url"] = sub(bstep["url"])
            target_url = bstep.get("url", "")
            if target_url:
                blocked = policy_engine.check_containment("browser", target_url)
                if blocked:
                    raise ValueError(f"[containment] {blocked['reason']}")
            from services.browserbase_routine import run_browser_step
            store_as = bstep.get("store_as")

            # Research digression: run it as a real Agentspan agent (durable
            # workflow on the Agentspan server) that reasons + calls a fetch tool
            # (Browserbase under the hood). Falls through to a direct read if the
            # agent engine is unavailable, so the demo never depends on it.
            value = None
            via = "browserbase"
            if bstep.get("agent_research") and FEATURES.get("agentspan"):
                try:
                    from services import agentspan_research
                    summary = agentspan_research.research(target_url)
                    if summary:
                        value = summary
                        via = "agentspan"
                        event_bus.emit("agent.agentspan", {
                            "agent":        "shepherd-researcher",
                            "execution_id": agentspan_research.status().get("last_execution_id"),
                            "url":          target_url,
                            "summary":      summary[:160],
                            "store_as":     store_as,
                        })
                except Exception as e:
                    print(f"[agentspan] research dispatch failed (non-fatal): {e}")

            if value is None:
                result = run_browser_step(bstep)
                value = result.get("value")
                via = result.get("status", "ok")

            # A "read" feeds the next step: store its value into a variable so a
            # later {VAR} fill uses what the agent just read. If empty, fall back to
            # the step's fallback_value (then ""), so a fill never ships a literal
            # {PLACEHOLDER}.
            if store_as:
                variables[store_as] = value or bstep.get("fallback_value", "") or ""
            event_bus.emit("step.browser", {
                "url":      target_url,
                "action":   bstep.get("action", "navigate"),
                "status":   via,
                "value":    (value or "")[:160],
                "store_as": store_as,
            })

        else:
            raise ValueError(f"Unknown action: '{a}'")

    def _js_fill(self, plan: list) -> None:
        """
        Actuate a fill plan ([{html_name, value}]) via Chrome JS injection — no
        keyboard focus required, immune to focus-stealing. Auto-enables Chrome's
        "Allow JavaScript from Apple Events", and falls back to click + Tab if JS
        stays blocked.
        """
        import os as _os
        import subprocess as _sp
        import tempfile as _tmp

        # Best-effort enable "Allow JavaScript from Apple Events" (checks state first
        # so it never toggles OFF when already enabled).
        _enable_js = (
            'tell application "Google Chrome" to activate\n'
            'delay 0.3\n'
            'tell application "System Events" to tell process "Google Chrome"\n'
            '  set mi to menu item "Allow JavaScript from Apple Events" of menu "Developer" '
            'of menu item "Developer" of menu "View" of menu bar 1\n'
            '  if value of attribute "AXMenuItemMarkChar" of mi is missing value then click mi\n'
            'end tell\n'
        )
        _sp.run(["osascript", "-e", _enable_js], check=False, capture_output=True, text=True)

        js_stmts = []
        for p in plan:
            name, value = p.get("html_name"), p.get("value")
            if not name or value is None:
                continue
            val = str(value).replace("\\", "\\\\").replace('"', '\\"')
            js_stmts.append(
                f"(function(){{var e=document.querySelector('[name={name}]');"
                f"if(e){{e.value=\\\"{val}\\\";e.dispatchEvent(new Event('input',{{bubbles:true}}))}}}})();"
            )
        js_block = "".join(js_stmts)
        ascript = (
            'tell application "Google Chrome"\n'
            '  activate\n'
            '  tell active tab of front window\n'
            f'    execute javascript "{js_block}"\n'
            '  end tell\n'
            'end tell\n'
        )
        with _tmp.NamedTemporaryFile(mode="w", suffix=".applescript", delete=False) as f:
            f.write(ascript)
            apath = f.name
        result = _sp.run(["osascript", apath], check=False, capture_output=True, text=True)
        _os.unlink(apath)

        if result.returncode != 0:
            # JS blocked — click Chrome to focus, Cmd+L → Tab into the form, paste each value.
            _sp.run(["osascript", "-e", 'tell application "Google Chrome" to activate'], check=False)
            time.sleep(0.3)
            pyautogui.click(640, 50)      # Chrome tab bar — focus without touching a form field
            time.sleep(0.2)
            pyautogui.hotkey("cmd", "l")
            time.sleep(0.15)
            pyautogui.hotkey("tab")       # address bar → first form field
            time.sleep(0.15)
            for p in plan:
                value = p.get("value")
                if value is not None:
                    _sp.run(["pbcopy"], input=str(value).encode(), check=False)
                    pyautogui.hotkey("cmd", "v")
                    time.sleep(0.12)
                pyautogui.hotkey("tab")
                time.sleep(0.08)

    def _detect_code_deviation(
        self, defined: RoutineStep, code: str
    ) -> Optional[str]:
        """
        Compare Agent S-generated code against the defined step's expected action type.
        Returns a description when the action type diverges, else None.
        """
        # Signals that indicate each action type is present in the code
        _SIGNALS: dict[str, list[str]] = {
            "click":        ["click("],
            "double_click": ["doubleClick("],
            "type":         ["typewrite(", "write("],
            "hotkey":       ["hotkey(", "press("],
            "move":         ["moveTo(", "moveRel("],
            "wait":         ["sleep("],
        }
        expected_sigs = _SIGNALS.get(defined.action, [])
        if not expected_sigs:
            return None
        if any(sig in code for sig in expected_sigs):
            return None  # matches expected action type — no deviation
        # Find what action Agent S actually used
        for act, sigs in _SIGNALS.items():
            if any(sig in code for sig in sigs):
                return f"action {defined.action}→{act}"
        return f"action {defined.action}→unknown"

    def _check_monitor(
        self,
        step: RoutineStep,
        index: int,
        run_id: str,
        routine_id: str = "",
    ) -> str:
        """
        Calls monitor at step boundary. Returns 'ok' or 'halt'.
        On FLAG or HALT: emits monitor.alert with suggestion chips,
        then blocks until the human approves/halts (or 30 s timeout).
        Never called inside a click sequence.
        """
        # Rule check — can fail gracefully without blocking the demo
        try:
            from services.monitor_agent import check_step
            result  = check_step(step, {"step_index": index})
            verdict = result.get("verdict", "ok")
            reason  = result.get("reason", "")
        except Exception as exc:
            print(f"[monitor] check_step failed (non-fatal): {exc}")
            return "ok"

        if verdict not in ("flag", "halt"):
            return verdict

        # Teaching loop — inject saved clauses instead of re-blocking. If a prior
        # human already taught how to handle this milestone (save_as_rule → a node
        # procedure/conditional), auto-resolve a FLAG with that taught action
        # rather than stopping the human again. HALTs are hard stops, never auto.
        if verdict == "flag":
            taught = self._taught_resolution(index)
            if taught:
                self._pending_override = taught
                self._interventions.append(InterventionEvent(
                    step_index=index, trigger=step.monitor_trigger,
                    decision="auto", instruction=taught, flag="one_off",
                    node_key=self._step_ms.get(index, {}).get("key", ""),
                    scenario=reason, ts=time.time(),
                ))
                event_bus.emit("monitor.auto_resolved", {
                    "run_id": run_id, "step_index": index,
                    "trigger": step.monitor_trigger, "instruction": taught,
                })
                return "ok"

        # ── Independent verifier (Haiku second opinion) ───────────────────────
        # Only for "flag" — "halt" from rules is already certain.
        # If verifier upgrades to "halt", skip the approval gate entirely.
        if verdict == "flag":
            try:
                import io as _io
                import pyautogui as _pag
                from services.verifier import verify as _verify
                _shot = _pag.screenshot()
                _buf = _io.BytesIO()
                _shot.save(_buf, format="PNG")
                vr = _verify(reason=reason, screenshot_png=_buf.getvalue())
                print(f"[verifier] verdict={vr['verdict']} conf={vr['confidence']:.2f} — {vr['explanation']}")
                event_bus.emit("verifier.result", {
                    "run_id": run_id, "step_index": index,
                    "verdict": vr["verdict"], "confidence": vr["confidence"],
                    "explanation": vr["explanation"], "model": vr["model"],
                    # Per-agent breakdown when the second opinion came from the
                    # Band oversight council (absent for the in-process verifier).
                    "votes": vr.get("votes") or [],
                })
                if vr["verdict"] == "halt" and vr["confidence"] >= 0.7:
                    # Both layers agree — halt immediately, no approval gate needed
                    return "halt"
                if vr["verdict"] == "ok" and vr["confidence"] >= 0.85:
                    # Verifier is confident it's a false alarm — let it through
                    return "ok"
            except Exception as ve:
                print(f"[verifier] failed (non-fatal, keeping flag): {ve}")

        # Approval gate — separated so exceptions here don't silently return "ok"
        from engine.approvals import (
            suggestions_for, request_approval, get_override_instruction, get_override_flag,
        )

        sugg = suggestions_for(step.monitor_trigger)

        history_note = ""
        if self._evolution and routine_id:
            try:
                s = self._evolution.get_stats(routine_id, index)
                if s.execution_count >= 2:
                    history_note = (
                        f"Step {index} history: "
                        f"{s.halt_count}× halted, "
                        f"{s.success_count}× approved"
                    )
            except Exception:
                pass

        event_bus.emit("monitor.alert", {
            "run_id":             run_id,
            "step_index":         index,
            "verdict":            verdict,
            "reason":             reason,
            "trigger":            step.monitor_trigger,
            "awaiting_approval":  True,
            "suggestions":        sugg,
            "history_note":       history_note,
        })

        # Voice oversight (additive): the agent speaks the flagged action and takes
        # a spoken approve/stop, racing alongside the on-screen gate. It never
        # overrides the gate — whichever channel answers first wins via set_decision.
        if FEATURES["deepgram"] and getattr(_cfg, "VOICE_OVERSIGHT", False):
            def _voice_gate(_reason=reason):
                try:
                    from services.deepgram_input import voice_gate as _vg
                    from engine.approvals import set_decision
                    d = _vg(_reason)
                    if d in ("approve", "halt"):
                        set_decision(d)
                except Exception as e:
                    print(f"[voice] gate non-fatal: {e}")
            threading.Thread(target=_voice_gate, daemon=True).start()

        default   = "approve" if verdict == "flag" else "halt"
        decision  = request_approval(index, reason, timeout=30.0, default=default)

        # Speak the outcome so a hands-free operator hears the result (best-effort).
        if FEATURES["deepgram"] and getattr(_cfg, "VOICE_OVERSIGHT", False):
            _spoken = "Halted. The action was not taken." if decision == "halt" else "Approved. Continuing."
            threading.Thread(
                target=lambda: __import__("services.deepgram_input", fromlist=["speak_and_play"]).speak_and_play(_spoken),
                daemon=True,
            ).start()

        event_bus.emit("monitor.decision", {
            "run_id": run_id, "step_index": index, "decision": decision,
        })

        if decision == "halt":
            self._interventions.append(InterventionEvent(
                step_index=index, trigger=step.monitor_trigger, decision="halt",
                node_key=self._step_ms.get(index, {}).get("key", ""),
                scenario=reason, ts=time.time(),
            ))
            return "halt"

        instruction, flag = "", "one_off"
        if decision == "override":
            instruction = get_override_instruction()
            flag = get_override_flag()
            self._pending_override = instruction

        # Record the human resolution so the coalescer can bake it (teaching loop).
        # flag=save_as_rule → conditional clause baked onto this milestone's node.
        self._interventions.append(InterventionEvent(
            step_index=index, trigger=step.monitor_trigger, decision=decision,
            instruction=instruction, flag=flag,
            node_key=self._step_ms.get(index, {}).get("key", ""),
            scenario=reason, ts=time.time(),
        ))

        if self._evolution and routine_id:
            try:
                self._evolution.record_approval(routine_id, index)
            except Exception:
                pass

        return "ok"

    def _node_for_step(self, index: int):
        """The active-graph milestone node this step belongs to, or None."""
        if not self._active_graph:
            return None
        key = self._step_ms.get(index, {}).get("key", "")
        if not key:
            return None
        for n in self._active_graph.nodes:
            if n.key == key:
                return n
        return None

    def _taught_resolution(self, index: int) -> str:
        """Taught text (procedure + conditional clauses) saved on this step's node,
        rendered for Agent S. Empty when nothing was taught here yet."""
        node = self._node_for_step(index)
        if node is None:
            return ""
        parts: list[str] = []
        if node.procedure:
            parts.append(node.procedure.strip())
        for c in node.conditionals:
            parts.append(f"if {c.when} → {c.do}")
        return " | ".join(p for p in parts if p)
