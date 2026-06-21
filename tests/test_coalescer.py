"""
Tests for the trace journal + async coalescer foundation. No network:
the LLM path is forced off so segmentation uses the heuristic fallback.

    .venv/bin/python tests/test_coalescer.py    # or: pytest tests/
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import RunTrace, RoutineStep, BatchField, InterventionEvent
from engine import trace_journal, coalescer
from engine import milestones as M
from engine.task_graph import TaskGraphStore


def test_journal_roundtrip_preserves_steps_fields_and_interventions():
    with tempfile.TemporaryDirectory() as d:
        orig_dir = trace_journal._DIR
        trace_journal._DIR = d
        try:
            steps = [
                RoutineStep(action="open_app", target="Chrome", description="Open form"),
                RoutineStep(
                    action="batch_fill", description="Fill",
                    fields=[BatchField(tabs=1, text="Alex", description="First name")],
                ),
                RoutineStep(action="hotkey", keys=["cmd", "return"], description="Submit"),
            ]
            tr = RunTrace(
                run_id="t1", routine_id="R", variables={"A": "1"}, status="completed",
                started_at=1.0, ended_at=2.0, executed=steps,
                interventions=[InterventionEvent(step_index=1, trigger="credential",
                                                 decision="override", instruction="use test creds",
                                                 flag="save_as_rule")],
                deviations=[{"step_index": 1, "reason": "x"}],
            )
            trace_journal.write(tr)
            back = trace_journal.read("t1")

            assert [s.action for s in back.executed] == ["open_app", "batch_fill", "hotkey"]
            # BatchField is rehydrated into an object, not left as a dict.
            bf = back.executed[1].fields[0]
            assert isinstance(bf, BatchField) and bf.description == "First name"
            assert back.executed[2].keys == ["cmd", "return"]
            assert back.interventions[0].flag == "save_as_rule"
            assert back.interventions[0].instruction == "use test creds"
            assert back.deviations == [{"step_index": 1, "reason": "x"}]
            assert "t1" in trace_journal.list_run_ids()
        finally:
            trace_journal._DIR = orig_dir


def test_coalesce_offline_builds_graph_with_edges():
    with tempfile.TemporaryDirectory() as d:
        orig_store = coalescer._store
        orig_llm = M.llm_available
        coalescer._store = TaskGraphStore(path=os.path.join(d, "g.json"))
        M.llm_available = lambda: False   # force heuristic, no network
        try:
            steps = [
                RoutineStep(action="open_app", target="Chrome", description="Open form"),
                RoutineStep(action="type", text="Alex", description="First name"),
                RoutineStep(action="hotkey", keys=["cmd", "return"], description="Submit form"),
            ]
            coalescer.coalesce_now(RunTrace(run_id="c1", routine_id="R", executed=steps))

            g = coalescer._store.load("R", {})
            assert g.run_count == 1
            assert len(g.nodes) >= 2
            assert len(g.edges) >= 1
            # A second run reinforces the shared edges rather than duplicating nodes.
            coalescer.coalesce_now(RunTrace(run_id="c2", routine_id="R", executed=steps))
            g2 = coalescer._store.load("R", {})
            assert g2.run_count == 2
            assert len(g2.nodes) == len(g.nodes)
            assert any(e.times_seen == 2 for e in g2.edges)
        finally:
            coalescer._store = orig_store
            M.llm_available = orig_llm


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
