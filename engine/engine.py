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
                agent_code   = self._live_execute(defined_step, i, routine)

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
                            self._exec_agent_code(agent_code)
                        else:
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

    def _build_instruction(self, step: RoutineStep, index: int, routine) -> str:
        """Compose the per-step instruction string passed to Agent S."""
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
        """
        if self._mode != "LIVE" or not self._agent_s.available:
            return None
        instruction = self._build_instruction(step, index, routine)
        demo_ctx    = self._build_demo_context(routine)
        return self._agent_s.plan_action(instruction, index, demo_ctx)

    def _exec_agent_code(self, code: str) -> None:
        """
        Execute Agent S-generated Python/pyautogui code in a restricted namespace.
        Agent S returns strings like: "pyautogui.click(760, 300)"
        """
        exec(code, {"pyautogui": pyautogui, "time": time})  # noqa: S102

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
                from services.browserbase_routine import run_browser_step
                run_browser_step(step.browser_step)
            else:
                import webbrowser
                webbrowser.open("http://localhost:8765/demo-web")
                time.sleep(2.0)

        else:
            raise ValueError(f"Unknown action: '{a}'")

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
        try:
            from services.monitor_agent import check_step
            from engine.approvals import suggestions_for, request_approval
            result  = check_step(step, {"step_index": index})
            verdict = result.get("verdict", "ok")
            reason  = result.get("reason", "")

            if verdict in ("flag", "halt"):
                sugg = suggestions_for(step.monitor_trigger)

                # Pull historical stats to enrich the alert message
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

                # Block for human decision; default conservatively
                default = "approve" if verdict == "flag" else "halt"
                decision = request_approval(index, reason, timeout=30.0, default=default)

                event_bus.emit("monitor.decision", {
                    "run_id": run_id, "step_index": index, "decision": decision,
                })

                if decision == "halt":
                    return "halt"

                # Record the approval in evolution stats
                if self._evolution and routine_id:
                    try:
                        self._evolution.record_approval(routine_id, index)
                    except Exception:
                        pass

                return "ok"

            return verdict
        except Exception as exc:
            print(f"[monitor] check failed (non-fatal): {exc}")
            return "ok"
