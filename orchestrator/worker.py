"""
AgentWorker — one agent, one thread, one task.

A worker runs a single goal on a single surface and reports lifecycle events
(``agent.*``) tagged with its ``agent_id`` so the Control Hub can group each
agent's run. Actuation safety is delegated entirely to the arbiter: a LOCAL
worker hands the engine an arbiter *guard* so every actuation batch (focus +
clicks/typing) is wrapped in the single ``LOCAL_DESKTOP`` lease; a Browserbase
worker drives an isolated session under that session's own lease.

The worker never touches another worker's state. Heavy modules (engine,
Browserbase) are imported lazily so the package imports cheaply and offline.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from orchestrator import surfaces
from orchestrator.arbiter import ActionArbiter, LeaseCancelled

# A custom runner: ``fn(worker) -> result``. Used by tests and bespoke flows.
RunFn = Callable[["AgentWorker"], object]


@dataclass
class AgentTask:
    goal: str
    surface_kind: str = surfaces.KIND_LOCAL   # 'local' | 'browserbase'
    agent_id: str = ""
    params: dict = field(default_factory=dict)
    name: str = ""
    # Optional custom runner — carried on the task so a backlogged task keeps it
    # when it is finally started (it would otherwise fall back to a real engine).
    run_fn: Optional[RunFn] = None


class AgentWorker(threading.Thread):
    def __init__(
        self,
        task: AgentTask,
        arbiter: ActionArbiter,
        on_event: Optional[Callable[[str, dict], None]] = None,
        run_fn: Optional[RunFn] = None,
        telemetry=None,
        coords: Optional[dict] = None,
        on_done: Optional[Callable[["AgentWorker"], None]] = None,
    ) -> None:
        super().__init__(daemon=True, name=f"agent-{task.agent_id or 'w'}")
        self.task = task
        self.agent_id = task.agent_id
        self.arbiter = arbiter
        self._on_event = on_event
        self._run_fn = run_fn
        self._telemetry = telemetry
        self._coords = coords or {}
        self._on_done = on_done

        self.status: str = "pending"      # pending|running|completed|failed|halted
        self.result = None
        self.error: Optional[str] = None
        self.surface: Optional[str] = None
        self.started_at: float = 0.0
        self.ended_at: float = 0.0
        self._halt = threading.Event()
        self._engine = None               # set for LOCAL runs (to forward halt)
        self._session = None              # set for BROWSERBASE runs (to tear down)

    # ── lifecycle ─────────────────────────────────────────────────────────────
    def run(self) -> None:
        self.status = "running"
        self.started_at = time.time()
        self._emit("agent.start", {
            "goal": self.task.goal, "surface_kind": self.task.surface_kind,
            "name": self.task.name or self.agent_id,
        })
        try:
            if self._run_fn is not None:
                self.result = self._run_fn(self)
            elif self.task.surface_kind == surfaces.KIND_BROWSERBASE:
                self.result = self._run_browserbase()
            else:
                self.result = self._run_local()
            if self._halt.is_set():
                self.status = "halted"
            else:
                self.status = self._status_from_result(self.result)
        except LeaseCancelled:
            self.status = "halted"
        except Exception as e:  # noqa: BLE001
            self.status = "failed"
            self.error = str(e)
            print(f"[worker {self.agent_id}] failed: {e}")
        finally:
            self.ended_at = time.time()
            self._teardown()
            self._emit("agent.end", {
                "status": self.status, "error": self.error,
                "duration_ms": int((self.ended_at - self.started_at) * 1000),
            })
            if self._on_done:
                try:
                    self._on_done(self)
                except Exception:
                    pass

    def halt(self) -> None:
        """Stop this agent: flag it, forward the halt to whatever it's driving,
        and only THEN cancel its queued leases. Order matters: the halt flags
        must be set before the lease is cancelled, so when a blocked acquire wakes
        with LeaseCancelled the engine already knows it's a halt (clean abort) and
        not a failure. A held lease finishes its current batch."""
        self._halt.set()
        if self._engine is not None:
            try:
                self._engine.request_halt()
            except Exception:
                pass
        if self._session is not None:
            try:
                self._session.request_halt()
            except Exception:
                pass
        self.arbiter.cancel(self.agent_id)

    @property
    def halted(self) -> bool:
        return self._halt.is_set()

    def snapshot(self) -> dict:
        return {
            "agent_id": self.agent_id,
            "name": self.task.name or self.agent_id,
            "goal": self.task.goal,
            "surface_kind": self.task.surface_kind,
            "surface": self.surface,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "duration_ms": int(((self.ended_at or time.time()) - self.started_at) * 1000)
            if self.started_at else 0,
        }

    # ── runners ───────────────────────────────────────────────────────────────
    def _run_local(self):
        """Drive the local desktop via Agent S, serialized on LOCAL_DESKTOP."""
        from engine.engine import ShepherdExecutionEngine
        from telemetry.telemetry import ShepherdTelemetry

        self.surface = surfaces.LOCAL_DESKTOP
        telemetry = self._telemetry or ShepherdTelemetry()
        guard = self.arbiter.guard(self.surface, self.agent_id)
        self._engine = ShepherdExecutionEngine(
            coords=self._coords, telemetry=telemetry, mode="AUTONOMOUS",
            actuation_guard=guard, agent_id=self.agent_id, surface=self.surface,
        )
        return self._engine.execute_autonomous(self.task.goal)

    def _run_browserbase(self):
        """Drive an isolated Browserbase session under its own lease (parallel)."""
        from services.browserbase_session import open_session
        from engine.browserbase_driver import BrowserbaseDriver

        self._session = open_session(agent_id=self.agent_id)
        self.surface = surfaces.browserbase_surface(self._session.session_id)
        guard = self.arbiter.guard(self.surface, self.agent_id)
        driver = BrowserbaseDriver(
            session=self._session, agent_id=self.agent_id,
            guard=guard, on_event=self._on_event, halt=self._halt,
        )
        self._emit("agent.surface", {
            "surface": self.surface,
            "live_view_url": getattr(self._session, "live_view_url", None),
        })
        return driver.run(self.task.goal, params=self.task.params)

    # ── helpers ───────────────────────────────────────────────────────────────
    def _teardown(self) -> None:
        if self._session is not None:
            try:
                self._session.close()
            except Exception:
                pass
            self._session = None

    @staticmethod
    def _status_from_result(result) -> str:
        status = getattr(result, "status", None)
        if status in ("completed", "failed", "aborted", "suspended"):
            # A suspended autonomous run (a monitor halt, a steer, or a parked
            # failure) is NOT a success. In orchestrated mode the per-worker
            # engine is torn down when run() ends, so a suspended task has
            # stopped and needs attention: report it as halted, never completed.
            return {"completed": "completed", "failed": "failed",
                    "aborted": "halted", "suspended": "halted"}[status]
        if isinstance(result, dict) and result.get("status"):
            return str(result["status"])
        return "failed"  # fail-safe: never report an unrecognized result as completed

    def _emit(self, event_type: str, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, {"agent_id": self.agent_id, **data})
        except Exception:
            pass
