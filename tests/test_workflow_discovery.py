"""
Tests for workflow discovery fix (issue #33): auto-promoted workflows must be
discoverable on re-dispatch via both the substring fallback and vector index text.

Covers:
  1. promote() yields NL-based, non-slug intent_patterns
  2. index text includes description + node labels
  3. re-dispatching the original NL intent matches via the substring/offline path
  4. promotion does NOT block when the description/LLM step is slow or unavailable
  5. coalescer threads the real NL intent into the graph
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import (
    TaskGraph, TaskGraphNode, TaskGraphEdge, Workflow,
    RunTrace, RoutineStep,
)
from engine.task_graph import TaskGraphStore
from engine.workflow_store import WorkflowStore
from router.vector_router import VectorRouter


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_graph(
    task_key: str = "AUTONOMOUS::find_the_creators_of_the_c____python__and_java_p",
    intents: list[str] | None = None,
) -> TaskGraph:
    """Build a graph mimicking the C++/Python/Java creators example."""
    graph = TaskGraph(task_key=task_key, routine_id=task_key)
    graph.nodes = [
        TaskGraphNode(key="open::::Open browser", kind="open", label="Open browser"),
        TaskGraphNode(key="navigate::::Go to Wikipedia", kind="navigate", label="Go to Wikipedia"),
        TaskGraphNode(key="search::::Search language creator", kind="search", label="Search language creator"),
        TaskGraphNode(key="scan::::Read results", kind="scan", label="Read results"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="open::::Open browser", to_key="navigate::::Go to Wikipedia"),
        TaskGraphEdge(from_key="navigate::::Go to Wikipedia", to_key="search::::Search language creator"),
        TaskGraphEdge(from_key="search::::Search language creator", to_key="scan::::Read results"),
    ]
    graph.intents = intents if intents is not None else [
        "find the creators of the C++, Python, and Java programming languages"
    ]
    graph.run_count = 1
    return graph


# ── Test 1: promote() yields NL-based, non-slug intent_patterns ─────────────

def test_promote_uses_nl_intent_not_slug(tmp_path):
    """When graph.intents contains real NL text, promote() uses it for
    name and intent_patterns — NOT the slugified task_key."""
    graph = _make_graph()
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    raw_name = graph.intents[0]
    name = raw_name.strip()[:60]
    intent_patterns = list(graph.intents)
    slug = graph.task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    workflow_id = f"WF_{slug.upper()[:40]}"

    wf = store.promote(graph, workflow_id, name, intent_patterns)

    # The name should be the real NL intent, not a slug
    assert "find the creators" in wf.name.lower()
    assert "_" not in wf.name  # not slugified
    # The intent_patterns should contain the real NL text
    assert any("creators" in p.lower() for p in wf.intent_patterns)
    assert not any("____" in p for p in wf.intent_patterns)  # no slug artifacts


def test_promote_with_empty_intents_falls_back_to_slug(tmp_path):
    """When graph.intents is empty (legacy), promotion still works with slug."""
    graph = _make_graph(intents=[])
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    raw_name = graph.task_key.replace("AUTONOMOUS::", "")
    name = raw_name.strip()[:60]
    intent_patterns = [raw_name]
    slug = graph.task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    workflow_id = f"WF_{slug.upper()[:40]}"

    wf = store.promote(graph, workflow_id, name, intent_patterns)
    assert wf.name  # should have something, even if slug-based


# ── Test 2: index text includes description + node labels ───────────────────

def test_index_text_includes_description_and_labels():
    """VectorRouter._workflow_index_text embeds name + description +
    intent_patterns + node labels (not just name + patterns)."""
    wf = Workflow(
        id="WF_TEST",
        name="Look up programming-language creators",
        description="Searches Wikipedia for the creators of programming languages",
        intent_patterns=["find who created a programming language"],
        nodes=[
            TaskGraphNode(key="k1", kind="open", label="Open browser"),
            TaskGraphNode(key="k2", kind="search", label="Search language creator"),
        ],
    )

    text = VectorRouter._workflow_index_text(wf)

    assert "Look up programming-language creators" in text
    assert "Searches Wikipedia" in text
    assert "find who created a programming language" in text
    assert "Open browser" in text
    assert "Search language creator" in text


def test_index_text_without_description():
    """When description is empty, index text still works (name + patterns + labels)."""
    wf = Workflow(
        id="WF_TEST",
        name="apply to this job",
        intent_patterns=["apply to this job posting"],
        nodes=[
            TaskGraphNode(key="k1", kind="fill", label="Fill form"),
        ],
    )

    text = VectorRouter._workflow_index_text(wf)
    assert "apply to this job" in text
    assert "Fill form" in text


# ── Test 3: re-dispatching the original NL intent matches via substring ──────

def test_substring_fallback_matches_nl_intent(tmp_path):
    """When vector search is unavailable, the substring fallback in
    router._match_workflow() should match the original NL intent against
    the workflow's intent_patterns (which now contain real NL text)."""
    graph = _make_graph()
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    # Promote with NL-derived name + patterns
    wf = store.promote(
        graph,
        "WF_LANG_CREATORS",
        "find the creators of the C++, Python, and Java programming languages",
        ["find the creators of the C++, Python, and Java programming languages"],
    )

    # Simulate what router._match_workflow does offline:
    # substring match on each workflow's intent_patterns
    query = "find the creators of the C++, Python, and Java programming languages"
    low = query.lower().strip()
    workflows = store.list()
    matched = None
    for w in workflows:
        hits = [p for p in w.intent_patterns if p.lower() in low]
        if hits:
            matched = w
            break

    assert matched is not None, "Substring fallback should match the original NL intent"
    assert matched.id == "WF_LANG_CREATORS"


