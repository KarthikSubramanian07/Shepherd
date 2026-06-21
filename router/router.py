"""
ShepherdIntentRouter — semantic vector search + keyword fallback.

Primary:  VectorRouter embeds the intent and finds the nearest routine via
          Redis vector search (HNSW cosine similarity, local BAAI/bge model).
Fallback: Keyword matching when Redis / fastembed are unavailable.
Execution mode (LIVE vs LOCKED) is set separately in the engine.
"""
import re
from typing import Optional

from shepherd_types import Intent, ResolvedRoutine, Plan
from router.registry import REGISTRY, CONFIDENCE_THRESHOLD
from router.vector_router import VectorRouter
from engine.workflow_store import WorkflowStore


class ShepherdIntentRouter:
    def __init__(self) -> None:
        self._registry = REGISTRY
        self._vector = VectorRouter(REGISTRY)
        self._workflows = WorkflowStore()
        # Index saved workflows into the same vector search so dispatch can
        # prefer a crystallized workflow over a recorded routine.
        try:
            self._vector.index_workflows(self._workflows.list())
        except Exception as e:
            print(f"[router] workflow indexing skipped (non-fatal): {e}")

    # ── dispatch: prefer a saved WORKFLOW, else a ROUTINE, else GENERIC ────────
    def resolve_plan(self, intent: Intent) -> Plan:
        """Return how to satisfy the intent. A saved Workflow wins when it matches
        (via the vector search, or an intent_pattern keyword fallback when Redis
        is down); otherwise defer to the recorded-routine resolver; otherwise the
        free-form generic agent."""
        wf = self._match_workflow(intent.raw_text)
        if wf is not None:
            workflow, confidence, source, matched = wf
            return Plan(
                kind="WORKFLOW", target=workflow.id,
                params=self._extract_workflow_params(workflow, intent.raw_text),
                confidence=confidence, matched=matched, source=source,
            )

        resolved = self.resolve(intent)
        if resolved is not None:
            return Plan(
                kind="ROUTINE", target=resolved.routine_id,
                params=resolved.variables, confidence=resolved.confidence,
                matched=resolved.matched_keywords,
                source="vector" if not resolved.matched_keywords else "keyword",
            )

        return Plan(kind="GENERIC", target="", params={}, confidence=0.0, source="fallback")

    def _match_workflow(self, text: str):
        """(workflow, confidence, source, matched) or None."""
        workflows = self._workflows.list()
        if not workflows:
            return None
        by_id = {w.id: w for w in workflows}

        hit = self._vector.resolve_workflow(text)
        if hit and hit[0] in by_id:
            return by_id[hit[0]], hit[1], "vector", []

        # Offline fallback: substring match on each workflow's intent_patterns.
        low = text.lower().strip()
        best = None
        for wf in workflows:
            matched = [p for p in wf.intent_patterns if p.lower() in low]
            if matched and (best is None or len(matched) > len(best[3])):
                conf = round(len(matched) / max(1, len(wf.intent_patterns)), 3)
                best = (wf, conf, "pattern", matched)
        return best

    @staticmethod
    def _extract_workflow_params(workflow, raw_text: str) -> dict:
        # Pull any registry-style variables the matching routine knows how to
        # extract; workflows reuse the same NL → variable patterns when present.
        spec = REGISTRY.get(workflow.from_graph) or REGISTRY.get(workflow.id) or {}
        params: dict[str, str] = dict(spec.get("variable_defaults", {}))
        for var_name, pattern in spec.get("variable_patterns", {}).items():
            m = re.search(pattern, raw_text, re.IGNORECASE)
            if m:
                params[var_name] = m.group(1).strip()
        return params

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
