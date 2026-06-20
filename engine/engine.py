"""
ShepherdExecutionEngine

LIVE mode  — Agent S plans actions against the recorded demonstration; pyautogui actuates.
LOCKED mode — Deterministic verbatim replay of pre-mapped steps (offline demo floor).

The click path is synchronous and sacred.
Nothing async, networked, or ML-based runs inside a routine's step sequence.
"""
import subprocess
import threading
import time
import uuid
from typing import Optional

import pyautogui

from config import FEATURES, EXECUTION_MODE
from shepherd_types import ExecutionResult, ResolvedRoutine, RoutineStep, StepRecord
from engine.coords import get as get_coord
from engine.routines import get_routine
from engine.agent_s_adapter import AgentSAdapter
from dashboard.events import event_bus

pyautogui.FAILSAFE = True   # slam mouse to top-left corner to abort
pyautogui.PAUSE    = 0.3    # deliberate, watchable motion — this is the wow factor

_APP_SETTLE = 2.0           # seconds to wait after open_app


class ShepherdExecutionEngine:
    def __init__(self, coords: dict, telemetry, mode: str = EXECUTION_MODE, agent_s=None) -> None:
        self._coords    = coords
        self._telemetry = telemetry
        self._mode      = mode
        self._agent_s   = agent_s if agent_s is not None else AgentSAdapter()
        self.last_step_records: list[StepRecord] = []
        self._halt_flag = threading.Event()

    def request_halt(self) -> None:
        """Set by monitor_agent or spoken 'stop' command. Checked at each step boundary."""
        self._halt_flag.set()

    def execute(self, resolved: ResolvedRoutine) -> ExecutionResult:
        self._halt_flag.clear()
        self.last_step_records = []
        run_id     = str(uuid.uuid4())[:8]
        started_at = time.time()

        routine   = get_routine(resolved.routine_id)
        variables = resolved.variables

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

        steps_done = 0
        error: Optional[str] = None
        status = "completed"

        with self._telemetry.span("routine.execute") as span:
            span.set_attribute("routine.id",   resolved.routine_id)
            span.set_attribute("routine.mode", self._mode)
            for k, v in variables.items():
                span.set_attribute(f"routine.variable.{k}", v)

            for i, step in enumerate(routine.steps):
                # ── halt check (boundary, never mid-click) ──────────────────
                if self._halt_flag.is_set():
                    status = "aborted"
                    event_bus.emit("execution.halted", {
                        "run_id": run_id, "step_index": i, "reason": "halt_requested"
                    })
                    break

                # ── monitor check at high-stakes boundaries ──────────────────
                if i in routine.high_stakes_steps:
                    verdict = self._check_monitor(step, i, run_id)
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

                # In LIVE mode, let Agent S plan the action (falls back to defined step)
                step = self._live_step(step, i, routine)

                step_status = "completed"
                step_error: Optional[str] = None

                with self._telemetry.span(f"action.{step.action}") as s:
                    s.set_attribute("action.type",   step.action)
                    s.set_attribute("action.index",  i)
                    if step.target:
                        s.set_attribute("action.target", step.target)
                    try:
                        self._dispatch(step, variables)
                        steps_done += 1
                    except Exception as exc:
                        step_status = "failed"
                        step_error  = str(exc)
                        error       = step_error
                        status      = "failed"
                        if FEATURES["sentry"]:
                            import sentry_sdk
                            sentry_sdk.capture_exception(exc)
                        s.set_attribute("error.message", step_error)
                        event_bus.emit("step.error", {
                            "run_id": run_id, "index": i, "error": step_error
                        })
                        break

                dur_ms = int((time.time() - step_t0) * 1000)
                self.last_step_records.append(StepRecord(
                    index=i, action=step.action, target=step.target,
                    status=step_status, started_at=step_t0,
                    duration_ms=dur_ms, error=step_error,
                ))
                event_bus.emit("step.complete", {
                    "run_id": run_id, "index": i,
                    "status": step_status, "duration_ms": dur_ms,
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
        return result

    # ── step dispatcher — SYNCHRONOUS, no async, no network ─────────────────

    def _live_step(self, step: RoutineStep, index: int, routine) -> RoutineStep:
        """
        In LIVE mode, ask Agent S to plan the action for this step.
        Falls back to the routine's defined step if Agent S is unavailable or returns None.
        Agent S uses the demonstration + per-step instruction as context.
        """
        if self._mode != "LIVE" or not self._agent_s.available:
            return step
        instruction = ""
        if routine.step_instructions and index in routine.step_instructions:
            instruction = routine.step_instructions[index]
        elif step.description:
            instruction = step.description
        demo_ctx = str(routine.demonstration) if routine.demonstration else ""
        planned = self._agent_s.plan_action(instruction, index, demo_ctx)
        if planned is None:
            return step
        # Merge planned fields onto a copy of the defined step (defined step is fallback)
        from dataclasses import replace as dc_replace
        overrides = {k: v for k, v in planned.items() if v is not None}
        try:
            return dc_replace(step, **overrides)
        except Exception:
            return step

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
            pyautogui.typewrite(sub(step.text) or "", interval=0.05)

        elif a == "hotkey":
            pyautogui.hotkey(*(step.keys or []))

        elif a == "open_app":
            subprocess.Popen(["open", "-a", sub(step.target) or ""])
            time.sleep(_APP_SETTLE)

        elif a == "wait":
            time.sleep(step.seconds or 1.0)

        elif a == "browser":
            # Invoked only at routine boundaries, never mid-sequence
            if FEATURES["browserbase"] and step.browser_step:
                from integrations.browserbase_routine import run_browser_step
                run_browser_step(step.browser_step)
            else:
                import webbrowser
                webbrowser.open("http://localhost:8765/demo-web")
                time.sleep(2.0)

        else:
            raise ValueError(f"Unknown action: '{a}'")

    def _check_monitor(self, step: RoutineStep, index: int, run_id: str) -> str:
        """Calls monitor at boundary. Returns 'ok', 'flag', or 'halt'. Never inside click."""
        try:
            from integrations.monitor_agent import check_step
            result = check_step(step, {"step_index": index})
            verdict = result.get("verdict", "ok")
            reason  = result.get("reason", "")
            if verdict in ("flag", "halt"):
                event_bus.emit("monitor.alert", {
                    "run_id": run_id, "step_index": index,
                    "verdict": verdict, "reason": reason,
                    "trigger": step.monitor_trigger,
                })
            return verdict
        except Exception as exc:
            print(f"[monitor] check failed (non-fatal): {exc}")
            return "ok"
