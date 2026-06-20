"""
RoutineEvolution — tracks per-node outcome statistics and evolves the
routine's risk profile over repeated executions.

Each step in each routine accumulates success / failure / halt / deviation
counts in Redis. The confidence score drives the dashboard's visual risk
indicators (confidence bars, ⚠ risky badges) and can promote previously
non-monitored nodes into the dynamic high-stakes set.

Redis keys: ghost:node:{routine_id}:{step_index}  →  NodeStats JSON
"""
import dataclasses
import json
from shepherd_types import NodeStats, StepRecord


class RoutineEvolution:
    def __init__(self) -> None:
        self._r = None
        try:
            from config import FEATURES, REDIS_URL
            if FEATURES.get("redis"):
                import redis as _redis
                self._r = _redis.from_url(REDIS_URL, decode_responses=True)
                self._r.ping()
        except Exception as e:
            print(f"[evolution] Redis unavailable — stats will not persist: {e}")

    # ── internal helpers ──────────────────────────────────────────────────────

    def _key(self, routine_id: str, step_index: int) -> str:
        return f"ghost:node:{routine_id}:{step_index}"

    def get_stats(self, routine_id: str, step_index: int) -> NodeStats:
        if self._r:
            try:
                raw = self._r.get(self._key(routine_id, step_index))
                if raw:
                    return NodeStats(**json.loads(raw))
            except Exception:
                pass
        return NodeStats(routine_id=routine_id, step_index=step_index)

    def _save(self, stats: NodeStats) -> None:
        if self._r:
            try:
                self._r.set(
                    self._key(stats.routine_id, stats.step_index),
                    json.dumps(dataclasses.asdict(stats)),
                )
            except Exception:
                pass

    # ── write path (called by engine) ────────────────────────────────────────

    def record_step(self, routine_id: str, step: StepRecord) -> None:
        """Update stats after a step finishes. Called from the engine."""
        stats = self.get_stats(routine_id, step.index)
        stats.execution_count += 1
        stats.total_duration_ms += step.duration_ms
        if step.status == "completed":
            stats.success_count += 1
        elif step.status == "failed":
            stats.failure_count += 1
        elif step.status == "halted":
            stats.halt_count += 1
        if step.deviation:
            stats.deviation_count += 1
        self._save(stats)

    def record_approval(self, routine_id: str, step_index: int) -> None:
        """Increment approval count when a human approves a flagged step."""
        stats = self.get_stats(routine_id, step_index)
        stats.approval_count += 1
        self._save(stats)

    # ── read path (called by server API) ─────────────────────────────────────

    def all_stats(self, routine_id: str, total_steps: int) -> list[dict]:
        """Return computed stats for every step in the routine."""
        out = []
        for i in range(total_steps):
            s = self.get_stats(routine_id, i)
            total = s.success_count + s.failure_count + s.halt_count
            confidence = s.success_count / total if total > 0 else 1.0
            avg_ms = s.total_duration_ms // s.execution_count if s.execution_count > 0 else 0
            out.append({
                "step_index":     s.step_index,
                "success_count":  s.success_count,
                "failure_count":  s.failure_count,
                "halt_count":     s.halt_count,
                "deviation_count": s.deviation_count,
                "approval_count": s.approval_count,
                "confidence":     round(confidence, 3),
                "avg_duration_ms": avg_ms,
                "execution_count": s.execution_count,
                "is_risky":       s.execution_count >= 2 and confidence < 0.6,
            })
        return out

    def risky_steps(self, routine_id: str, total_steps: int) -> set[int]:
        """Step indices that have become statistically risky (confidence < 60%)."""
        return {
            s["step_index"]
            for s in self.all_stats(routine_id, total_steps)
            if s["is_risky"]
        }
