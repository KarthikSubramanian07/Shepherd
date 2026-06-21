"""
Tests for auto-promotion of crystallized task graphs into dispatchable
workflows — the fix for "workflows don't show up in the frontend". A graph that
crystallizes (or already exists) is promoted so it appears in /api/workflows,
without a manual bake-out.

Network-free: the async LLM describe step is stubbed to a no-op.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import engine.workflow_describe as _describe
import engine.generalize as _generalize
from shepherd_types import TaskGraph, TaskGraphNode, TaskGraphEdge
from engine.task_graph import TaskGraphStore
from engine.workflow_store import WorkflowStore
from engine import workflow_promote as WP


@pytest.fixture(autouse=True)
def _stub_llm():
    """Keep promotion network-free WITHOUT leaking stubs into other test modules:
    promote_graph lazily imports generate_description + generalize_goal, so we
    patch the module attributes for the duration of each test and restore after.
    generalize is stubbed to identity → deterministic workflow ids."""
    o_desc = _describe.generate_description
    o_gen = _generalize.generalize_goal
    _describe.generate_description = lambda *a, **k: None
    _generalize.generalize_goal = lambda goal: (goal or "").strip()
    try:
        yield
    finally:
        _describe.generate_description = o_desc
        _generalize.generalize_goal = o_gen


def _make_graph(task_key, n_nodes=3, run_count=1, intent=None):
    g = TaskGraph(task_key=task_key, routine_id=task_key)
    kinds = ["open", "fill", "submit", "verify", "scan"]
    g.nodes = [
        TaskGraphNode(key=f"{kinds[i % 5]}::::Step{i}", kind=kinds[i % 5], label=f"Step{i}")
        for i in range(n_nodes)
    ]
    g.edges = [
        TaskGraphEdge(from_key=g.nodes[i].key, to_key=g.nodes[i + 1].key)
        for i in range(max(0, n_nodes - 1))
    ]
    # Intent drives the (generalized) workflow identity; default it from the key.
    g.intents = [intent or task_key.replace("AUTONOMOUS::", "").replace("_", " ")]
    g.run_count = run_count
    return g


def _stores(tmp_path):
    gp = str(tmp_path / "task_graphs.json")
    wp = str(tmp_path / "workflows.json")
    return gp, wp


def test_workflow_id_for():
    assert WP.workflow_id_for("AUTONOMOUS::write_a_gmail_message") == "WF_WRITE_A_GMAIL_MESSAGE"


def test_promote_graph_basic(tmp_path):
    gp, wp = _stores(tmp_path)
    gs = TaskGraphStore(gp)
    gs.save(_make_graph("AUTONOMOUS::write_a_gmail_message"),
            intent_text="write a gmail message", variables={}, run_id="r1")

    wf = WP.promote_graph("AUTONOMOUS::write_a_gmail_message", gp, wp)
    assert wf is not None
    assert wf.id == "WF_WRITE_A_GMAIL_MESSAGE"
    assert WorkflowStore(wp).get("WF_WRITE_A_GMAIL_MESSAGE") is not None


def test_skip_if_unchanged_avoids_version_churn(tmp_path):
    gp, wp = _stores(tmp_path)
    gs = TaskGraphStore(gp)
    gs.save(_make_graph("AUTONOMOUS::foo", n_nodes=3),
            intent_text="foo", variables={}, run_id="r1")

    first = WP.promote_graph("AUTONOMOUS::foo", gp, wp, skip_if_unchanged=True)
    assert first is not None and first.version == 1

    # Same graph again — no growth → skipped, version stays 1.
    second = WP.promote_graph("AUTONOMOUS::foo", gp, wp, skip_if_unchanged=True)
    assert second is None
    assert WorkflowStore(wp).get("WF_FOO").version == 1

    # Graph grows → re-promoted, version bumps.
    gs.save(_make_graph("AUTONOMOUS::foo", n_nodes=5),
            intent_text="foo", variables={}, run_id="r2")
    third = WP.promote_graph("AUTONOMOUS::foo", gp, wp, skip_if_unchanged=True)
    assert third is not None and third.version == 2


def test_backfill_promotes_qualifying_and_skips_degenerate(tmp_path):
    gp, wp = _stores(tmp_path)
    gs = TaskGraphStore(gp)
    gs.save(_make_graph("AUTONOMOUS::big", n_nodes=4), intent_text="big", variables={}, run_id="r1")
    gs.save(_make_graph("AUTONOMOUS::two", n_nodes=2), intent_text="two", variables={}, run_id="r2")
    gs.save(_make_graph("AUTONOMOUS::tiny", n_nodes=1), intent_text="tiny", variables={}, run_id="r3")

    promoted = WP.backfill_workflows(min_nodes=2, graph_store_path=gp, wf_store_path=wp)
    assert set(promoted) == {"WF_BIG", "WF_TWO"}      # tiny (1 node) skipped
    assert WorkflowStore(wp).get("WF_TINY") is None

    # Idempotent: nothing new on a second pass.
    assert WP.backfill_workflows(min_nodes=2, graph_store_path=gp, wf_store_path=wp) == []


def test_backfill_merges_specific_graphs_into_one_general_workflow(tmp_path):
    """Topic-specific legacy graphs collapse into ONE general workflow."""
    gp, wp = _stores(tmp_path)
    gs = TaskGraphStore(gp)
    # Two different specific graphs that generalize to the same goal.
    gs.save(_make_graph("AUTONOMOUS::draft_email_dolphins", n_nodes=3,
                        intent="draft a mail app email"),
            intent_text="draft a mail app email", variables={}, run_id="r1")
    gs.save(_make_graph("AUTONOMOUS::draft_email_mosquitos", n_nodes=5,
                        intent="draft a mail app email"),
            intent_text="draft a mail app email", variables={}, run_id="r2")

    promoted = WP.backfill_workflows(min_nodes=2, graph_store_path=gp, wf_store_path=wp)
    assert promoted == ["WF_DRAFT_A_MAIL_APP_EMAIL"]   # merged, not two specifics

    wf = WorkflowStore(wp).get("WF_DRAFT_A_MAIL_APP_EMAIL")
    assert wf.name == "draft a mail app email"
    assert len(wf.nodes) == 5   # richest graph is the representative


def test_coalescer_auto_promotes_on_save(tmp_path, monkeypatch):
    """The coalescer cold path promotes the saved graph (config-gated)."""
    import config
    monkeypatch.setattr(config, "AUTO_PROMOTE_WORKFLOWS", True, raising=False)
    monkeypatch.setattr(config, "AUTO_PROMOTE_MIN_NODES", 2, raising=False)

    gp, wp = _stores(tmp_path)
    calls = {}

    def _fake_promote(task_key, *a, **k):
        calls["task_key"] = task_key
        calls["skip"] = k.get("skip_if_unchanged")
        return None

    monkeypatch.setattr(WP, "promote_graph", _fake_promote)

    from engine import coalescer
    graph = _make_graph("AUTONOMOUS::bar", n_nodes=3)
    coalescer._maybe_auto_promote("AUTONOMOUS::bar", graph)
    assert calls["task_key"] == "AUTONOMOUS::bar"
    assert calls["skip"] is True

    # Below the node floor → not promoted.
    calls.clear()
    coalescer._maybe_auto_promote("AUTONOMOUS::baz", _make_graph("AUTONOMOUS::baz", n_nodes=1))
    assert calls == {}


if __name__ == "__main__":
    import tempfile
    import pathlib
    # Fixtures don't run under the standalone runner — set the stubs directly.
    _describe.generate_description = lambda *a, **k: None
    _generalize.generalize_goal = lambda goal: (goal or "").strip()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                import inspect
                params = inspect.signature(fn).parameters
                if "tmp_path" in params:
                    with tempfile.TemporaryDirectory() as d:
                        fn(pathlib.Path(d)) if len(params) == 1 else None
                else:
                    fn()
                print(f"ok  {name}")
            except TypeError:
                print(f"skip {name} (needs pytest fixtures)")
    print("done")
