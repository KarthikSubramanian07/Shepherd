"""Pure data loader — reads routines.json into RoutineDefinition objects. No control logic."""
import json
import os
from shepherd_types import BatchField, RoutineDefinition, RoutineStep, RecordedStep

_ROUTINES_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "routines.json")
_cache: dict[str, RoutineDefinition] | None = None


def load_routines(path: str = _ROUTINES_PATH) -> dict[str, RoutineDefinition]:
    global _cache
    if _cache is not None:
        return _cache

    with open(path) as f:
        raw = json.load(f)

    result: dict[str, RoutineDefinition] = {}
    for entry in raw:
        steps = []
        for s in entry["steps"]:
            raw_fields = s.pop("fields", None)
            step = RoutineStep(**s)
            if raw_fields:
                step.fields = [BatchField(**f) for f in raw_fields]
            steps.append(step)
        demonstration = None
        if entry.get("demonstration"):
            demonstration = [RecordedStep(**r) for r in entry["demonstration"]]
        step_instructions = None
        if entry.get("step_instructions"):
            step_instructions = {int(k): v for k, v in entry["step_instructions"].items()}
        result[entry["routine_id"]] = RoutineDefinition(
            routine_id=entry["routine_id"],
            description=entry["description"],
            variables=entry.get("variables", []),
            steps=steps,
            demonstration=demonstration,
            step_instructions=step_instructions,
            high_stakes_steps=entry.get("high_stakes_steps", []),
            mode=entry.get("mode", "LIVE"),
        )

    _cache = result
    return result


def get_routine(routine_id: str) -> RoutineDefinition:
    routines = load_routines()
    if routine_id not in routines:
        raise KeyError(f"Unknown routine_id: '{routine_id}'. Available: {list(routines)}")
    return routines[routine_id]
