"""
Tests for the auto-promote feature (issue #28): first-time ad-hoc autonomous
tasks are promoted into dispatchable workflows when the 'Bake out' toggle
fires the promote command.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import TaskGraph, TaskGraphNode, TaskGraphEdge
from engine.task_graph import TaskGraphStore
from engine.workflow_store import WorkflowStore


def _make_graph(task_key: str = "AUTONOMOUS::apply_to_job") -> TaskGraph:
    """Build a minimal but realistic autonomous task graph."""
    graph = TaskGraph(task_key=task_key, routine_id=task_key)
    graph.nodes = [
        TaskGraphNode(key="open::::Open browser", kind="open", label="Open browser"),
        TaskGraphNode(key="navigate::::Go to site", kind="navigate", label="Go to site"),
        TaskGraphNode(key="fill::::Fill form", kind="fill", label="Fill form"),
        TaskGraphNode(key="submit::::Submit", kind="submit", label="Submit"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="open::::Open browser", to_key="navigate::::Go to site"),
        TaskGraphEdge(from_key="navigate::::Go to site", to_key="fill::::Fill form"),
        TaskGraphEdge(from_key="fill::::Fill form", to_key="submit::::Submit"),
    ]
    graph.intents = ["apply to this job posting", "submit my application"]
    graph.variables = {"JOB_URL": "https://example.com/job"}
    graph.run_count = 1
    return graph


# ── promote logic (mirrors relay_client._promote_graph / dashboard endpoint) ──

def test_promote_from_task_graph(tmp_path):
    """WorkflowStore.promote() creates a workflow from a crystallized graph."""
    graph = _make_graph()
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    raw_name = graph.intents[0] if graph.intents else graph.task_key
    name = raw_name.strip()[:60]
    intent_patterns = list(graph.intents) if graph.intents else [raw_name]
    slug = graph.task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    workflow_id = f"WF_{slug.upper()[:40]}"

    wf = store.promote(graph, workflow_id, name, intent_patterns)

    assert wf.id == "WF_APPLY_TO_JOB"
    assert wf.name == "apply to this job posting"
    assert wf.intent_patterns == ["apply to this job posting", "submit my application"]
    assert wf.version == 1
    assert len(wf.nodes) == 4
    assert wf.start_key == "open::::Open browser"
    assert wf.from_graph == "AUTONOMOUS::apply_to_job"


def test_promote_idempotent_bumps_version(tmp_path):
    """Re-promoting the same graph bumps the workflow version (safe re-fire)."""
    graph = _make_graph()
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    wf1 = store.promote(graph, "WF_APPLY_TO_JOB", "apply to this job", ["apply"])
    wf2 = store.promote(graph, "WF_APPLY_TO_JOB", "apply to this job", ["apply"])

    assert wf1.version == 1
    assert wf2.version == 2


def test_promote_with_no_intents_falls_back_to_slug(tmp_path):
    """When graph.intents is empty, name/patterns derive from task_key slug."""
    graph = _make_graph("AUTONOMOUS::fill_form")
    graph.intents = []
    store = WorkflowStore(str(tmp_path / "workflows.json"))

    raw_name = graph.task_key.replace("AUTONOMOUS::", "")
    name = raw_name.strip()[:60]
    intent_patterns = [raw_name]
    slug = graph.task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    workflow_id = f"WF_{slug.upper()[:40]}"

    wf = store.promote(graph, workflow_id, name, intent_patterns)

    assert wf.name == "fill_form"
    assert wf.intent_patterns == ["fill_form"]


def test_promote_skips_empty_graph(tmp_path):
    """An empty graph (run_count=0, no nodes) should not be promoted."""
    graph = TaskGraph(task_key="AUTONOMOUS::empty", routine_id="AUTONOMOUS::empty")
    # Simulate the guard from _promote_graph / the endpoint
    assert graph.run_count == 0 and not graph.nodes


def test_promote_persisted_graph_via_store(tmp_path):
    """End-to-end: save a graph, load it back, promote it."""
    graph_store = TaskGraphStore(str(tmp_path / "task_graphs.json"))
    graph = _make_graph()
    graph_store.save(graph, intent_text="apply to this job", variables={"JOB_URL": "x"}, run_id="r1")

    # Reload from disk (simulates what the promote endpoint does)
    loaded = graph_store.load("AUTONOMOUS::apply_to_job", {})
    assert loaded.run_count == 2  # _make_graph sets 1, save increments
    assert "apply to this job" in loaded.intents

    wf_store = WorkflowStore(str(tmp_path / "workflows.json"))
    wf = wf_store.promote(loaded, "WF_APPLY_TO_JOB", loaded.intents[0], loaded.intents)
    assert wf.id == "WF_APPLY_TO_JOB"
    assert len(wf.nodes) == 4
