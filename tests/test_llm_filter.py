"""
Tests for the retrieve→LLM-filter routing pipeline (issue #39).

Covers BOTH directions with a mocked LLM so tests are deterministic in CI:
  (a) FALSE POSITIVE: research intent + job-application candidate → select()
      returns NONE → resolve_plan = GENERIC.
  (b) FALSE NEGATIVE: re-dispatch intent + Wikipedia workflow candidate →
      select() returns that workflow id → kind=WORKFLOW.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from shepherd_types import Intent, Workflow, TaskGraphNode
from router import llm_filter
from router.llm_filter import LLM_ERROR
from router.vector_router import HIGH_CONFIDENCE_THRESHOLD


# ── Helpers ──────────────────────────────────────────────────────────────────────

RESEARCH_INTENT = "Find the creators of C++, Python, and Java by going to their Wikipedia pages"
REDISPATCH_INTENT = "Find the creators of C++, Python, and Java via Wikipedia"

JOB_APP_CANDIDATE = {
    "id": "ROUTINE_JOB_APPLICATION",
    "name": "ROUTINE_JOB_APPLICATION",
    "description": "Apply to a role, researching the applicant's GitHub on the live web to fill the projects field",
}

WIKI_WORKFLOW_CANDIDATE = {
    "id": "WF_WIKIPEDIA_CREATORS",
    "name": "Wikipedia creators research",
    "description": "Find the creators of C++, Python, and Java by going to their Wikipedia pages",
}


# ── Unit tests: llm_filter.select() ─────────────────────────────────────────────

def test_select_returns_none_for_false_positive():
    """Research intent + job-application candidate → NONE (reject false positive)."""
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="NONE"):
        result = llm_filter.select(RESEARCH_INTENT, [JOB_APP_CANDIDATE])
    assert result is None


def test_select_returns_id_for_true_match():
    """Re-dispatch intent + matching workflow candidate → returns the id."""
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="WF_WIKIPEDIA_CREATORS"):
        result = llm_filter.select(REDISPATCH_INTENT, [WIKI_WORKFLOW_CANDIDATE])
    assert result == "WF_WIKIPEDIA_CREATORS"


def test_select_returns_error_when_llm_unavailable():
    """Graceful degradation: LLM unavailable → LLM_ERROR sentinel."""
    with patch.object(llm_filter.llm, "available", return_value=False):
        result = llm_filter.select(RESEARCH_INTENT, [JOB_APP_CANDIDATE])
    assert result == LLM_ERROR


def test_select_returns_none_on_empty_candidates():
    """No candidates → None without calling LLM."""
    result = llm_filter.select(RESEARCH_INTENT, [])
    assert result is None


def test_select_handles_llm_exception():
    """LLM raises → LLM_ERROR sentinel (allows caller to degrade)."""
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", side_effect=Exception("timeout")):
        result = llm_filter.select(RESEARCH_INTENT, [JOB_APP_CANDIDATE])
    assert result == LLM_ERROR


# ── Integration tests: resolve_plan() end-to-end ─────────────────────────────────

def _build_wiki_workflow():
    """A workflow that matches the Wikipedia creators research intent."""
    return Workflow(
        id="WF_WIKIPEDIA_CREATORS",
        name="Wikipedia creators research",
        description="Find the creators of C++, Python, and Java by going to their Wikipedia pages",
        intent_patterns=["find the creators", "wikipedia pages"],
        params=[],
        nodes=[TaskGraphNode(key="open::::Open", kind="open", label="Open Wikipedia")],
        edges=[],
        version=1,
        from_graph="",
        start_key="open::::Open",
    )


def test_resolve_plan_false_positive_goes_generic(tmp_path):
    """FALSE POSITIVE: research intent + job-app routine candidate at 0.79 →
    LLM says NONE → GENERIC (not ROUTINE)."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    router = ShepherdIntentRouter()
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))

    # Mock vector router to return job-app as a candidate at 0.79
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = []
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="NONE"):
        plan = router.resolve_plan(Intent(raw_text=RESEARCH_INTENT, timestamp=0.0))

    assert plan.kind == "GENERIC", f"Expected GENERIC but got {plan.kind} (target={plan.target})"
    assert plan.source == "llm_filter"


def test_resolve_plan_false_negative_routes_workflow(tmp_path):
    """FALSE NEGATIVE: re-dispatch intent + baked Wikipedia workflow candidate →
    LLM says WF_WIKIPEDIA_CREATORS → kind=WORKFLOW."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store

    # Mock vector router to return the workflow candidate at 0.85 (below high-confidence)
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = [("WF_WIKIPEDIA_CREATORS", 0.85)]
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.72)]

    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="WF_WIKIPEDIA_CREATORS"):
        plan = router.resolve_plan(Intent(raw_text=REDISPATCH_INTENT, timestamp=0.0))

    assert plan.kind == "WORKFLOW", f"Expected WORKFLOW but got {plan.kind}"
    assert plan.target == "WF_WIKIPEDIA_CREATORS"
    assert plan.source == "llm_filter"


def test_resolve_plan_high_confidence_skips_llm(tmp_path):
    """High-confidence (>=0.90) skips LLM filter entirely."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store

    # Mock vector router: workflow candidate at 0.92 (above high-confidence)
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = [("WF_WIKIPEDIA_CREATORS", 0.92)]
    router._vector.candidates.return_value = []

    # LLM should NOT be called — patch it to raise if called
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", side_effect=AssertionError("LLM should not be called")):
        plan = router.resolve_plan(Intent(raw_text=REDISPATCH_INTENT, timestamp=0.0))

    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_WIKIPEDIA_CREATORS"
    assert plan.confidence >= HIGH_CONFIDENCE_THRESHOLD
    assert plan.source == "vector"


def test_resolve_plan_llm_unavailable_degrades_to_threshold(tmp_path):
    """LLM unavailable + top candidate above SIMILARITY_THRESHOLD → routes (degradation)."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    router = ShepherdIntentRouter()
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))

    # Mock vector router: routine candidate at 0.79 (above 0.40 threshold)
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = []
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    # LLM unavailable: select() returns LLM_ERROR sentinel
    with patch.object(llm_filter.llm, "available", return_value=False):
        plan = router.resolve_plan(Intent(raw_text=RESEARCH_INTENT, timestamp=0.0))

    # Without LLM, falls back to conservative top-1 threshold (0.40)
    assert plan.kind == "ROUTINE"
    assert plan.target == "ROUTINE_JOB_APPLICATION"


def test_resolve_plan_transient_llm_failure_degrades_to_threshold(tmp_path):
    """Transient LLM failure (configured but call throws) → degrades to threshold."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    router = ShepherdIntentRouter()
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))

    # Mock vector router: routine candidate at 0.79
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = []
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    # LLM is configured (available=True) but call raises (transient failure)
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", side_effect=Exception("rate limited")):
        plan = router.resolve_plan(Intent(raw_text=RESEARCH_INTENT, timestamp=0.0))

    # Should degrade to threshold-based routing, NOT go GENERIC
    assert plan.kind == "ROUTINE"
    assert plan.target == "ROUTINE_JOB_APPLICATION"


def test_resolve_plan_offline_fallback_still_works(tmp_path):
    """When vector search is completely unavailable, offline pattern fallback works."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store

    # Mock vector router: completely unavailable (returns empty)
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = []
    router._vector.candidates.return_value = []

    plan = router.resolve_plan(Intent(
        raw_text="find the creators on wikipedia pages", timestamp=0.0,
    ))
    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_WIKIPEDIA_CREATORS"
    assert plan.source == "pattern"
