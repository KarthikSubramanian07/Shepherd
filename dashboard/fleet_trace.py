"""
Per-agent execution-trace accumulator for the Fleet view.

Subscribes to ``event_bus`` and builds one trace dict per ``agent_id``,
using the same data structure the coordinator uses for its single-agent
trace so the existing ``TraceGraph`` React component can render it
unchanged.

Non-blocking by design: events arrive on the emitting thread (fire-and-
forget from the driver/engine), are accumulated in-memory, and the
frontend polls at its own cadence.
"""
from __future__ import annotations

import threading
from typing import Optional

from dashboard.events import event_bus

# ── trace bookkeeping (mirrors coordinator/server.py::_new_trace) ─────────
_MAX_TRACE_NODES = 2000
_MAX_FINISHED_TRACES = 200

_lock = threading.Lock()
# agent_id → trace dict
_traces: dict[str, dict] = {}
# run_id → agent_id  (engine step.* events carry run_id but not agent_id)
_run_to_agent: dict[str, str] = {}


def _new_trace(run_id: Optional[str], routine_id: Optional[str],
               kind: Optional[str]) -> dict:
    return {
        "run_id": run_id, "routine_id": routine_id, "kind": kind,
        "known": None, "status": "running", "current": None,
        "nodes": [], "by_index": {},
    }


def _resolve_agent_id(data: dict) -> Optional[str]:
    """Extract agent_id from event data, falling back to the run_id map."""
    aid = data.get("agent_id")
    if aid:
        return aid
    rid = data.get("run_id")
    if rid:
        with _lock:
            return _run_to_agent.get(rid)
    return None


def _on_event(message: dict) -> None:
    """Synchronous event_bus subscriber — called inline on the emitting thread."""
    t = message.get("type", "")
    d = message.get("data", {})

    # ── execution lifecycle ────────────────────────────────────────────────
    if t == "execution.start":
        aid = d.get("agent_id")
        if not aid:
            return
        rid = d.get("run_id")
        tr = _new_trace(rid, d.get("routine_id"), d.get("mode"))
        with _lock:
            _traces[aid] = tr
            if rid:
                _run_to_agent[rid] = aid
        return

    if t == "task.graph.loaded":
        aid = _resolve_agent_id(d)
        if not aid:
            return
        with _lock:
            tr = _traces.get(aid)
        if tr is None:
            return
        if d.get("run_id"):
            tr["run_id"] = d["run_id"]
        if d.get("routine_id"):
            tr["routine_id"] = d["routine_id"]
        tr["known"] = bool(d.get("known"))
        return

    if t == "execution.complete":
        aid = _resolve_agent_id(d)
        if not aid:
            return
        with _lock:
            tr = _traces.get(aid)
        if tr:
            tr["status"] = d.get("status", "completed")
            tr["current"] = None
        _evict_finished()
        return

    if t in ("execution.halted", "execution.suspended"):
        aid = _resolve_agent_id(d)
        if not aid:
            return
        with _lock:
            tr = _traces.get(aid)
        if tr:
            tr["status"] = "halted" if t == "execution.halted" else "suspended"
            tr["current"] = None
        return

    if t == "execution.resumed":
        aid = _resolve_agent_id(d)
        if not aid:
            return
        with _lock:
            tr = _traces.get(aid)
        if tr:
            tr["status"] = "running"
        return

    # ── step events (mirrors coordinator _apply_trace_event) ──────────────
    if not t.startswith("step."):
        return

    aid = _resolve_agent_id(d)
    if not aid:
        return

    with _lock:
        tr = _traces.get(aid)
    if tr is None:
        return

    idx = d.get("index")
    if idx is None:
        idx = d.get("step_index")
    if idx is None:
        return

    by = tr["by_index"]
    if len(by) >= _MAX_TRACE_NODES and idx not in by:
        return
    node = by.get(idx)
    if node is None:
        node = {"index": idx, "status": "pending"}
        by[idx] = node
        tr["nodes"].append(node)

    if t == "step.start":
        node.update({"action": d.get("action"),
                     "description": d.get("description"), "status": "running"})
        tr["current"] = idx
    elif t == "step.agent_s_thinking":
        node["thinking"] = d.get("description") or node.get("thinking")
    elif t == "step.complete":
        node.update({"status": d.get("status", "completed"),
                     "durationMs": d.get("duration_ms")})
    elif t == "step.error":
        node.update({"status": "error", "error": d.get("error")})
    elif t in ("step.deviation", "step.fallback"):
        node["note"] = d.get("reason") or d.get("description") or t


# ── public API ────────────────────────────────────────────────────────────

def _evict_finished() -> None:
    """Drop the oldest finished traces when we exceed the cap."""
    with _lock:
        finished = [(k, v) for k, v in _traces.items()
                    if v.get("status") not in ("running", None)]
        excess = len(finished) - _MAX_FINISHED_TRACES
        if excess <= 0:
            return
        for aid, tr in finished[:excess]:
            _traces.pop(aid, None)
            rid = tr.get("run_id")
            if rid:
                _run_to_agent.pop(rid, None)


def get_trace(agent_id: str) -> Optional[dict]:
    """Return a roster-safe snapshot of the trace for *agent_id*, or None."""
    with _lock:
        tr = _traces.get(agent_id)
    if tr is None:
        return None
    return {
        "runId":     tr.get("run_id"),
        "routineId": tr.get("routine_id"),
        "kind":      tr.get("kind"),
        "known":     tr.get("known"),
        "status":    tr.get("status", "running"),
        "current":   tr.get("current"),
        "nodes":     [dict(n) for n in tr.get("nodes", [])],
    }


def remove_trace(agent_id: str) -> None:
    """Drop trace state for a finished agent (called on reap)."""
    with _lock:
        tr = _traces.pop(agent_id, None)
        if tr and tr.get("run_id"):
            _run_to_agent.pop(tr["run_id"], None)


def install() -> None:
    """Subscribe to event_bus. Idempotent (EventBus deduplicates)."""
    event_bus.subscribe(_on_event)
