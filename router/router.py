"""
ShepherdIntentRouter — semantic vector search + keyword fallback.

Primary:  VectorRouter embeds the intent and finds the nearest routine via
          Redis vector search (HNSW cosine similarity, local BAAI/bge model).
Fallback: Keyword matching when Redis / fastembed are unavailable.
Execution mode (LIVE vs LOCKED) is set separately in the engine.
"""
import re
from shepherd_types import Intent, ResolvedRoutine
from router.registry import REGISTRY, CONFIDENCE_THRESHOLD
from router.vector_router import VectorRouter


class ShepherdIntentRouter:
    def __init__(self) -> None:
        self._registry = REGISTRY
        self._vector = VectorRouter(REGISTRY)

    def resolve(self, intent: Intent) -> ResolvedRoutine | None:
        # ── 1. Try semantic vector search first ───────────────────────────────
        if self._vector.available:
            result = self._vector.resolve(intent.raw_text)
            if result:
                routine_id, similarity = result
                spec = self._registry[routine_id]
                variables = self._extract_variables(spec, intent.raw_text)
                print(f"[vector_router] matched {routine_id} (similarity={similarity:.3f})")
                return ResolvedRoutine(
                    routine_id=routine_id,
                    variables=variables,
                    confidence=similarity,
                    matched_keywords=[],
                )

        # ── 2. Keyword fallback ───────────────────────────────────────────────
        text = intent.raw_text.lower().strip()
        best_id: str | None = None
        best_score = 0.0
        best_keywords: list[str] = []

        for routine_id, spec in self._registry.items():
            matched = [kw for kw in spec["keywords"] if kw in text]
            if not matched:
                continue
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
            if any(
                kw in text
                for rid, s in self._registry.items()
                if rid != best_id
                for kw in s["keywords"]
            ):
                return None

        variables = self._extract_variables(spec, intent.raw_text)
        return ResolvedRoutine(
            routine_id=best_id,
            variables=variables,
            confidence=round(confidence, 3),
            matched_keywords=best_keywords,
        )

    def _extract_variables(self, spec: dict, raw_text: str) -> dict[str, str]:
        variables: dict[str, str] = dict(spec.get("variable_defaults", {}))
        for var_name, pattern in spec.get("variable_patterns", {}).items():
            m = re.search(pattern, raw_text, re.IGNORECASE)
            if m:
                variables[var_name] = m.group(1).strip()
        return variables
