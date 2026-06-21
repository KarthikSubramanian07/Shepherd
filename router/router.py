"""
ShepherdIntentRouter — semantic vector search + keyword fallback.

Primary:  VectorRouter embeds the intent and finds the nearest routine via
          Redis vector search (HNSW cosine similarity, local BAAI/bge model).
Fallback: Keyword matching when Redis / fastembed are unavailable.
Execution mode (LIVE vs LOCKED) is set separately in the engine.
"""

import re

from shepherd_types import Intent, ResolvedRoutine, Plan
from router.registry import REGISTRY, CONFIDENCE_THRESHOLD
from router.vector_router import VectorRouter, SIMILARITY_THRESHOLD
from router import llm_filter
from engine.workflow_store import WorkflowStore
from config import MATCH_WORKFLOWS, MATCH_ROUTINES


class ShepherdIntentRouter:
    def __init__(
        self,
        match_workflows: bool | None = None,
        match_routines: bool | None = None,
    ) -> None:
        self._registry = REGISTRY
        self._vector = VectorRouter(REGISTRY)
        self._workflows = WorkflowStore()
        # Independent source toggles (default from config). Disabling one makes
        # the router ignore that source entirely — its candidates, offline/pattern
        # matches, and keyword fallbacks are all skipped.
        self._match_workflows = MATCH_WORKFLOWS if match_workflows is None else match_workflows
        self._match_routines = MATCH_ROUTINES if match_routines is None else match_routines
        if not (self._match_workflows and self._match_routines):
            print(f"[router] sources: workflows={self._match_workflows} routines={self._match_routines}")
        # Index saved workflows into the same vector search so dispatch can
        # prefer a crystallized workflow over a recorded routine.
        try:
            self._vector.index_workflows(self._workflows.list())
        except Exception as e:
            print(f"[router] workflow indexing skipped (non-fatal): {e}")

    # ── dispatch: prefer a saved WORKFLOW, else a ROUTINE, else GENERIC ────────
    def resolve_plan(self, intent: Intent) -> Plan:
        """Return how to satisfy the intent.

        Pipeline (when vector search is available):
          1. Gather top-K candidates from BOTH workflow and routine vector sets.
          2. Call LLM filter (select) on ALL candidates — no score-based bypass.
          3. If LLM picks a candidate -> route to it; if NONE -> GENERIC.
          4. Graceful degradation: LLM unavailable/errored -> conservative top-1
             threshold (0.40).

        Offline fallback (Redis down): intent_pattern substring matching.
        """
        text = intent.raw_text

        # ── Gather candidates from both vector sets (each source toggleable) ─
        wf_candidates = self._vector.workflow_candidates(text) if self._match_workflows else []
        rt_candidates = self._vector.candidates(text) if self._match_routines else []

        # When vector search returned anything, also let an explicit intent_pattern
        # match compete. A pattern hit is a deliberate, high-precision trigger, so
        # it ranks above a fuzzy similarity, and the specifically-matched workflow
        # may not be vector-indexed (or Redis may surface unrelated workflows). We
        # merge it into the pool (deduped, top score) rather than depending on the
        # index. The fully-offline case (no vector candidates at all) falls through
        # to the dedicated pattern fallback below, which preserves source="pattern".
        if wf_candidates or rt_candidates:
            off = self._match_workflow_offline(text) if self._match_workflows else None
            if off is not None:
                wf_off = off[0]
                wf_candidates = [
                    (cid, s) for (cid, s) in wf_candidates if cid != wf_off.id
                ]
                wf_candidates.insert(0, (wf_off.id, 0.99))
            plan = self._route_with_candidates(text, wf_candidates, rt_candidates)
            # Vector recall can miss a similar workflow (it never reached the
            # shortlist). Before giving up, compare the intent against the FULL
            # workflow set with a prompt — but skip workflows the filter already
            # judged in the shortlist (no point re-asking the same set).
            if plan.kind == "GENERIC":
                already = [cid for cid, _ in wf_candidates]
                similar = self._similar_workflow_plan(text, exclude_ids=already)
                if similar is not None:
                    return similar
            return plan

        # ── Offline fallback: substring match on workflow intent_patterns ───
        wf = self._match_workflow_offline(text) if self._match_workflows else None
        if wf is not None:
            workflow, confidence, source, matched = wf
            return Plan(
                kind="WORKFLOW",
                target=workflow.id,
                params=self._extract_workflow_params(workflow, text),
                confidence=confidence,
                matched=matched,
                source=source,
            )

        # ── Prompt similarity: no vector search (Redis down) and no literal
        # pattern hit, so compare the intent to every workflow by MEANING. This
        # is what lets a phrased intent ("email my boss about Q3") match a
        # generalized workflow ("send an email") offline. ───────────────────
        similar = self._similar_workflow_plan(text)
        if similar is not None:
            return similar

        # ── Keyword fallback for routines ───────────────────────────────────
        resolved = self._resolve_keyword(intent) if self._match_routines else None
        if resolved is not None:
            return Plan(
                kind="ROUTINE",
                target=resolved.routine_id,
                params=resolved.variables,
                confidence=resolved.confidence,
                matched=resolved.matched_keywords,
                source="keyword",
            )

        return Plan(
            kind="GENERIC", target="", params={}, confidence=0.0, source="fallback"
        )

    def _route_with_candidates(
        self,
        text: str,
        wf_candidates: list[tuple[str, float]],
        rt_candidates: list[tuple[str, float]],
    ) -> Plan:
        """Route using the retrieve→filter pipeline."""
        workflows = self._workflows.list()
        by_id = {w.id: w for w in workflows}

        # Merge: build a unified candidate list, workflows preferred on ties
        all_candidates: list[tuple[str, float, str]] = []  # (id, score, kind)
        for cid, score in wf_candidates:
            if cid in by_id:
                all_candidates.append((cid, score, "WORKFLOW"))
        for cid, score in rt_candidates:
            if cid in self._registry:
                all_candidates.append((cid, score, "ROUTINE"))

        if not all_candidates:
            return Plan(
                kind="GENERIC", target="", params={}, confidence=0.0, source="fallback"
            )

        # Sort descending by score; workflows win ties
        all_candidates.sort(key=lambda x: (x[1], x[2] == "WORKFLOW"), reverse=True)
        top_id, top_score, top_kind = all_candidates[0]

        # LLM filter is the authoritative precision gate — always runs on candidates
        candidate_infos = self._build_candidate_infos(all_candidates, by_id)

        chosen_id = llm_filter.select(text, candidate_infos)

        if chosen_id == llm_filter.LLM_ERROR:
            # LLM was unavailable or call failed — degrade to conservative threshold
            if top_score >= SIMILARITY_THRESHOLD:
                print(
                    f"[router] LLM unavailable, degraded top-1: {top_id} (score={top_score:.3f})"
                )
                return self._plan_for(
                    top_id, top_kind, top_score, text, source="vector"
                )
            return Plan(
                kind="GENERIC", target="", params={}, confidence=0.0, source="fallback"
            )

        if chosen_id is None:
            # LLM explicitly said NONE — no candidate matches the intent
            return Plan(
                kind="GENERIC",
                target="",
                params={},
                confidence=0.0,
                source="llm_filter",
            )

        # Find the chosen candidate's kind
        for cid, score, kind in all_candidates:
            if cid == chosen_id:
                print(f"[router] LLM filter chose: {chosen_id} (score={score:.3f})")
                return self._plan_for(chosen_id, kind, score, text, source="llm_filter")

        return Plan(
            kind="GENERIC", target="", params={}, confidence=0.0, source="llm_filter"
        )

    def _build_candidate_infos(
        self,
        all_candidates: list[tuple[str, float, str]],
        wf_by_id: dict,
    ) -> list[dict]:
        """Build the candidate info dicts for the LLM filter prompt."""
        infos: list[dict] = []
        for cid, _score, kind in all_candidates:
            if kind == "WORKFLOW":
                wf = wf_by_id[cid]
                infos.append(
                    {
                        "id": cid,
                        "name": wf.name,
                        "description": wf.description or wf.name,
                    }
                )
            else:
                spec = self._registry[cid]
                infos.append(
                    {
                        "id": cid,
                        "name": cid,
                        "description": spec.get("description", cid),
                    }
                )
        return infos

    def _plan_for(
        self, target_id: str, kind: str, confidence: float, text: str, source: str
    ) -> Plan:
        """Build a Plan for the given target."""
        if kind == "WORKFLOW":
            workflows = self._workflows.list()
            by_id = {w.id: w for w in workflows}
            wf = by_id[target_id]
            low = text.lower()
            matched = [p for p in wf.intent_patterns if p.lower() in low]
            return Plan(
                kind="WORKFLOW",
                target=wf.id,
                params=self._extract_workflow_params(wf, text),
                confidence=confidence,
                matched=matched,
                source=source,
            )
        else:
            spec = self._registry[target_id]
            variables = self._extract_variables(spec, text)
            return Plan(
                kind="ROUTINE",
                target=target_id,
                params=variables,
                confidence=confidence,
                matched=[],
                source=source,
            )

    def _match_workflow_offline(self, text: str):
        """Offline fallback: substring match on each workflow's intent_patterns.
        Returns (workflow, confidence, source, matched) or None."""
        workflows = self._workflows.list()
        if not workflows:
            return None

        low = text.lower().strip()
        best = None
        for wf in workflows:
            matched = [p for p in wf.intent_patterns if p.lower() in low]
            if matched and (best is None or len(matched) > len(best[3])):
                conf = round(len(matched) / max(1, len(wf.intent_patterns)), 3)
                best = (wf, conf, "pattern", matched)
        return best

    # Cap on how many workflows go into one comparison prompt (keeps the prompt
    # bounded). If exceeded, the overflow is logged rather than silently dropped.
    _SIMILAR_MAX_WORKFLOWS = 50

    def _similar_workflow_plan(self, text: str, exclude_ids: list[str] | None = None):
        """Prompt-compare the intent against EVERY saved workflow to find a
        similar one — the search step that catches generalized workflows a
        literal/vector match misses. Returns a WORKFLOW Plan or None.

        Reuses the router's LLM precision filter (llm_filter.select): it is
        handed the full workflow list as candidates and returns the id that
        satisfies the same goal, or NONE. `exclude_ids` are workflows the filter
        already judged this turn (the vector shortlist) — if EVERY workflow was
        already judged, there is nothing new to compare and we skip the call.
        Degrades to None when workflows are disabled, none exist, or the LLM is
        unavailable."""
        if not self._match_workflows:
            return None
        workflows = self._workflows.list()
        if not workflows:
            return None
        excluded = set(exclude_ids or ())
        if excluded and all(w.id in excluded for w in workflows):
            return None  # the shortlist already covered every workflow
        if len(workflows) > self._SIMILAR_MAX_WORKFLOWS:
            print(f"[router] similar-workflow prompt comparing first "
                  f"{self._SIMILAR_MAX_WORKFLOWS} of {len(workflows)} workflows")
            workflows = workflows[: self._SIMILAR_MAX_WORKFLOWS]

        infos = []
        for w in workflows:
            desc = w.description or w.name
            if w.intent_patterns:
                desc = f"{desc} (e.g. {'; '.join(w.intent_patterns[:3])})"
            infos.append({"id": w.id, "name": w.name, "description": desc})

        chosen = llm_filter.select(text, infos)
        if not chosen or chosen == llm_filter.LLM_ERROR:
            return None
        if any(w.id == chosen for w in workflows):
            print(f"[router] similar-workflow match via prompt: {chosen}")
            return self._plan_for(chosen, "WORKFLOW", 0.6, text, source="llm_similar")
        return None

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
        """Legacy single-result resolution. Used only when vector is unavailable."""
        return self._resolve_keyword(intent)

    def _resolve_keyword(self, intent: Intent) -> ResolvedRoutine | None:
        """Keyword fallback for routine matching (Redis down)."""
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
