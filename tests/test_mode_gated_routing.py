"""
Tests for mode-gated routing (issue #52).

Validates the mode→candidate-source gating in resolve_plan():
  1. AUTONOMOUS + no workflow → GENERIC (not a routine match).
  2. AUTONOMOUS + matching workflow → kind=WORKFLOW.
  3. LIVE + routine match → kind=ROUTINE; workflow beats routine when both match.
  4. LOCKED → keyword-only, no LLM filter, no autonomous fallback.
  5. Relay `mode` command with AUTONOMOUS → _runtime_mode set + mode.changed emitted.

All tests mock the LLM filter + stub the vector router; deterministic, no network.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch, MagicMock

from shepherd_types import Intent, Workflow, TaskGraphNode
from router import llm_filter


# ── Helpers ───────────────────────────────────────────────────────────────────

RESEARCH_INTENT = "Find the creators of C++, Python, and Java by going to their Wikipedia pages"


def _build_wiki_workflow():
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


# ── Test 1: AUTONOMOUS + no workflow indexed → GENERIC ────────────────────────

def test_autonomous_no_workflow_returns_generic(tmp_path):
    """AUTONOMOUS mode with no indexed workflow → kind=GENERIC (not ROUTINE)."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    router = ShepherdIntentRouter()
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))

    # Mock vector router: no workflow candidates, routine candidate available
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = []
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    # In AUTONOMOUS mode, routine candidates are NOT generated, so LLM filter
    # should never be called — but mock it anyway for safety.
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="ROUTINE_JOB_APPLICATION"):
        plan = router.resolve_plan(
            Intent(raw_text=RESEARCH_INTENT, timestamp=0.0), mode="AUTONOMOUS"
        )

    # No routine candidates in AUTONOMOUS → GENERIC fallback
    assert plan.kind == "GENERIC"
    assert plan.source == "fallback"
    # Verify routine vector was NOT called
    router._vector.candidates.assert_not_called()


# ── Test 2: AUTONOMOUS + matching workflow → kind=WORKFLOW ─────────────────────

def test_autonomous_with_workflow_returns_workflow(tmp_path):
    """AUTONOMOUS mode with a matching indexed workflow → kind=WORKFLOW."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store

    # Mock vector router: workflow candidate present at high score
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = [("WF_WIKIPEDIA_CREATORS", 0.88)]
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    # LLM filter selects the workflow
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="WF_WIKIPEDIA_CREATORS"):
        plan = router.resolve_plan(
            Intent(raw_text=RESEARCH_INTENT, timestamp=0.0), mode="AUTONOMOUS"
        )

    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_WIKIPEDIA_CREATORS"
    # Routine vector should NOT have been called in AUTONOMOUS
    router._vector.candidates.assert_not_called()


# ── Test 3: LIVE + routine match; workflow beats routine ───────────────────────

def test_live_routine_match(tmp_path):
    """LIVE mode: intent matching a curated routine → kind=ROUTINE."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    # Enable routines explicitly — this validates MODE gating independent of the
    # deployment default (config ships with routines off).
    router = ShepherdIntentRouter(match_routines=True)
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))

    # Mock vector router: only routine candidate
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = []
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.82)]

    # LLM filter picks the routine
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="ROUTINE_JOB_APPLICATION"):
        plan = router.resolve_plan(
            Intent(raw_text="apply to this job posting", timestamp=0.0), mode="LIVE"
        )

    assert plan.kind == "ROUTINE"
    assert plan.target == "ROUTINE_JOB_APPLICATION"


