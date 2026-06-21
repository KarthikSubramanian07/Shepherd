"""
Durable run-trace journal.

The cheap, synchronous write at the run boundary that makes async crystallization
robust: every run's RunTrace is persisted as JSON under data/run_traces/ before the
coalescer touches it. This lets a failed/slow coalesce be retried, lets a graph be
re-crystallized later with a better model, and decouples execution from the LLM.

Self-contained: executed steps are stored inline (full fidelity) so re-coalescing
never depends on the live routines.json.
"""
import json
import os
from dataclasses import asdict

from shepherd_types import RunTrace, InterventionEvent
from engine.routines import _build_step

_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "run_traces")


def _path(run_id: str) -> str:
    return os.path.join(_DIR, f"{run_id}.json")


def write(trace: RunTrace) -> None:
    os.makedirs(_DIR, exist_ok=True)
    data = {
        "run_id":        trace.run_id,
        "routine_id":    trace.routine_id,
        "variables":     trace.variables,
        "status":        trace.status,
        "started_at":    trace.started_at,
        "ended_at":      trace.ended_at,
        "executed":      [asdict(s) for s in trace.executed],
        "interventions": [asdict(i) for i in trace.interventions],
        "deviations":    trace.deviations,
    }
    tmp = _path(trace.run_id) + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, _path(trace.run_id))  # atomic — never a half-written trace


def read(run_id: str) -> RunTrace:
    with open(_path(run_id)) as f:
        return _deserialize(json.load(f))


def _deserialize(raw: dict) -> RunTrace:
    return RunTrace(
        run_id=raw["run_id"],
        routine_id=raw["routine_id"],
        variables=raw.get("variables", {}),
        status=raw.get("status", "completed"),
        started_at=raw.get("started_at", 0.0),
        ended_at=raw.get("ended_at", 0.0),
        executed=[_build_step(s) for s in raw.get("executed", [])],
        interventions=[InterventionEvent(**i) for i in raw.get("interventions", [])],
        deviations=raw.get("deviations", []),
    )


def list_run_ids() -> list[str]:
    if not os.path.isdir(_DIR):
        return []
    return sorted(f[:-5] for f in os.listdir(_DIR) if f.endswith(".json"))
