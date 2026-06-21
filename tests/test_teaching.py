"""
Tests for the teaching loop: EDIT-mode coalescing that bakes human interventions
into the workflow as conditional clauses / taught procedures. No network — the LLM
patch path is forced off so the deterministic heuristic patch runs.

    .venv/bin/python tests/test_teaching.py    # or: pytest tests/
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import RunTrace, RoutineStep, InterventionEvent
from engine import coalescer, workflow_edit, llm
from engine import milestones as M
from engine.task_graph import TaskGraphStore, milestone_key


def _graph_with_nodes():
    store = TaskGraphStore(path="/dev/null")
    from shepherd_types import TaskGraph
    g = TaskGraph(task_key="R", routine_id="R")
    store.record_milestone(g, "fill", "Fill applicant details", None, 3, "ok", "r0")
    store.record_milestone(g, "submit", "Submit application", None, 1, "ok", "r0")
    return store, g


def test_heuristic_patch_save_as_rule_becomes_conditional():
    store, g = _graph_with_nodes()
    key = milestone_key("fill", None, "Fill applicant details")
    trace = RunTrace(run_id="r1", routine_id="R", interventions=[
        InterventionEvent(step_index=1, trigger="unknown_field", decision="override",
                          instruction="research the applicant's GitHub for projects",
                          flag="save_as_rule", node_key=key,
                          scenario="the projects field is empty"),
    ])
    ops = workflow_edit.heuristic_patch(g, trace)
    assert len(ops) == 1 and ops[0]["op"] == "add_conditional"
    assert ops[0]["node"] == key
    assert ops[0]["do"] == "research the applicant's GitHub for projects"


def test_heuristic_patch_one_off_is_noop():
    store, g = _graph_with_nodes()
    key = milestone_key("fill", None, "Fill applicant details")
    trace = RunTrace(run_id="r1", routine_id="R", interventions=[
        InterventionEvent(step_index=1, decision="override", instruction="use test creds",
                          flag="one_off", node_key=key),
    ])
    ops = workflow_edit.heuristic_patch(g, trace)
    assert ops == [{"op": "noop", "reason": "one_off"}]


def test_apply_patch_bakes_conditional_onto_node():
    store, g = _graph_with_nodes()
    key = milestone_key("fill", None, "Fill applicant details")
    ops = [{"op": "add_conditional", "node": key,
            "when": "projects field is empty", "do": "research GitHub", "goto": None}]
    applied = workflow_edit.apply_patch(store, g, ops, "r1")
    node = store.node_by_key(g, key)
    assert len(applied) == 1
    assert node.source == "taught"
    assert len(node.conditionals) == 1
    assert node.conditionals[0].when == "projects field is empty"
    # Idempotent: re-applying the same op does not duplicate the clause.
    workflow_edit.apply_patch(store, g, ops, "r2")
    assert len(node.conditionals) == 1


def test_apply_patch_set_procedure_and_add_node():
    store, g = _graph_with_nodes()
    fill_key = milestone_key("fill", None, "Fill applicant details")
    ops = [
        {"op": "set_procedure", "node": fill_key,
         "procedure": "always fill projects from research", "requires": ["github_url"]},
        {"op": "add_node", "after": fill_key, "kind": "research",
         "label": "Research GitHub", "condition": "no project info on file",
         "procedure": "open the applicant's GitHub and summarize pinned repos"},
    ]
    applied = workflow_edit.apply_patch(store, g, ops, "r1")
    assert len(applied) == 2

    fill = store.node_by_key(g, fill_key)
    assert fill.procedure == "always fill projects from research"
    assert "github_url" in fill.requires

    research_key = milestone_key("research", None, "Research GitHub")
    research = store.node_by_key(g, research_key)
    assert research is not None and research.source == "taught"
    # Edge fill -> research carries the NL condition.
    edge = next(e for e in g.edges if e.from_key == fill_key and e.to_key == research_key)
    assert edge.condition == "no project info on file"


def test_fill_intervention_node_keys_from_executed_segmentation():
    executed_ms = [
        {"kind": "fill", "label": "Fill details", "value": None, "fine_start": 0, "fine_end": 2},
        {"kind": "submit", "label": "Submit", "value": None, "fine_start": 3, "fine_end": 3},
    ]
    trace = RunTrace(run_id="r1", routine_id="R", interventions=[
        InterventionEvent(step_index=1, instruction="x", flag="save_as_rule"),  # no node_key
    ])
    coalescer._fill_intervention_node_keys(trace, executed_ms)
    assert trace.interventions[0].node_key == milestone_key("fill", None, "Fill details")


def test_coalesce_edit_mode_bakes_into_known_workflow():
    with tempfile.TemporaryDirectory() as d:
        orig_store = coalescer._store
        orig_seg_llm = M.llm_available
        orig_llm_avail = llm.available
        coalescer._store = TaskGraphStore(path=os.path.join(d, "g.json"))
        # Force heuristic everywhere: segmentation (milestones) AND patch (workflow_edit).
        M.llm_available = lambda: False
        llm.available = lambda: False
        try:
            steps = [
                RoutineStep(action="open_app", target="Chrome", description="Open form"),
                RoutineStep(action="type", text="Alex", description="Fill applicant details"),
                RoutineStep(action="hotkey", keys=["cmd", "return"], description="Submit form"),
            ]
            # Run 1: CREATE — workflow learned, no teaching yet.
            coalescer.coalesce_now(RunTrace(run_id="c1", routine_id="R", executed=steps))
            g1 = coalescer._store.load("R", {})
            assert all(not n.conditionals for n in g1.nodes)

            # Run 2: EDIT — human taught a save_as_rule rule. node_key is left empty
            # so the coalescer maps step_index→milestone via the segmentation itself.
            trace2 = RunTrace(run_id="c2", routine_id="R", executed=steps, interventions=[
                InterventionEvent(step_index=1, trigger="unknown_field", decision="override",
                                  instruction="research the applicant's GitHub",
                                  flag="save_as_rule",
                                  scenario="projects field empty"),
            ])
            coalescer.coalesce_now(trace2)

            g2 = coalescer._store.load("R", {})
            taught = [n for n in g2.nodes if n.conditionals]
            assert len(taught) == 1
            assert taught[0].source == "taught"
            assert taught[0].conditionals[0].do == "research the applicant's GitHub"
            # Survives persistence roundtrip.
            assert taught[0].conditionals[0].when == "projects field empty"
        finally:
            coalescer._store = orig_store
            M.llm_available = orig_seg_llm
            llm.available = orig_llm_avail


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
