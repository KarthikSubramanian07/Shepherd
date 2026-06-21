"""
ShepherdExecutionEngine

LIVE mode       — Agent S plans actions against the recorded demonstration; pyautogui actuates.
LOCKED mode     — Deterministic verbatim replay of pre-mapped steps (offline demo floor).
AUTONOMOUS mode — Agent S receives the raw user goal and loops until DONE/FAIL/max steps.

The click path is synchronous and sacred.
Nothing async, networked, or ML-based runs inside a routine's step sequence.
"""
import subprocess
import threading
import time
import uuid
from typing import Optional

import pyautogui

import config as _cfg
from config import FEATURES, EXECUTION_MODE, AUTONOMOUS_MAX_STEPS
from shepherd_types import (
    AUTONOMOUS_ROUTINE_ID,
    ExecutionResult, ResolvedRoutine, RoutineStep, StepRecord, RunTrace, InterventionEvent,
)
from engine.coords import get as get_coord
from engine.routines import get_routine
from engine.agent_s_adapter import AgentSAdapter
from engine.task_graph import TaskGraphStore, summarize, milestone_key
from engine.coalescer import submit as submit_trace
from dashboard.events import event_bus
from telemetry import audit_log
from telemetry import request_log as rlog
from services import policy_engine

pyautogui.FAILSAFE = True   # slam mouse to top-left corner to abort
pyautogui.PAUSE    = 0.3    # deliberate, watchable motion — this is the wow factor

_APP_SETTLE = 2.0           # seconds to wait after open_app


