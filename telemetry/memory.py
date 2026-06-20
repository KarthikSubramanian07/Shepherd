"""
Redis passive execution memory — stores completed runs for the Replay panel.
Read/write only. Not in the router, not in the click path.
"""
import json
import uuid
from typing import Optional

from config import FEATURES, REDIS_URL
from shepherd_types import ExecutionResult, StepRecord, ReplayRecord


class ExecutionMemory:
    def __init__(self) -> None:
        self._r = None
        if FEATURES["redis"]:
            try:
                import redis
                self._r = redis.from_url(REDIS_URL, decode_responses=True)
                self._r.ping()
                print("[redis] Memory store connected.")
            except Exception as e:
                print(f"[redis] Unavailable (non-fatal): {e}")
                self._r = None

    @property
    def online(self) -> bool:
        return self._r is not None

    def store(self, result: ExecutionResult, steps: list[StepRecord],
              confidence: float = 0.0) -> Optional[str]:
        if self._r is None:
            return None
        try:
            run_id = result.run_id or str(uuid.uuid4())[:8]
            record = ReplayRecord(
                run_id=run_id,
                routine_id=result.routine_id,
                started_at=result.started_at,
                ended_at=result.ended_at,
                steps=steps,
                variables=result.variables,
                confidence=confidence,
            )
            payload = _serialize(record)
            js = json.dumps(payload)
            self._r.lpush("ghost:executions", js)
            self._r.set(f"ghost:run:{run_id}", js)
            self._r.set(f"ghost:last:{result.routine_id}", js)
            for k, v in result.variables.items():
                self._r.set(f"ghost:var:{k}", v)
            return run_id
        except Exception as e:
            print(f"[redis] store failed (non-fatal): {e}")
            return None

    def recent(self, n: int = 20) -> list[dict]:
        if self._r is None:
            return []
        try:
            return [json.loads(r) for r in self._r.lrange("ghost:executions", 0, n - 1)]
        except Exception:
            return []

    def get_run(self, run_id: str) -> Optional[dict]:
        if self._r is None:
            return None
        try:
            raw = self._r.get(f"ghost:run:{run_id}")
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def last_value(self, var_name: str) -> Optional[str]:
        if self._r is None:
            return None
        try:
            return self._r.get(f"ghost:var:{var_name}")
        except Exception:
            return None


def _serialize(r: ReplayRecord) -> dict:
    return {
        "run_id":        r.run_id,
        "routine_id":    r.routine_id,
        "started_at":    r.started_at,
        "ended_at":      r.ended_at,
        "steps": [
            {
                "index":           s.index,
                "action":          s.action,
                "target":          s.target,
                "status":          s.status,
                "started_at":      s.started_at,
                "duration_ms":     s.duration_ms,
                "error":           s.error,
                "monitor_verdict": s.monitor_verdict,
            }
            for s in r.steps
        ],
        "variables":       r.variables,
        "confidence":      r.confidence,
        "sentry_event_id": r.sentry_event_id,
    }
