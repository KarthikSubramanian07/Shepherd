"""
Phase 4/5 tests — workflow dispatch + milestone-graph traversal.

Deterministic and network-free: the LLM worker is forced onto its heuristic
fallback so the executor's control flow (single-message advance, conditional
branching, KB extraction) is validated without hitting Gemma. A separate live
Gemma run (scripts/validate_job_app.py) exercises the same scenario with the
real model.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import Intent, TaskGraph, TaskGraphNode, TaskGraphEdge, Conditional
from engine import llm
from engine import workflow_store as WS
from engine.workflow_store import WorkflowStore
from engine.workflow_executor import WorkflowExecutor, LLMWorker, options_for
from tests.mock_job_app import (
    build_workflow, MockJobAppEnv, OPEN, FILL, PROJECTS, RESEARCH, SUBMIT,
)


def _no_llm(monkeypatch):
    """Force the LLM worker onto its deterministic heuristic."""
    monkeypatch.setattr(llm, "available", lambda: False)


# ── phase 4: promotion + store ───────────────────────────────────────────────────
def test_promote_graph_to_workflow(tmp_path):
    graph = TaskGraph(task_key="ROUTINE_FORM_FILL", routine_id="ROUTINE_FORM_FILL")
    graph.nodes = [
        TaskGraphNode(key="open::::Open", kind="open", label="Open"),
        TaskGraphNode(key="fill::::Fill", kind="fill", label="Fill"),
        TaskGraphNode(key="submit::::Submit", kind="submit", label="Submit"),
    ]
    graph.edges = [
        TaskGraphEdge(from_key="open::::Open", to_key="fill::::Fill"),
        TaskGraphEdge(from_key="fill::::Fill", to_key="submit::::Submit"),
    ]
    graph.variables = {"APPLICANT_NAME": "Alex"}

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    wf = store.promote(graph, "WF_FORM", "Fill a form", ["fill a form", "apply"])

    assert wf.id == "WF_FORM"
    assert wf.version == 1
    assert wf.start_key == "open::::Open"          # derived entry node
    assert wf.params == ["APPLICANT_NAME"]
    # round-trips through disk
    loaded = store.get("WF_FORM")
    assert loaded is not None and [n.key for n in loaded.nodes] == [n.key for n in wf.nodes]
    # re-promoting bumps the version
    assert store.promote(graph, "WF_FORM", "Fill a form", ["apply"]).version == 2


def test_options_preview_includes_edges_and_conditionals():
    wf = build_workflow()
    projects = next(n for n in wf.nodes if n.key == PROJECTS)
    opts = options_for(wf, projects)
    keys = {o.key: o for o in opts}
    assert RESEARCH in keys and keys[RESEARCH].via == "conditional"   # taught branch
    assert SUBMIT in keys and keys[SUBMIT].via == "edge"             # common path
    assert keys[RESEARCH].when                                        # carries NL guard


# ── phase 4: router returns a Plan, preferring a saved workflow ───────────────────
def test_router_dispatches_workflow_over_routine(tmp_path):
    from router.router import ShepherdIntentRouter

    store = WorkflowStore(str(tmp_path / "workflows.json"))
    store.save(build_workflow())

    router = ShepherdIntentRouter()
    router._workflows = store   # offline: pattern fallback over intent_patterns

    plan = router.resolve_plan(Intent(raw_text="please apply to this job for me", timestamp=0.0))
    assert plan.kind == "WORKFLOW"
    assert plan.target == "WF_JOB_APPLICATION"
    assert "apply to this job" in plan.matched


def test_router_falls_back_to_routine_when_no_workflow(tmp_path):
    from router.router import ShepherdIntentRouter

    router = ShepherdIntentRouter()
    router._workflows = WorkflowStore(str(tmp_path / "empty.json"))  # no workflows

    plan = router.resolve_plan(Intent(raw_text="fill out the form", timestamp=0.0))
    assert plan.kind in ("ROUTINE", "GENERIC")
    assert plan.kind != "WORKFLOW"


# ── phase 5: traversal — single-message advance + conditional branch ──────────────
def test_traversal_takes_taught_branch_to_fake_projects_page(monkeypatch):
    _no_llm(monkeypatch)
    wf = build_workflow()
    events = []
    ex = WorkflowExecutor(LLMWorker(MockJobAppEnv()), event_emit=lambda n, p: events.append((n, p)))

    # No projects_summary in profile → the taught conditional must fire.
    run = ex.run(wf, goal="apply to this job",
                 params={"applicant_name": "Alex Johnson", "applicant_email": "alex@example.com"})

    assert run.status == "completed"
    # It followed: open → fill → projects → RESEARCH (fake page) → submit
    assert run.visited_keys == [OPEN, FILL, PROJECTS, RESEARCH, SUBMIT]
    # It read the fake projects page and learned the summary
    assert "projects_summary" in run.profile
    assert "Shepherd" in run.profile["projects_summary"]

    # single-message advance: every non-terminal step previewed its next options
    steps = [p for (n, p) in events if n == "workflow.step"]
    projects_step = next(s for s in steps if s["node_key"] == PROJECTS)
    assert projects_step["branch"]                      # took a conditional in the same msg
    assert projects_step["next"] == RESEARCH
    assert {o["key"] for o in projects_step["options"]} >= {RESEARCH, SUBMIT}


def test_traversal_skips_branch_when_input_already_known(monkeypatch):
    _no_llm(monkeypatch)
    wf = build_workflow()
    ex = WorkflowExecutor(LLMWorker(MockJobAppEnv()))

    run = ex.run(wf, goal="apply to this job", params={
        "applicant_name": "Alex Johnson", "applicant_email": "alex@example.com",
        "projects_summary": "Prior summary already on file",
    })

    assert run.status == "completed"
    assert RESEARCH not in run.visited_keys           # no need to research
    assert run.visited_keys == [OPEN, FILL, PROJECTS, SUBMIT]


def test_unknown_next_falls_back_to_common_path(monkeypatch):
    """A fuzzy/unknown `next` from the worker never strands the run — it falls back
    to the first (common-path) option."""
    _no_llm(monkeypatch)
    wf = build_workflow()

    class WildWorker:
        def act(self, turn):
            from engine.workflow_executor import WorkerResult
            return WorkerResult(did="x", status="done", next="not-a-real-node")

    run = WorkflowExecutor(WildWorker()).run(wf, params={})
    assert run.status in ("completed", "aborted")
    assert run.visited_keys[0] == OPEN                 # still started + advanced
    assert len(run.visited_keys) > 1
