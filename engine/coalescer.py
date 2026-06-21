"""
Async coalescer — the COLD PATH of crystallization.

The engine finishes a run, writes the RunTrace to the durable journal, and hands it
here. A single daemon worker thread then does the slow work (LLM milestone
segmentation, graph merge, edge reconciliation, persistence) OFF the hot path, so
crystallization never slows execution.

Robustness:
  - A coalesce failure NEVER affects the run; the journal is retained for retry.
  - One worker = ordered, single-flight graph writes (no write races).
  - coalesce_now()/recoalesce() allow synchronous re-crystallization from the journal
    (tests, or re-running with a better model later).
"""
import queue
import threading

from shepherd_types import RunTrace
from engine.task_graph import TaskGraphStore, milestone_key
from engine.milestones import segment
from engine import trace_journal
from dashboard.events import event_bus

_q: "queue.Queue[RunTrace]" = queue.Queue()
_store = TaskGraphStore()
_started = False
_lock = threading.Lock()


def _ensure_worker() -> None:
    global _started
    with _lock:
        if _started:
            return
        threading.Thread(target=_loop, name="coalescer", daemon=True).start()
        _started = True


def submit(trace: RunTrace) -> None:
    """
    Called by the engine at the run boundary. Cheap: durable journal write + enqueue,
    then returns immediately. The expensive segmentation runs in the worker.
    """
    try:
        trace_journal.write(trace)   # durable backup — run is already done, so off the click path
    except Exception as e:
        print(f"[coalescer] journal write failed for {trace.run_id} (non-fatal): {e}")
    _ensure_worker()
    _q.put(trace)


def coalesce_now(trace: RunTrace) -> None:
    """Synchronous coalesce — for tests and re-crystallization."""
    _coalesce(trace)


def recoalesce(run_id: str) -> None:
    """Re-run coalescing for a journaled run (e.g. after a model/prompt change)."""
    _coalesce(trace_journal.read(run_id))


def _loop() -> None:
    while True:
        trace = _q.get()
        try:
            _coalesce(trace)
        except Exception as e:
            print(f"[coalescer] {trace.run_id} failed (journal retained for retry): {e}")
        finally:
            _q.task_done()


def _coalesce(trace: RunTrace) -> None:
    """Segment the executed trace into milestones and merge into the task graph.

    (CREATE mode. EDIT-mode patching of an existing workflow + baking taught
    procedures from trace.interventions is the next phase.)
    """
    graph = _store.load(trace.routine_id, trace.variables)
    was_known = _store.is_known(graph)
    prior_labels = [n.label for n in graph.nodes]

    executed_ms = segment(trace.executed, trace.variables, prior_labels=prior_labels)

    # Dedupe within this run so times_seen counts runs, not intra-run repeats.
    unique_ms: list[dict] = []
    by_key: dict[str, dict] = {}
    for m in executed_ms:
        key = milestone_key(m["kind"], m["value"], m["label"])
        if key in by_key:
            by_key[key]["fine"] += m["fine"]
        else:
            by_key[key] = dict(m)
            unique_ms.append(by_key[key])

    appended = 0
    for m in unique_ms:
        kind, _node = _store.record_milestone(
            graph, m["kind"], m["label"], m["value"], m["fine"], trace.status, trace.run_id)
        if kind == "appended" and was_known:
            appended += 1

    # Link consecutive milestones (in executed order) into the workflow DAG.
    ordered_keys = [
        milestone_key(m["kind"], m["value"], m["label"]) for m in executed_ms
    ]
    for from_key, to_key in zip(ordered_keys, ordered_keys[1:]):
        _store.record_edge(graph, from_key, to_key, trace.run_id)

    _store.save(graph, intent_text="", variables=trace.variables, run_id=trace.run_id)
    event_bus.emit("task.graph.saved", {
        "run_id":     trace.run_id,
        "routine_id": trace.routine_id,
        "run_count":  graph.run_count,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "appended":   appended,
        "milestones": [m["label"] for m in unique_ms],
    })