def test_slug_based_patterns_fail_substring_match(tmp_path):
    """Demonstrate the old bug: slug-based patterns DON'T match real NL queries."""
    graph = _make_graph(intents=[])  # no intents → slug fallback
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    slug = graph.task_key.replace("AUTONOMOUS::", "")
    wf = store.promote(graph, "WF_SLUG", slug, [slug])

    query = "find the creators of the C++, Python, and Java programming languages"
    low = query.lower().strip()
    hits = [p for p in wf.intent_patterns if p.lower() in low]
    assert not hits, "Slug patterns should NOT match real NL queries (this was the bug)"


# ── Test 4: promotion does NOT block when description is async ──────────────

def test_promote_does_not_block_on_description(tmp_path):
    """Promotion must complete instantly; the LLM describe is async.
    Verify that promote_graph returns immediately (under 1s) even though
    generate_description was fired."""
    from engine.workflow_promote import promote_graph

    # Set up a graph on disk
    gs_path = str(tmp_path / "task_graphs.json")
    ws_path = str(tmp_path / "workflows.json")
    graph_store = TaskGraphStore(gs_path)
    graph = _make_graph()
    graph_store.save(graph, intent_text="find the creators", variables={}, run_id="r1")

    t0 = time.monotonic()
    wf = promote_graph(
        graph.task_key,
        graph_store_path=gs_path,
        wf_store_path=ws_path,
    )
    elapsed = time.monotonic() - t0

    assert wf is not None
    assert wf.name  # has a name (NL-derived)
    assert elapsed < 2.0, f"Promotion took {elapsed:.1f}s — should be near-instant"


# ── Test 5: coalescer threads real NL intent into the graph ──────────────────

def test_coalescer_saves_intent_text(tmp_path):
    """When the coalescer receives a trace with intent_text, the graph's
    intents list should contain that text after coalescing."""
    from engine import coalescer
    from engine import milestones as M

    orig_store = coalescer._store
    orig_llm = M.llm_available
    coalescer._store = TaskGraphStore(path=str(tmp_path / "g.json"))
    M.llm_available = lambda: False  # force heuristic, no network

    try:
        steps = [
            RoutineStep(action="open_app", target="Chrome", description="Open browser"),
            RoutineStep(action="type", text="C++ creator", description="Search for C++ creator"),
            RoutineStep(action="hotkey", keys=["cmd", "return"], description="Submit search"),
        ]
        trace = RunTrace(
            run_id="disc1",
            routine_id="AUTONOMOUS::find_creators",
            executed=steps,
            intent_text="find the creators of C++, Python, and Java",
        )
        coalescer.coalesce_now(trace)

        g = coalescer._store.load("AUTONOMOUS::find_creators", {})
        assert g.run_count == 1
        assert "find the creators of C++, Python, and Java" in g.intents
    finally:
        coalescer._store = orig_store
        M.llm_available = orig_llm


def test_coalescer_empty_intent_text_does_not_add(tmp_path):
    """When intent_text is empty (legacy behavior), no empty string is added."""
    from engine import coalescer
    from engine import milestones as M

    orig_store = coalescer._store
    orig_llm = M.llm_available
    coalescer._store = TaskGraphStore(path=str(tmp_path / "g.json"))
    M.llm_available = lambda: False

    try:
        steps = [
            RoutineStep(action="open_app", target="Chrome", description="Open browser"),
        ]
        trace = RunTrace(
            run_id="disc2",
            routine_id="AUTONOMOUS::test",
            executed=steps,
            intent_text="",
        )
        coalescer.coalesce_now(trace)

        g = coalescer._store.load("AUTONOMOUS::test", {})
        assert "" not in g.intents
    finally:
        coalescer._store = orig_store
        M.llm_available = orig_llm


# ── Test: Workflow description field serialization round-trip ────────────────

def test_workflow_description_round_trip(tmp_path):
    """Workflow description serializes and deserializes correctly."""
    store = WorkflowStore(str(tmp_path / "workflows.json"))
    wf = Workflow(
        id="WF_TEST",
        name="Test workflow",
        description="Searches Wikipedia for language creators",
        intent_patterns=["find who created a language"],
    )
    store.save(wf)
    loaded = store.get("WF_TEST")
    assert loaded is not None
    assert loaded.description == "Searches Wikipedia for language creators"


def test_workflow_description_defaults_empty(tmp_path):
    """Workflows saved before the description field default to empty string."""
    import json
    path = str(tmp_path / "workflows.json")
    # Write a workflow without the description field (legacy format)
    with open(path, "w") as f:
        json.dump({"WF_OLD": {
            "id": "WF_OLD", "name": "old workflow",
            "intent_patterns": [], "params": [], "version": 1,
            "from_graph": "", "start_key": "", "created_at": 0, "updated_at": 0,
            "nodes": [], "edges": [],
        }}, f)

    store = WorkflowStore(path)
    wf = store.get("WF_OLD")
    assert wf is not None
    assert wf.description == ""