def test_live_workflow_beats_routine(tmp_path):
    """LIVE mode: when both workflow and routine match, workflow wins."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store

    # Mock vector router: both workflow and routine are candidates
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = [("WF_WIKIPEDIA_CREATORS", 0.90)]
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    # LLM filter picks the workflow (higher relevance)
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="WF_WIKIPEDIA_CREATORS"):
        plan = router.resolve_plan(
            Intent(raw_text=RESEARCH_INTENT, timestamp=0.0), mode="LIVE"
        )

    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_WIKIPEDIA_CREATORS"


# ── Test 4: LOCKED → keyword-only, no LLM filter, no autonomous fallback ──────

def test_locked_keyword_only_no_llm(tmp_path):
    """LOCKED mode: uses keyword resolver only, no vector, no LLM filter."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store

    # Mock vector router — should NOT be called in LOCKED
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = [("WF_WIKIPEDIA_CREATORS", 0.95)]
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.90)]

    # Use an intent that matches the keyword registry for the browser routine
    with patch.object(llm_filter.llm, "available", return_value=True) as mock_avail, \
         patch.object(llm_filter.llm, "complete", return_value="WF_WIKIPEDIA_CREATORS") as mock_complete:
        plan = router.resolve_plan(
            Intent(raw_text="open web browser and navigate to google", timestamp=0.0),
            mode="LOCKED",
        )

    # Vector search was never called
    router._vector.workflow_candidates.assert_not_called()
    router._vector.candidates.assert_not_called()
    # LLM filter was never called
    mock_complete.assert_not_called()

    # Should resolve via keyword fallback to a routine (if matched) or GENERIC
    # The exact result depends on registry keywords, but crucially NOT a workflow
    assert plan.kind in ("ROUTINE", "GENERIC")
    if plan.kind == "ROUTINE":
        assert plan.source == "keyword"


def test_locked_no_autonomous_fallback(tmp_path):
    """LOCKED mode: unmatched intent → GENERIC (no autonomous fallback)."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    router = ShepherdIntentRouter()
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))

    router._vector = MagicMock()

    plan = router.resolve_plan(
        Intent(raw_text="some completely novel unrecognized gibberish xyzzy", timestamp=0.0),
        mode="LOCKED",
    )

    # No autonomous fallback in LOCKED — just GENERIC
    assert plan.kind == "GENERIC"
    assert plan.source == "fallback"
    # Vector never called
    router._vector.workflow_candidates.assert_not_called()
    router._vector.candidates.assert_not_called()


# ── Test 5: Relay `mode` command with AUTONOMOUS ──────────────────────────────

def test_relay_mode_command_autonomous():
    """RelayClient._apply_command('mode', ...) accepts AUTONOMOUS: sets runtime mode + emits."""
    import queue as _queue
    import config as _cfg
    from dashboard.events import event_bus
    from services.relay_client import RelayClient

    # Save original state
    original_mode = _cfg._runtime_mode
    emitted_events: list[dict] = []

    def capture_event(message):
        if message.get("type") == "mode.changed":
            emitted_events.append(message["data"])

    event_bus.subscribe(capture_event)

    try:
        # Exercise the real relay handler (lowercase input → normalized to AUTONOMOUS).
        client = RelayClient(engine=MagicMock(), remote_intents=_queue.Queue())
        client._apply_command("mode", {"mode": "autonomous"})

        assert _cfg._runtime_mode == "AUTONOMOUS"
        assert len(emitted_events) == 1
        assert emitted_events[0]["mode"] == "AUTONOMOUS"
    finally:
        _cfg._runtime_mode = original_mode
        event_bus.unsubscribe(capture_event)


# ── Test: mode=None preserves LIVE-like behavior (back-compat) ────────────────

def test_mode_none_defaults_to_live_behavior(tmp_path):
    """mode=None (back-compat default) behaves like LIVE: workflows + routines + LLM filter."""
    from router.router import ShepherdIntentRouter
    from engine.workflow_store import WorkflowStore

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(_build_wiki_workflow())

    # Enable routines explicitly so "both sources consulted" reflects mode, not
    # the deployment default (config ships with routines off).
    router = ShepherdIntentRouter(match_routines=True)
    router._workflows = store

    # Mock vector router: both sources
    router._vector = MagicMock()
    router._vector.workflow_candidates.return_value = [("WF_WIKIPEDIA_CREATORS", 0.88)]
    router._vector.candidates.return_value = [("ROUTINE_JOB_APPLICATION", 0.79)]

    # LLM filter chooses the workflow
    with patch.object(llm_filter.llm, "available", return_value=True), \
         patch.object(llm_filter.llm, "complete", return_value="WF_WIKIPEDIA_CREATORS"):
        plan = router.resolve_plan(Intent(raw_text=RESEARCH_INTENT, timestamp=0.0))

    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_WIKIPEDIA_CREATORS"
    # Both vector sources were called (LIVE-like behavior)
    router._vector.workflow_candidates.assert_called_once()
    router._vector.candidates.assert_called_once()
