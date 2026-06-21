"""
ActionArbiter — the action queue between agents.

This is the heart of multi-agent safety. Every agent that wants to actuate a
surface must hold that surface's *lease*. The arbiter grants at most one lease
per surface at a time, so two agents can never drive the same physical desktop
(or the same Browserbase session) simultaneously — the literal data race the
whole feature exists to prevent.

Properties:
  - **Mutual exclusion** per surface: one holder at a time.
  - **Parallelism** across surfaces: ``LOCAL_DESKTOP`` serializes all local
    agents, while each ``BROWSERBASE:<id>`` runs free of the others.
  - **Fairness**: FIFO within a priority level (no starvation).
  - **Halt preemption**: a higher-priority waiter (e.g. a "stop") jumps the
    queue, and ``cancel(agent_id)`` wakes a blocked acquire so a halted agent
    stops waiting immediately. A *held* lease is never yanked mid-action (you
    cannot interrupt a click that is already running) — preemption only reorders
    or cancels *waiters*.
  - **Observability**: every wait/grant/release emits an ``arbiter.*`` event and
    is recorded on an optional timeline so the Control Hub (and tests) can see
    exactly who holds and who waits.

Nothing here touches the network or the model — it is pure, fast, in-process
synchronization, safe to call from any thread.
"""
from __future__ import annotations

import itertools
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator, Optional

# Priority lanes. Higher number is served first; FIFO within a lane.
PRIORITY_NORMAL = 0
PRIORITY_HALT = 100


class LeaseCancelled(Exception):
    """Raised by ``hold()`` when an agent's queued acquire is cancelled (halt)."""


@dataclass
class Lease:
    surface: str
    agent_id: str
    priority: int
    seq: int
    acquired_at: float = 0.0
    released_at: float = 0.0

    @property
    def held_ms(self) -> int:
        end = self.released_at or time.time()
        return int((end - self.acquired_at) * 1000)


@dataclass
class _Waiter:
    agent_id: str
    priority: int
    seq: int
    cancelled: bool = False


@dataclass
class _SurfaceState:
    holder: Optional[Lease] = None
    waiters: list[_Waiter] = field(default_factory=list)


@dataclass
class _Span:
    surface: str
    agent_id: str
    acquired_at: float
    released_at: float


