"""
Agent memory — cross-run semantic recall on a Redis vector set.

Beyond caching: every finished run is embedded (its goal + the milestones it
actually performed) and stored in a Redis 8 vector set (`VADD`). When a new goal
arrives, we `VSIM` it against that memory and, if a similar *successful* prior run
exists, recall its milestone chain so the planner reuses a proven path instead of
planning from scratch. Crucially this matches by MEANING, so a reworded goal
("submit my application for the Acme role" vs "apply to the Acme job") recalls the
same run — which the slug-keyed per-goal graph cannot do.

Same vector-set primitive that powers intent routing and the semantic cache, now
as the agent's long-term memory. Off the click path; lazy + graceful (no Redis /
embedder -> no recall, the agent plans fresh as before).
"""
import hashlib
import json
from typing import Optional

from config import FEATURES, REDIS_URL
from services import embeddings

_VSET = "shepherd:runmem"
_RECALL_MIN_SIM = 0.80
_r = None
_installed = False
# Per-run scratch the indexer fills from the event stream before a run completes.
_pending: dict = {}


def _redis():
    global _r
    if _r is not None:
        return _r
    if not FEATURES["redis"]:
        return None
    try:
        import redis
        _r = redis.from_url(REDIS_URL)
        _r.ping()
    except Exception as e:
        print(f"[run_memory] Redis unavailable (non-fatal): {e}")
        _r = None
    return _r


def available() -> bool:
    return _redis() is not None and embeddings.available()


def _key_text(goal: str, milestones: list[str]) -> str:
    return f"{goal} | " + " > ".join(milestones)


def index_run(goal: str, milestones: list[str], status: str, run_id: str) -> None:
    """Embed and store a finished run so future similar goals can recall it."""
    if not available() or not goal:
        return
    try:
        r = _redis()
        elem = hashlib.sha1(run_id.encode()).hexdigest()[:16]
        vec = embeddings.embed_bytes(_key_text(goal, milestones))
        r.execute_command("VADD", _VSET, "FP32", vec, elem)
        r.set(f"{_VSET}:val:{elem}", json.dumps({
            "run_id": run_id, "goal": goal, "milestones": milestones, "status": status,
        }))
    except Exception as e:
        print(f"[run_memory] index non-fatal: {e}")


def recall(goal: str, *, min_sim: float = _RECALL_MIN_SIM) -> Optional[dict]:
    """Most similar SUCCESSFUL prior run for this goal, or None. Returns
    {run_id, goal, milestones, similarity}."""
    if not available() or not goal:
        return None
    try:
        r = _redis()
        vec = embeddings.embed_bytes(goal)
        res = r.execute_command("VSIM", _VSET, "FP32", vec, "WITHSCORES", "COUNT", 5)
        # res is [elem, score, elem, score, ...]; pick the best completed match.
        for i in range(0, len(res) - 1, 2):
            elem = res[i].decode() if isinstance(res[i], bytes) else res[i]
            sim = float(res[i + 1])
            if sim < min_sim:
                break
            raw = r.get(f"{_VSET}:val:{elem}")
            if not raw:
                continue
            rec = json.loads(raw)
            if rec.get("status") == "completed" and rec.get("milestones"):
                rec["similarity"] = round(sim, 4)
                return rec
    except Exception as e:
        print(f"[run_memory] recall non-fatal: {e}")
    return None


def stats() -> dict:
    r = _redis()
    if not r:
        return {"available": False}
    try:
        try:
            entries = int(r.execute_command("VCARD", _VSET) or 0)
        except Exception:
            entries = 0
        return {"available": True, "runs_indexed": entries}
    except Exception:
        return {"available": False}


# ── event-bus indexer: index every completed run (no engine edits) ───────────

def install() -> None:
    global _installed
    if _installed or not available():
        return
    try:
        from dashboard.events import event_bus
        event_bus.subscribe(_on_event)
        _installed = True
        print("[run_memory] cross-run recall active — similar goals reuse proven milestones.")
    except Exception as e:
        print(f"[run_memory] install non-fatal: {e}")


def _on_event(event_type: str, data: dict) -> None:
    try:
        run_id = data.get("run_id") or ""
        if event_type == "execution.start":
            _pending[run_id] = {"goal": data.get("routine_id") or "", "milestones": []}
        elif event_type == "task.graph.loaded":
            labels = [m.get("label") for m in (data.get("milestones") or []) if m.get("label")]
            if run_id in _pending and labels:
                _pending[run_id]["milestones"] = labels
        elif event_type == "execution.complete":
            p = _pending.pop(run_id, None)
            if p and p["goal"]:
                index_run(p["goal"], p["milestones"], "completed", run_id)
        elif event_type == "execution.halted":
            _pending.pop(run_id, None)
    except Exception as e:
        print(f"[run_memory] index event non-fatal: {e}")
