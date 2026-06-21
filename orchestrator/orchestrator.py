"""
Orchestrator — the supervisor over a pool of agent workers.

Responsibilities:
  - own the single :class:`ActionArbiter` (the action queue) all workers share,
  - accept dispatched tasks, mint an ``agent_id``, and spawn an
    :class:`AgentWorker` thread (subject to a concurrency cap; over-cap tasks
    wait in a backlog),
  - track live + finished workers for the Control Hub fleet view,
  - route halts: per-agent (``halt``) and global (``halt_all``).

The orchestrator is deliberately thin — all serialization lives in the arbiter,
all execution in the workers. It never actuates anything itself.
"""
from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from typing import Callable, Optional

from orchestrator import config, surfaces
from orchestrator.arbiter import ActionArbiter
from orchestrator.worker import AgentTask, AgentWorker, RunFn


class Orchestrator:
    def __init__(
        self,
        on_event: Optional[Callable[[str, dict], None]] = None,
        telemetry=None,
        coords: Optional[dict] = None,
        max_agents: int = config.MAX_CONCURRENT_AGENTS,
    ) -> None:
        self._on_event = on_event
        self._telemetry = telemetry
        self._coords = coords or {}
        self._max_agents = max(1, max_agents)

        self.arbiter = ActionArbiter(on_event=on_event)
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._workers: dict[str, AgentWorker] = {}     # live + recently finished
        self._backlog: deque[AgentTask] = deque()
        self._bb_active = 0                             # live Browserbase sessions
        self._running = 0                               # started-but-not-finished
        # Cap retained finished workers so a long session doesn't grow the fleet
        # view / memory without bound.
        self._max_finished = 50

    # ── dispatch ──────────────────────────────────────────────────────────────
    def dispatch(
        self,
        goal: str,
        surface_kind: str = "",
        name: str = "",
        params: Optional[dict] = None,
        run_fn: Optional[RunFn] = None,
    ) -> str:
        """Queue a goal for a new agent. Returns the assigned ``agent_id``.
        Spawns immediately if under the concurrency cap, else backlogs it."""
        kind = (surface_kind or config.DEFAULT_SURFACE_KIND).lower()
        if kind not in surfaces.VALID_KINDS:
            raise ValueError(f"unknown surface kind: {kind!r}")
        agent_id = f"agent-{next(self._ids):03d}"
        task = AgentTask(
            goal=goal, surface_kind=kind, agent_id=agent_id,
            params=params or {}, name=name or agent_id, run_fn=run_fn,
        )
        with self._lock:
            if self._can_start(task):
                self._start(task)
            else:
                self._backlog.append(task)
                self._emit("agent.queued", {
                    "agent_id": agent_id, "goal": goal, "surface_kind": kind,
                    "backlog": len(self._backlog),
                })
        return agent_id

    # ── halt ──────────────────────────────────────────────────────────────────
    def halt(self, agent_id: str) -> bool:
        with self._lock:
            w = self._workers.get(agent_id)
        if w is None:
            return False
        w.halt()
        return True

    def halt_all(self) -> int:
        with self._lock:
            workers = list(self._workers.values())
            self._backlog.clear()
        for w in workers:
            w.halt()
        return len(workers)

    # ── fleet view ────────────────────────────────────────────────────────────
    def snapshot(self) -> dict:
        with self._lock:
            agents = [w.snapshot() for w in self._workers.values()]
            backlog = [
                {"agent_id": t.agent_id, "goal": t.goal, "surface_kind": t.surface_kind}
                for t in self._backlog
            ]
        return {
            "agents": agents,
            "backlog": backlog,
            "queue": self.arbiter.snapshot(),
            "max_agents": self._max_agents,
            "active": sum(1 for a in agents if a["status"] == "running"),
        }

    def get(self, agent_id: str) -> Optional[AgentWorker]:
        with self._lock:
            return self._workers.get(agent_id)

    def join_all(self, timeout: Optional[float] = None) -> None:
        """Block until all live workers finish (used by --once / tests)."""
        deadline = (time.time() + timeout) if timeout else None
        while True:
            with self._lock:
                live = [w for w in self._workers.values() if w.is_alive()]
                pending = bool(self._backlog)
            if not live and not pending:
                return
            for w in live:
                w.join(timeout=0.2)
            if deadline and time.time() > deadline:
                return

    # ── internals ─────────────────────────────────────────────────────────────
    def _can_start(self, task: AgentTask) -> bool:
        # Use an explicit counter, not is_alive(): _on_worker_done runs *inside*
        # the finishing worker's own thread (still alive), so is_alive() would
        # over-count and the backlog would never drain.
        if self._running >= self._max_agents:
            return False
        if task.surface_kind == surfaces.KIND_BROWSERBASE:
            if self._bb_active >= config.MAX_BROWSERBASE_SESSIONS:
                return False
        return True

    def _start(self, task: AgentTask) -> None:
        self._running += 1
        if task.surface_kind == surfaces.KIND_BROWSERBASE:
            self._bb_active += 1
        worker = AgentWorker(
            task=task, arbiter=self.arbiter, on_event=self._on_event,
            run_fn=task.run_fn, telemetry=self._telemetry, coords=self._coords,
            on_done=self._on_worker_done,
        )
        self._workers[task.agent_id] = worker
        self._emit("agent.spawn", {
            "agent_id": task.agent_id, "goal": task.goal,
            "surface_kind": task.surface_kind, "name": task.name,
        })
        worker.start()

    def _on_worker_done(self, worker: AgentWorker) -> None:
        with self._lock:
            self._running = max(0, self._running - 1)
            if worker.task.surface_kind == surfaces.KIND_BROWSERBASE:
                self._bb_active = max(0, self._bb_active - 1)
            self._reap_finished()
            self._drain_backlog()

    def _drain_backlog(self) -> None:
        # Caller holds the lock. Start every backlogged task the caps now allow.
        # Scan the whole backlog, not just the head: a blocked Browserbase task
        # (session cap reached) must not stall runnable local tasks behind it.
        progressed = True
        while self._backlog and progressed:
            progressed = False
            for i, task in enumerate(self._backlog):
                if self._can_start(task):
                    del self._backlog[i]
                    self._start(task)
                    progressed = True
                    break

    def _reap_finished(self) -> None:
        # Caller holds the lock. Drop the oldest finished workers beyond the cap,
        # keeping all live ones.
        finished = [w for w in self._workers.values() if not w.is_alive()]
        excess = len(finished) - self._max_finished
        if excess <= 0:
            return
        for w in sorted(finished, key=lambda w: w.started_at)[:excess]:
            self._workers.pop(w.agent_id, None)

    def _emit(self, event_type: str, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, data)
        except Exception:
            pass