class ShepherdExecutionEngine:
    def __init__(
        self,
        coords: dict,
        telemetry,
        mode: str = EXECUTION_MODE,
        agent_s=None,
        evolution=None,
    ) -> None:
        self._coords    = coords
        self._telemetry = telemetry
        self._mode      = mode
        self._agent_s   = agent_s if agent_s is not None else AgentSAdapter()
        self._evolution = evolution  # RoutineEvolution | None — injected by main.py
        self._graphs    = TaskGraphStore()
        self._active_graph = None   # task graph loaded as reference for the current run
        self._step_ms      = {}     # fine-step index → milestone reference for Agent S
        self.last_step_records: list[StepRecord] = []
        self._interventions: list[InterventionEvent] = []  # human teaching this run → coalescer
        self._halt_flag = threading.Event()
        self._pending_override: str = ""
        # Rate-limit tracking (resets each run)
        self._run_action_times: list[float] = []

    def request_halt(self) -> None:
        """Set by monitor_agent or spoken 'stop' command. Checked at each step boundary."""
        self._halt_flag.set()

    def effective_mode(self) -> str:
        if _cfg._runtime_mode:
            return _cfg._runtime_mode.upper()
        return self._mode.upper()

    def execute_autonomous(self, goal: str) -> ExecutionResult:
        """
        Free-form goal execution — Agent S plans each action from the full intent
        and current screenshot until DONE, FAIL, halt, or step budget exhausted.
        """
        self._halt_flag.clear()
        self.last_step_records = []
        self._agent_s.reset_autonomous()
        run_id = str(uuid.uuid4())[:8]
        started_at = time.time()
        max_steps = AUTONOMOUS_MAX_STEPS
        variables = {"GOAL": goal}

        # Per-goal task graph — each executed step appends a node; persisted at end.
        task_key = self._autonomous_task_key(goal)
        graph = self._graphs.load(task_key, variables)
        self._active_graph = graph
        was_known = self._graphs.is_known(graph)

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
        monitor_step = RoutineStep(action="agent_s", description=goal)

        try:
          with self._telemetry.span("routine.execute") as span:
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

                result = self._agent_s.predict_autonomous(goal, i)
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

                with self._telemetry.span("action.agent_s") as s:
                    s.set_attribute("action.type", "agent_s")
                    s.set_attribute("action.index", i)
                    s.set_attribute("action.agent_s", True)
                    try:
                        self._exec_agent_code(result.code)
                        steps_done += 1
                    except Exception as exc:
                        step_status = "failed"
                        step_error = str(exc)
                        error = step_error
                        status = "failed"
                        print(f"[engine] autonomous step {i} failed: {step_error}")
                        if FEATURES["sentry"]:
                            import sentry_sdk
                            sentry_sdk.capture_exception(exc)
                        s.set_attribute("error.message", step_error)
                        event_bus.emit("step.error", {
                            "run_id": run_id, "index": i, "error": step_error,
                        })
                        break

                dur_ms = int((time.time() - step_t0) * 1000)
                rlog.step_result(run_id, i, step_status, dur_ms, step_error or "")
                # Each executed step adds a node to the task graph (value=step index
                # keeps every step a distinct node rather than collapsing duplicates).
                label = self._autonomous_node_label(self._agent_s.last_reasoning, result.code)
                fine  = len((result.code or "").splitlines())
                self._graphs.record_milestone(
                    graph, "step", label, str(i), fine, step_status, run_id)
                # Persist immediately so the graph is viewable mid-run and survives
                # an interrupt before the run formally completes.
                self._graphs.flush(graph)
                event_bus.emit("task.graph.node", {
                    "run_id": run_id, "index": i, "label": label,
                    "kind": "step", "status": step_status,
                })
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

        # Persist the graph this run built (one node per executed step).
        self._graphs.save(graph, intent_text=goal, variables=variables, run_id=run_id)
        event_bus.emit("task.graph.saved", {
            "run_id":     run_id,
            "routine_id": task_key,
            "run_count":  graph.run_count,
            "node_count": len(graph.nodes),
            "milestones": [n.label for n in graph.nodes],
        })
        self._active_graph = None

        rlog.request_finished(run_id, status, steps_done, result.duration_ms,
                              [n.label for n in graph.nodes])
        return result

    @staticmethod
    def _autonomous_task_key(goal: str) -> str:
        slug = "".join(c if c.isalnum() else "_" for c in (goal or "").lower()).strip("_")
        return "AUTONOMOUS::" + (slug[:48] or "goal")

    @staticmethod
    def _autonomous_node_label(reasoning: str, code: Optional[str]) -> str:
        """A concise human-readable label for a step's graph node: the first line of
        the agent's reasoning, else the first action it ran."""
        r = " ".join((reasoning or "").split())
        if r:
            return (r[:77] + "…") if len(r) > 78 else r
        first = (code or "").strip().splitlines()[0] if (code or "").strip() else "action"
        return first[:78]

    def execute(self, resolved: ResolvedRoutine) -> ExecutionResult:
        self._halt_flag.clear()
        self.last_step_records = []
        self._interventions = []
        self._run_action_times = []
        # Fresh Agent S trajectory per run — its reflection/trajectory state is
        # per-task and must not leak across runs.
        self._agent_s.reset()
        run_id     = str(uuid.uuid4())[:8]
        started_at = time.time()

        # Respect runtime mode switch from dashboard POST /api/mode
        if _cfg._runtime_mode:
            self._mode = _cfg._runtime_mode.upper()

        routine   = get_routine(resolved.routine_id)
        variables = resolved.variables

        # ── Load this task's persistent graph as a reference ────────────────────
        # The graph is coarse (milestones, not clicks). On a repeat run it tells us
        # (and Agent S) what's already been done; new milestones get appended below.
        graph = self._graphs.load(resolved.routine_id, variables)
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
            "routine_id": resolved.routine_id,
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
          with self._telemetry.span("routine.execute") as span:
            span.set_attribute("routine.id",   resolved.routine_id)
            span.set_attribute("routine.mode", self._mode)
            for k, v in variables.items():
                span.set_attribute(f"routine.variable.{k}", v)

            limits = policy_engine.get_limits()

            for i, step in enumerate(routine.steps):
                # ── halt check (boundary, never mid-click) ──────────────────
                if self._halt_flag.is_set():
                    status = "aborted"
                    event_bus.emit("execution.halted", {
                        "run_id": run_id, "step_index": i, "reason": "halt_requested"
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
                agent_code = self._live_execute(defined_step, i, routine)

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
                with self._telemetry.span(span_name) as s:
                    s.set_attribute("action.type",    "agent_s" if agent_code else step.action)
                    s.set_attribute("action.index",   i)
                    s.set_attribute("action.agent_s", bool(agent_code))
                    if step.target:
                        s.set_attribute("action.target", step.target)
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
                        step_status = "failed"
                        step_error  = str(exc)
                        error       = step_error
                        status      = "failed"
                        print(f"[engine] step {i} failed: {step_error}")
                        if FEATURES["sentry"]:
                            import sentry_sdk
                            sentry_sdk.capture_exception(exc)
                        s.set_attribute("error.message", step_error)
                        event_bus.emit("step.error", {
                            "run_id": run_id, "index": i, "error": step_error
                        })
                        break

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
            routine_id=resolved.routine_id,
            variables=variables,
            status=status,
            started_at=started_at,
            ended_at=ended_at,
            executed=executed,
            interventions=list(self._interventions),
            deviations=deviations,
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
        """Execute a dispatched Workflow by traversing its milestone graph rather
        than replaying recorded clicks. Agent S grounds + actuates each milestone;
        the executor's single-message advance picks the next node / conditional
        branch from the previewed options (no extra round-trip). The click path
        stays sacred — actuation goes through the same restricted exec helper."""
        from engine.workflow_executor import WorkflowExecutor, AgentSWorker
        from engine import workflow_control
        from engine.workflow_store import WorkflowStore

        self._halt_flag.clear()
        self._agent_s.reset()
        run_id = str(uuid.uuid4())[:8]
        started_at = time.time()

        worker = AgentSWorker(self._agent_s, self._exec_agent_code)
        executor = WorkflowExecutor(
            worker, event_emit=event_bus.emit, gate=workflow_control.review,
            telemetry=self._telemetry,
        )
        # Parent span so Arize Phoenix traces THROUGH the workflow: each milestone's
        # workflow.node span nests under this workflow.execute span.
        with self._telemetry.span("workflow.execute") as _wspan:
            _wspan.set_attribute("workflow.id", workflow.id)
            _wspan.set_attribute("workflow.name", workflow.name or "")
            _wspan.set_attribute("workflow.goal", goal)
            wf_run = executor.run(workflow, goal=goal, params=params, profile=profile)
            _wspan.set_attribute("workflow.status", wf_run.status)
            _wspan.set_attribute("workflow.steps", len(wf_run.path))

        # Teaching loop — bake any `remember`-flagged human steers into the
        # workflow so the branch/procedure is automatic next run, then persist the
        # bumped version. one_off steers are journal-only (never baked).
        try:
            applied = workflow_control.bake(workflow, wf_run.interventions, run_id)
            if applied:
                store = WorkflowStore()
                workflow.version += 1
                store.save(workflow)
                event_bus.emit("workflow.baked", {
                    "workflow_id": workflow.id, "version": workflow.version,
                    "ops": applied,
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

    # ── step dispatcher — SYNCHRONOUS, no async, no network ─────────────────

    def _build_instruction(self, step: RoutineStep, index: int, routine) -> str:
        """Compose the per-step instruction string passed to Agent S."""
        if self._pending_override:
            inst = self._pending_override
            self._pending_override = ""
            return inst
        if routine.step_instructions and index in routine.step_instructions:
            return routine.step_instructions[index]
        return step.description or step.action

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
    ) -> Optional[str]:
        """
        Ask Agent S to plan this step. Returns executable Python/pyautogui code,
        or None to fall back to _dispatch(defined step).

        batch_fill steps use plan_batch_action — one API call for all fields.
        All other steps use plan_action — one API call per step.
        """
        if self._mode != "LIVE" or not self._agent_s.available:
            return None
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
        # the rest don't benefit from vision planning.
        if step.action in ("batch_fill", "wait", "hotkey", "open_app"):
            return None

        instruction = self._build_instruction(step, index, routine)
        return self._agent_s.plan_action(instruction, index, demo_ctx)

    def _exec_agent_code(self, code: str) -> None:
        """
        Execute Agent S-generated Python/pyautogui code in a restricted namespace.
        Agent S returns strings like: "pyautogui.click(760, 300)"
        """
        exec(code, {"__builtins__": __builtins__, "pyautogui": pyautogui, "time": time})  # noqa: S102

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
            import subprocess as _sp
            text_val = sub(step.text) or ""
            press_return = text_val.endswith("\n")
            text_body = text_val.rstrip("\n")
            if text_body:
                # typewrite() silently drops : @ / etc. on macOS; clipboard-paste handles all chars.
                _sp.run(["pbcopy"], input=text_body.encode(), check=False)
                pyautogui.hotkey("cmd", "v")
                time.sleep(0.05)
            if press_return:
                pyautogui.press("return")

        elif a == "hotkey":
            pyautogui.hotkey(*(step.keys or []))

        elif a == "open_app":
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
                    import io as _io, subprocess as _sp0
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
            # Invoked only at routine boundaries, never mid-sequence
            target_url = (step.browser_step or {}).get("url", "")
            if target_url:
                blocked = policy_engine.check_containment("browser", target_url)
                if blocked:
                    raise ValueError(f"[containment] {blocked['reason']}")
            if FEATURES["browserbase"] and step.browser_step:
                from services.browserbase_routine import run_browser_step
                run_browser_step(step.browser_step)
            else:
                import webbrowser
                webbrowser.open("http://localhost:8765/demo-web")
                time.sleep(2.0)

        else:
            raise ValueError(f"Unknown action: '{a}'")

    def _js_fill(self, plan: list) -> None:
        """
        Actuate a fill plan ([{html_name, value}]) via Chrome JS injection — no
        keyboard focus required, immune to focus-stealing. Auto-enables Chrome's
        "Allow JavaScript from Apple Events", and falls back to click + Tab if JS
        stays blocked.
        """
        import os as _os, subprocess as _sp, tempfile as _tmp

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

        default   = "approve" if verdict == "flag" else "halt"
        decision  = request_approval(index, reason, timeout=30.0, default=default)

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