class ActionArbiter:
    def __init__(
        self,
        on_event: Optional[Callable[[str, dict], None]] = None,
        record_timeline: bool = False,
    ) -> None:
        # One condition guards all surface state; lease hold times are short
        # (one action batch), so a single lock is simplest and contention-free
        # in practice.
        self._cv = threading.Condition()
        self._surfaces: dict[str, _SurfaceState] = {}
        self._seq = itertools.count()
        self._on_event = on_event
        self._record_timeline = record_timeline
        self._timeline: list[_Span] = []

    # ── core lease API ────────────────────────────────────────────────────────
    def acquire(
        self,
        surface: str,
        agent_id: str,
        priority: int = PRIORITY_NORMAL,
        timeout: Optional[float] = None,
    ) -> Optional[Lease]:
        """Block until this agent holds ``surface``. Returns the Lease, or None
        if cancelled (halt) or timed out. FIFO within ``priority``."""
        deadline = (time.time() + timeout) if timeout is not None else None
        with self._cv:
            st = self._surfaces.setdefault(surface, _SurfaceState())
            w = _Waiter(agent_id=agent_id, priority=priority, seq=next(self._seq))
            st.waiters.append(w)
            announced_wait = False
            while True:
                if w.cancelled:
                    self._drop(st, w)
                    self._emit("arbiter.cancel", surface, agent_id)
                    return None
                # Eligible to run iff the surface is free and we are the highest
                # priority / earliest-queued waiter.
                if st.holder is None and self._front(st) is w:
                    self._drop(st, w)
                    lease = Lease(
                        surface=surface, agent_id=agent_id, priority=priority,
                        seq=w.seq, acquired_at=time.time(),
                    )
                    st.holder = lease
                    self._emit("arbiter.grant", surface, agent_id,
                               waiters=len(st.waiters))
                    return lease
                # Only announce a wait if we actually have to block (someone else
                # holds it) — an uncontended acquire shouldn't spam the queue UI.
                if not announced_wait:
                    announced_wait = True
                    self._emit("arbiter.wait", surface, agent_id,
                               waiters=len(st.waiters))
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self._drop(st, w)
                        return None
                self._cv.wait(remaining)

    def release(self, lease: Lease) -> None:
        """Release a held lease and wake the next waiter."""
        with self._cv:
            st = self._surfaces.get(lease.surface)
            if not st or st.holder is not lease:
                return
            st.holder = None
            lease.released_at = time.time()
            if self._record_timeline:
                self._timeline.append(_Span(
                    surface=lease.surface, agent_id=lease.agent_id,
                    acquired_at=lease.acquired_at, released_at=lease.released_at,
                ))
            self._emit("arbiter.release", lease.surface, lease.agent_id,
                       held_ms=lease.held_ms, waiters=len(st.waiters))
            self._cv.notify_all()

    @contextmanager
    def hold(
        self,
        surface: str,
        agent_id: str,
        priority: int = PRIORITY_NORMAL,
        timeout: Optional[float] = None,
    ) -> Iterator[Lease]:
        """Context manager: acquire on enter, release on exit. Raises
        :class:`LeaseCancelled` if the acquire is cancelled/halted so the caller
        skips the protected action instead of running it unguarded."""
        lease = self.acquire(surface, agent_id, priority, timeout)
        if lease is None:
            raise LeaseCancelled(
                f"{agent_id} lease on {surface} not granted (halted or timed out)")
        try:
            yield lease
        finally:
            self.release(lease)

    def guard(self, surface: str, agent_id: str):
        """Return a zero-arg factory the engine calls as ``with guard():`` around
        each actuation. Bundling focus+actuate inside one hold is what stops a
        second agent from stealing window focus mid-batch."""
        def _factory():
            return self.hold(surface, agent_id)
        return _factory

    # ── halt / preemption ─────────────────────────────────────────────────────
    def cancel(self, agent_id: str) -> int:
        """Cancel every *queued* acquire for this agent (it is halting). Held
        leases are left to release normally. Returns how many waiters were
        cancelled."""
        n = 0
        with self._cv:
            for st in self._surfaces.values():
                for w in st.waiters:
                    if w.agent_id == agent_id and not w.cancelled:
                        w.cancelled = True
                        n += 1
            if n:
                self._cv.notify_all()
        return n

    # ── introspection (Control Hub + tests) ───────────────────────────────────
    def snapshot(self) -> list[dict]:
        """Per-surface view of who holds and who waits, for the action-queue UI."""
        with self._cv:
            out = []
            for surface, st in self._surfaces.items():
                if st.holder is None and not st.waiters:
                    continue
                out.append({
                    "surface": surface,
                    "holder": st.holder.agent_id if st.holder else None,
                    "held_ms": st.holder.held_ms if st.holder else 0,
                    "waiters": [
                        {"agent_id": w.agent_id, "priority": w.priority}
                        for w in self._ordered(st)
                    ],
                })
            return out

    def timeline(self) -> list[_Span]:
        """Recorded (acquire, release) spans — only when ``record_timeline``."""
        with self._cv:
            return list(self._timeline)

    # ── internals ─────────────────────────────────────────────────────────────
    @staticmethod
    def _ordered(st: _SurfaceState) -> list[_Waiter]:
        # Highest priority first, then FIFO by seq. Cancelled waiters are on their
        # way out (a halt woke them) — don't surface them in the queue view.
        live = [w for w in st.waiters if not w.cancelled]
        return sorted(live, key=lambda w: (-w.priority, w.seq))

    def _front(self, st: _SurfaceState) -> Optional[_Waiter]:
        live = [w for w in st.waiters if not w.cancelled]
        if not live:
            return None
        return min(live, key=lambda w: (-w.priority, w.seq))

    @staticmethod
    def _drop(st: _SurfaceState, w: _Waiter) -> None:
        try:
            st.waiters.remove(w)
        except ValueError:
            pass

    def _emit(self, event_type: str, surface: str, agent_id: str, **extra) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, {
                "surface": surface, "agent_id": agent_id, **extra,
            })
        except Exception:
            pass
