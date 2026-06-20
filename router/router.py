"""
ShepherdIntentRouter — deterministic keyword/intent matching.
Routine SELECTION is always deterministic; never uses ML or vector search.
Execution mode (LIVE vs LOCKED) is set separately in the engine.
"""
import re
from shepherd_types import Intent, ResolvedRoutine
from router.registry import REGISTRY, CONFIDENCE_THRESHOLD


class ShepherdIntentRouter:
    def __init__(self) -> None:
        self._registry = REGISTRY

    def resolve(self, intent: Intent) -> ResolvedRoutine | None:
        text = intent.raw_text.lower().strip()
        best_id: str | None = None
        best_score = 0.0
        best_keywords: list[str] = []

        for routine_id, spec in self._registry.items():
            matched = [kw for kw in spec["keywords"] if kw in text]
            if not matched:
                continue
            # Rank by match count; ratio penalizes routines with long keyword lists.
            score = len(matched)
            if score > best_score:
                best_score = score
                best_id = routine_id
                best_keywords = matched

        if best_id is None:
            return None

        spec = self._registry[best_id]
        confidence = len(best_keywords) / len(spec["keywords"])
        if len(best_keywords) < 2 and confidence < CONFIDENCE_THRESHOLD:
            # Allow a lone keyword when no other routine matched at all (e.g. "demo", "search")
            if any(
                kw in text
                for rid, s in self._registry.items()
                if rid != best_id
                for kw in s["keywords"]
            ):
                return None
        variables: dict[str, str] = dict(spec.get("variable_defaults", {}))

        for var_name, pattern in spec.get("variable_patterns", {}).items():
            m = re.search(pattern, intent.raw_text, re.IGNORECASE)
            if m:
                variables[var_name] = m.group(1).strip()

        return ResolvedRoutine(
            routine_id=best_id,
            variables=variables,
            confidence=round(confidence, 3),
            matched_keywords=best_keywords,
        )
