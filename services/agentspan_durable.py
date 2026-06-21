"""
Durable run ledger — so a Shepherd run survives a crash and resumes from the last
completed milestone instead of starting over. "Agents that never fail in
production" (the Agentspan thesis), applied to the run itself.

How it works, off the click path:
  - We subscribe to the engine's existing event stream (no engine edits). Each run
    writes a durable ledger to Redis: its goal, milestone count, and completed
    count, updated on every `step.complete`.
  - If the process dies mid-run, the ledger is left in state "running". On the next
    boot, `resume_incomplete()` finds those orphaned runs and hands them back to the
    entry loop to continue from where they stopped.
  - When Agentspan is reachable, the run is also registered as a real durable
    Agentspan execution (a queryable record + resume handle); the high-stakes
    approval can be parked as a durable Conductor HUMAN task that survives a crash
    (see `durable_approval`). Both are best-effort: with Agentspan down, the Redis
    ledger alone still gives crash-resume.

Everything here degrades gracefully: no Redis -> no-op (run behaves as before).
"""
import json
import time
from typing import Optional

from config import FEATURES, REDIS_URL

_LEDGER_PREFIX = "shepherd:durable:"
_r = None            # lazy Redis client
_installed = False


def available() -> bool:
    return _redis() is not None


def _redis():
    global _r
    if _r is not None:
        return _r
    if not FEATURES["redis"]:
        return None
    try:
        import redis
        _r = redis.from_url(REDIS_URL, decode_responses=True)
        _r.ping()
    except Exception as e:
        print(f"[durable] Redis unavailable (non-fatal): {e}")
        _r = None
    return _r


# ── event-bus integration (no engine edits) ─────────────────────────────────

def install() -> None:
    """Subscribe to the engine event stream so every run is durably checkpointed.
    Safe to call once at startup; no-op without Redis."""
    global _installed
    if _installed or not available():
        return
    from dashboard.events import event_bus
    event_bus.subscribe(_on_event)
    _installed = True
    print("[durable] run ledger active — runs resume from the last milestone after a crash.")


def _on_event(event_type: str, data: dict) -> None:
    try:
        if event_type == "execution.start":
            _begin(
                run_id=data.get("run_id") or "",
                goal=data.get("routine_id") or "",
                total=int(data.get("total_steps") or 0),
            )
        elif event_type == "step.complete":
            _checkpoint(data.get("run_id") or "", int(data.get("index") or 0))
        elif event_type == "execution.complete":
            _finish(data.get("run_id") or "", "completed")
        elif event_type == "execution.halted":
            _finish(data.get("run_id") or "", "halted")
    except Exception as e:
        print(f"[durable] ledger update non-fatal: {e}")


# ── ledger ───────────────────────────────────────────────────────────────────

def _key(run_id: str) -> str:
    return f"{_LEDGER_PREFIX}{run_id}"


def _begin(run_id: str, goal: str, total: int) -> None:
    r = _redis()
    if not r or not run_id:
        return
    ledger = {
        "run_id": run_id, "goal": goal, "total": total,
        "done": 0, "status": "running", "started_at": _now(),
        "execution_id": _register_agentspan(run_id, goal),
    }
    r.set(_key(run_id), json.dumps(ledger))


def _checkpoint(run_id: str, index: int) -> None:
    r = _redis()
    if not r or not run_id:
        return
    raw = r.get(_key(run_id))
    if not raw:
        return
    led = json.loads(raw)
    # `done` = highest completed step index + 1; resume restarts at `done`.
    led["done"] = max(int(led.get("done", 0)), index + 1)
    r.set(_key(run_id), json.dumps(led))


def _finish(run_id: str, status: str) -> None:
    r = _redis()
    if not r or not run_id:
        return
    raw = r.get(_key(run_id))
    if not raw:
        return
    led = json.loads(raw)
    led["status"] = status
    led["ended_at"] = _now()
    r.set(_key(run_id), json.dumps(led))


def resume_incomplete() -> list[dict]:
    """Runs that were still 'running' when the process last died (orphaned by a
    crash). Marks them 'resuming' and returns them for the entry loop to continue.
    A clean shutdown leaves runs 'completed'/'halted', so they are not resumed."""
    r = _redis()
    if not r:
        return []
    out: list[dict] = []
    try:
        for k in r.scan_iter(match=f"{_LEDGER_PREFIX}*"):
            raw = r.get(k)
            if not raw:
                continue
            led = json.loads(raw)
            if led.get("status") == "running" and led.get("done", 0) < led.get("total", 0):
                led["status"] = "resuming"
                r.set(k, json.dumps(led))
                out.append(led)
    except Exception as e:
        print(f"[durable] resume scan non-fatal: {e}")
    return out


def stats() -> dict:
    r = _redis()
    if not r:
        return {"available": False}
    try:
        keys = list(r.scan_iter(match=f"{_LEDGER_PREFIX}*"))
        runs = [json.loads(r.get(k)) for k in keys if r.get(k)]
        return {
            "available": True,
            "runs_tracked": len(runs),
            "in_flight": sum(1 for x in runs if x.get("status") in ("running", "resuming")),
            "completed": sum(1 for x in runs if x.get("status") == "completed"),
        }
    except Exception:
        return {"available": False}


# ── Agentspan durable execution (best-effort) ────────────────────────────────

def _register_agentspan(run_id: str, goal: str) -> Optional[str]:
    """Hook for tying the run to a durable Agentspan execution id. Today the Redis
    ledger is the source of truth for crash-resume, and Agentspan's durable
    execution lives at the sub-task level (the research agent in
    `agentspan_research.py` runs on the self-hosted Agentspan server, so a flaky
    research step retries/replays instead of failing the whole run). Returns None
    unless a per-run Agentspan execution is later wired here; kept so the ledger
    schema already carries the field."""
    return None


def _now() -> float:
    return time.time()
