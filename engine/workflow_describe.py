"""
Async workflow description generator — fire-and-forget background task.

After a workflow is promoted (instantly dispatchable with NL-derived name),
this module calls the LLM to produce:
  - a clean human-readable title
  - a short human-readable description
  - 3–5 paraphrased intent_patterns for richer vector matching

When the LLM completes, the saved workflow is updated and the vector store
is re-indexed. If the LLM is unavailable or slow, the NL-derived values
from promotion are kept — the workflow remains fully functional.

NEVER called on the hot path — always fire-and-forget via threading.Thread.
"""
from __future__ import annotations

import threading
from typing import Optional


def generate_description(
    workflow_id: str,
    wf_store_path: str,
    *,
    on_complete: Optional[callable] = None,
) -> None:
    """Fire-and-forget: spawn a daemon thread that calls the LLM to enrich
    the workflow's title, description, and intent_patterns, then persists the
    update and re-indexes the vector store.

    `on_complete(workflow)` is called if provided (for testing hooks).
    """
    threading.Thread(
        target=_generate,
        args=(workflow_id, wf_store_path),
        kwargs={"on_complete": on_complete},
        daemon=True,
        name=f"wf-describe-{workflow_id}",
    ).start()


def _generate(
    workflow_id: str,
    wf_store_path: str,
    *,
    on_complete: Optional[callable] = None,
) -> None:
    try:
        from engine.workflow_store import WorkflowStore
        from engine import llm

        store = WorkflowStore(wf_store_path)
        wf = store.get(workflow_id)
        if wf is None:
            return

        if not llm.available():
            print(f"[workflow_describe] LLM unavailable — keeping NL-derived values for {workflow_id}")
            if on_complete:
                on_complete(wf)
            return

        node_labels = [n.label for n in wf.nodes]
        prompt = _build_prompt(wf.name, wf.intent_patterns, node_labels)

        raw = llm.complete(
            system=(
                "You generate concise metadata for workflow definitions. "
                "Return ONLY a JSON object with keys: title, description, intent_patterns. "
                "No markdown fences, no extra text."
            ),
            messages=[("user", prompt)],
            max_tokens=400,
            timeout=30.0,
        )

        parsed = llm.parse_json_object(raw)
        title = (parsed.get("title") or "").strip()
        description = (parsed.get("description") or "").strip()
        patterns = parsed.get("intent_patterns") or []

        # Re-load the workflow fresh right before saving so we only patch the
        # enriched fields onto the latest version — avoids overwriting concurrent
        # modifications (teaching loop, re-promotion) made during the LLM wait.
        fresh = store.get(workflow_id)
        if fresh is None:
            return
        if title:
            fresh.name = title[:80]
        if description:
            fresh.description = description[:300]
        if patterns and isinstance(patterns, list):
            filtered = [str(p).strip()[:120] for p in patterns[:7] if str(p).strip()]
            if filtered:
                fresh.intent_patterns = filtered

        store.save(fresh)
        wf = fresh
        print(f"[workflow_describe] Updated {workflow_id}: {wf.name!r}")

        _reindex_vector_store(wf)

        if on_complete:
            on_complete(wf)

    except Exception as e:
        print(f"[workflow_describe] Failed for {workflow_id} (non-fatal, NL values kept): {e}")
        if on_complete:
            try:
                from engine.workflow_store import WorkflowStore
                wf = WorkflowStore(wf_store_path).get(workflow_id)
                on_complete(wf)
            except Exception:
                pass


def _build_prompt(name: str, intent_patterns: list[str], node_labels: list[str]) -> str:
    """Build the LLM prompt for workflow metadata generation.

    Key balance: patterns must be GENERALIZED enough that semantically-similar
    future intents match (e.g. "find who created a programming language"), but
    SCOPED enough that unrelated intents don't false-match (never "do research"
    or "use a browser"). The milestone/node labels ground the scope — describe
    the KIND of task they represent, not the literal one-off values.
    """
    milestones = ", ".join(node_labels[:10]) if node_labels else "none"
    patterns = ", ".join(f'"{p}"' for p in intent_patterns[:5]) if intent_patterns else name
    return (
        f"A workflow was auto-created from a user's task.\n"
        f"Current name: \"{name}\"\n"
        f"Current intent patterns: {patterns}\n"
        f"Milestones (the steps the workflow performs): {milestones}\n\n"
        f"Generate metadata that GENERALIZES beyond the specific instance but stays "
        f"SCOPED to this kind of task:\n\n"
        f"1. `title`: a clean, human-readable title describing the CATEGORY of task, "
        f"not the specific values (max 60 chars). "
        f"E.g. for a run about C++/Python/Java creators → "
        f"\"Look up programming-language creators on Wikipedia\"\n"
        f"2. `description`: one-sentence summary of what this workflow does (max 200 chars)\n"
        f"3. `intent_patterns`: 3-5 short, varied natural-language phrases a user might "
        f"say to trigger this SAME KIND of task. "
        f"IMPORTANT: patterns should be grounded in the milestone labels — describe "
        f"the specific domain/action (e.g. \"find who created a programming language\", "
        f"\"get language designer from Wikipedia\"). "
        f"NEVER produce generic catch-all patterns like \"do research\", \"search the web\", "
        f"\"use a browser\", or \"look something up\" — these would false-match unrelated "
        f"intents. Each pattern must be specific enough that only tasks of this same kind "
        f"would match.\n\n"
        f"Return JSON: {{\"title\": \"...\", \"description\": \"...\", \"intent_patterns\": [\"...\", ...]}}"
    )


def _reindex_vector_store(wf) -> None:
    """Re-index a single workflow in the vector store after enrichment."""
    try:
        from router.vector_router import VectorRouter
        from engine.workflow_store import WorkflowStore

        # Use the module-level singleton if the router has been initialized.
        # Otherwise, build a lightweight indexer just for this workflow.
        import router.vector_router as vr

        if not hasattr(vr, '_singleton'):
            return

        router_instance = vr._singleton
        if router_instance is not None and router_instance.available:
            router_instance.index_single_workflow(wf)
    except Exception as e:
        print(f"[workflow_describe] vector re-index failed (non-fatal): {e}")
