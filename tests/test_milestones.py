"""
Tests for the milestone segmenter + task-graph edges.

No network and no test framework required: run directly with
    .venv/bin/python tests/test_milestones.py
(The test_* functions are also pytest-discoverable if pytest is added later.)
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shepherd_types import RoutineStep, TaskGraph
from engine import milestones as M
from engine.milestones import TAXONOMY, render_step, _heuristic_segment
from engine.task_graph import (
    TaskGraphStore, milestone_key, _serialize, _deserialize,
)

# A GitHub-research trace like ROUTINE_JOB_APPLICATION (9 fine steps).
TRACE = [
    RoutineStep(action="open_app", target="Chrome", text="localhost/form", description="Open form"),
    RoutineStep(action="wait", seconds=1.0, description="Wait for form"),
    RoutineStep(action="type", text="Alex", description="First name"),
    RoutineStep(action="hotkey", keys=["cmd", "t"], description="Open new tab to research GitHub"),
    RoutineStep(action="type", text="github.com/octocat\n", description="Navigate to GitHub profile"),
    RoutineStep(action="wait", seconds=1.0, description="Scan pinned repositories"),
    RoutineStep(action="hotkey", keys=["cmd", "1"], description="Switch back to application tab"),
    RoutineStep(action="type", text="summary", description="Fill projects from research"),
    RoutineStep(action="hotkey", keys=["cmd", "return"], description="Submit application"),
]


def test_heuristic_segmentation_is_contiguous_and_complete(monkeypatch=None):
    """Heuristic fallback (no network) must fully cover the trace, in order."""
    # Force the offline path regardless of whether a key is configured.
    orig = M.llm_available
    M.llm_available = lambda: False
    try:
        ms = M.segment(TRACE, {}, prior_labels=[])
    finally:
        M.llm_available = orig

    assert ms, "expected at least one milestone"
    assert all(m["kind"] in TAXONOMY for m in ms), "all kinds must be in the taxonomy"
    # Contiguous, gap-free coverage of every fine index.
    assert ms[0]["fine_start"] == 0
    assert ms[-1]["fine_end"] == len(TRACE) - 1
    for a, b in zip(ms, ms[1:]):
        assert b["fine_start"] == a["fine_end"] + 1, "ranges must not gap or overlap"
    assert sum(m["fine"] for m in ms) == len(TRACE)


def test_heuristic_segment_matches_segment_when_offline():
    """segment() offline must equal the raw heuristic segmentation."""
    orig = M.llm_available
    M.llm_available = lambda: False
    try:
        assert M.segment(TRACE, {}) == _heuristic_segment(TRACE, {})
    finally:
        M.llm_available = orig


def test_render_step_includes_key_signals():
    line = render_step(3, TRACE[3])  # hotkey cmd+t
    assert line.startswith("3 hotkey")
    assert "cmd+t" in line
    assert "research GitHub" in line


def test_edge_recording_builds_branch_point():
    """Two runs that diverge after a shared node create a branch point."""
    with tempfile.TemporaryDirectory() as d:
        store = TaskGraphStore(path=os.path.join(d, "g.json"))
        graph = TaskGraph(task_key="T", routine_id="T")

        def key(kind, label, value=None):
            return milestone_key(kind, value, label)

        kA = key("open", "Open")
        kB = key("submit", "Sign in")
        kC = key("scan", "Dashboard loaded")
        kD = key("verify", "Sign-in rejected")

        # Run 1: A -> B -> C
        for kind, label in [("open", "Open"), ("submit", "Sign in"), ("scan", "Dashboard loaded")]:
            store.record_milestone(graph, kind, label, None, 1, "completed", "run1")
        store.record_edge(graph, kA, kB, "run1")
        store.record_edge(graph, kB, kC, "run1")

        # Run 2: A -> B -> D (diverges at B)
        store.record_milestone(graph, "verify", "Sign-in rejected", None, 1, "completed", "run2")
        store.record_edge(graph, kA, kB, "run2")
        store.record_edge(graph, kB, kD, "run2")

        out = [e for e in graph.edges if e.from_key == kB]
        assert {e.to_key for e in out} == {kC, kD}, "B should branch to both C and D"
        shared = next(e for e in graph.edges if e.from_key == kA and e.to_key == kB)
        assert shared.times_seen == 2, "the shared A->B edge should be reinforced across runs"


def test_self_loops_and_empty_keys_are_ignored():
    graph = TaskGraph(task_key="T", routine_id="T")
    store = TaskGraphStore()
    store.record_edge(graph, "x", "x", "r")   # self-loop
    store.record_edge(graph, "", "y", "r")    # empty source
    store.record_edge(graph, "y", "", "r")    # empty target
    assert graph.edges == []


def test_serialize_roundtrip_preserves_edges():
    graph = TaskGraph(task_key="T", routine_id="T")
    store = TaskGraphStore()
    store.record_milestone(graph, "open", "Open", None, 1, "completed", "r")
    store.record_milestone(graph, "submit", "Submit", None, 1, "completed", "r")
    store.record_edge(graph, milestone_key("open", None, "Open"),
                      milestone_key("submit", None, "Submit"), "r")
    restored = _deserialize(_serialize(graph))
    assert len(restored.edges) == 1
    assert restored.edges[0].from_key == graph.edges[0].from_key
    assert restored.edges[0].to_key == graph.edges[0].to_key
    assert restored.edges[0].times_seen == 1


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
