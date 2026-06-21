"""
Tests for the independent ROUTINE / WORKFLOW routing-source toggles
(MATCH_WORKFLOWS / MATCH_ROUTINES). A disabled source must be ignored entirely:
its vector candidates, offline/pattern matches, and keyword fallbacks are all
skipped, so the router never dispatches to it.

Network-free: the router degrades gracefully with no Redis, and the matching
helpers are stubbed on the instance.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import Intent
from router.router import ShepherdIntentRouter


def _boom(*a, **k):
    raise AssertionError("disabled source was consulted")


def _intent(text="do something"):
    return Intent(raw_text=text, timestamp=0.0)


def test_workflows_disabled_skips_workflow_source():
    r = ShepherdIntentRouter(match_workflows=False, match_routines=True)
    # Any consult of the workflow source must blow up.
    r._vector.workflow_candidates = _boom
    r._match_workflow_offline = _boom
    # Routine source returns nothing -> overall GENERIC.
    r._vector.candidates = lambda text: []
    r._resolve_keyword = lambda intent: None

    plan = r.resolve_plan(_intent())
    assert plan.kind == "GENERIC"


def test_routines_disabled_skips_routine_source():
    r = ShepherdIntentRouter(match_workflows=True, match_routines=False)
    r._vector.candidates = _boom
    r._resolve_keyword = _boom
    # Workflow source returns nothing -> overall GENERIC.
    r._vector.workflow_candidates = lambda text: []
    r._match_workflow_offline = lambda text: None

    plan = r.resolve_plan(_intent())
    assert plan.kind == "GENERIC"


def test_both_disabled_always_generic_even_with_keyword_hit():
    r = ShepherdIntentRouter(match_workflows=False, match_routines=False)
    # Even if both sources WOULD match, neither may be consulted.
    r._vector.workflow_candidates = _boom
    r._vector.candidates = _boom
    r._match_workflow_offline = _boom
    r._resolve_keyword = _boom

    plan = r.resolve_plan(_intent("fill out the form"))
    assert plan.kind == "GENERIC"


def test_routine_keyword_fallback_works_when_enabled():
    r = ShepherdIntentRouter(match_workflows=True, match_routines=True)
    # No vector candidates / no workflows -> reach the keyword fallback, which
    # we stub to return a routine resolution.
    from shepherd_types import ResolvedRoutine
    r._vector.workflow_candidates = lambda text: []
    r._vector.candidates = lambda text: []
    r._match_workflow_offline = lambda text: None
    r._resolve_keyword = lambda intent: ResolvedRoutine(
        routine_id="ROUTINE_FORM_FILL", variables={}, confidence=0.9,
        matched_keywords=["form"],
    )
    plan = r.resolve_plan(_intent("fill out the form"))
    assert plan.kind == "ROUTINE"
    assert plan.target == "ROUTINE_FORM_FILL"


def test_config_defaults_workflows_on_routines_off():
    import config
    # Deployment default: route to crystallized workflows only.
    assert config.MATCH_WORKFLOWS is True
    assert config.MATCH_ROUTINES is False
    r = ShepherdIntentRouter()  # picks up config defaults
    assert r._match_workflows is True
    assert r._match_routines is False


# ── prompt-based similar-workflow search ─────────────────────────────────────
def _router_with_workflow(tmp_path, match_workflows=True):
    """Router whose store holds one general workflow, vector search dead."""
    from unittest.mock import MagicMock
    from shepherd_types import Workflow, TaskGraphNode
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "wf.json"))
    store.save(Workflow(
        id="WF_SEND_AN_EMAIL", name="send an email",
        description="send an email", intent_patterns=["send an email"],
        params=[], nodes=[TaskGraphNode(key="open::::Open", kind="open", label="Open")],
        edges=[], version=1, from_graph="", start_key="open::::Open",
    ))
    r = ShepherdIntentRouter(match_workflows=match_workflows, match_routines=False)
    r._workflows = store
    r._vector = MagicMock()
    r._vector.workflow_candidates.return_value = []
    r._vector.candidates.return_value = []
    r._resolve_keyword = lambda intent: None
    return r


def test_similar_workflow_matched_by_prompt_offline(tmp_path):
    """No vector / no literal pattern hit → prompt compares to all workflows."""
    from router import llm_filter
    r = _router_with_workflow(tmp_path)
    # The phrased intent doesn't substring-match the workflow; the prompt does.
    import unittest.mock as mock
    with mock.patch.object(llm_filter, "select", return_value="WF_SEND_AN_EMAIL"):
        plan = r.resolve_plan(_intent("email my boss about the Q3 numbers"))
    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_SEND_AN_EMAIL"
    assert plan.source == "llm_similar"


def test_similar_workflow_none_goes_generic(tmp_path):
    from router import llm_filter
    import unittest.mock as mock
    r = _router_with_workflow(tmp_path)
    with mock.patch.object(llm_filter, "select", return_value=None):
        plan = r.resolve_plan(_intent("totally unrelated request"))
    assert plan.kind == "GENERIC"


def test_similar_workflow_skipped_when_workflows_disabled(tmp_path):
    from router import llm_filter
    import unittest.mock as mock
    r = _router_with_workflow(tmp_path, match_workflows=False)
    # select must never be consulted when the workflow source is disabled.
    with mock.patch.object(llm_filter, "select", side_effect=AssertionError("consulted")):
        plan = r.resolve_plan(_intent("email my boss"))
    assert plan.kind == "GENERIC"


def test_similar_workflow_llm_error_goes_generic(tmp_path):
    from router import llm_filter
    import unittest.mock as mock
    r = _router_with_workflow(tmp_path)
    with mock.patch.object(llm_filter, "select", return_value=llm_filter.LLM_ERROR):
        plan = r.resolve_plan(_intent("email my boss"))
    assert plan.kind == "GENERIC"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
