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
from engine.task_graph import TaskGraphStore, milestone_key, compress_to_correct_path
from engine.milestones import segment
from engine import trace_journal, workflow_edit
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

    CREATE: always merge the observed milestones + edges (builds/reinforces the graph).
    EDIT:   if the workflow was already known and the run carried human teaching
            (interventions), bake a patch on top — add conditional clauses / taught
            procedures — so the workflow self-improves without re-recording.
    """
    graph = _store.load(trace.routine_id, trace.variables)
    was_known = _store.is_known(graph)
    prior_labels = [n.label for n in graph.nodes]

    executed_ms = segment(trace.executed, trace.variables, prior_labels=prior_labels)

    # Compress wrong turns into the milestone they backed out to: the graph stores
    # the canonical FORWARD path (each node the next correct step), not the
    # fumbling. This also dedupes within the run — a key appears once — so
    # times_seen counts runs, not intra-run repeats.
    path_ms = compress_to_correct_path(executed_ms)
    _fill_intervention_node_keys(trace, path_ms)

    appended = 0
    for m in path_ms:
        kind, _node = _store.record_milestone(
            graph, m["kind"], m["label"], m["value"], m["fine"], trace.status, trace.run_id,
            detail=m.get("detail", ""), mistakes=m.get("mistakes"))
        if kind == "appended" and was_known:
            appended += 1

    # Link consecutive forward milestones into the workflow DAG. A shared node
    # whose successor differs across runs becomes a genuine branch point; a
    # matching path reinforces the existing edge.
    ordered_keys = [
        milestone_key(m["kind"], m["value"], m["label"]) for m in path_ms
    ]
    for from_key, to_key in zip(ordered_keys, ordered_keys[1:]):
        _store.record_edge(graph, from_key, to_key, trace.run_id)

    # ── EDIT mode: bake human teaching into the workflow (teaching loop) ─────────
    applied_ops: list[dict] = []
    if trace.interventions:
        patch = workflow_edit.build_patch(graph, trace)
        applied_ops = workflow_edit.apply_patch(_store, graph, patch, trace.run_id)

    _store.save(graph, intent_text=trace.intent_text, variables=trace.variables, run_id=trace.run_id)

    # Auto-promote into a dispatchable workflow so the crystallized graph shows
    # up in the Workflows page (and is router-matchable) without a manual bake-out.
    # Skipped when unchanged so repeat runs don't churn the workflow version.
    _maybe_auto_promote(trace.routine_id, graph)

    event_bus.emit("task.graph.saved", {
        "run_id":     trace.run_id,
        "routine_id": trace.routine_id,
        "run_count":  graph.run_count,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "appended":   appended,
        "compressed": len(executed_ms) - len(path_ms),
        "milestones": [m["label"] for m in path_ms],
        "mode":       "edit" if (was_known and trace.interventions) else "create",
        "baked_ops":  applied_ops,
    })


def _maybe_auto_promote(task_key: str, graph) -> None:
    """Promote the freshly-saved graph into a workflow (config-gated, best-effort)."""
    from config import AUTO_PROMOTE_WORKFLOWS, AUTO_PROMOTE_MIN_NODES
    if not AUTO_PROMOTE_WORKFLOWS or len(graph.nodes) < AUTO_PROMOTE_MIN_NODES:
        return
    try:
        from engine.workflow_promote import promote_graph
        wf = promote_graph(task_key, skip_if_unchanged=True)
        if wf is not None:
            print(f"[coalescer] auto-promoted {task_key} -> workflow {wf.id} (v{wf.version})")
    except Exception as e:
        print(f"[coalescer] auto-promote skipped for {task_key} (non-fatal): {e}")


def _fill_intervention_node_keys(trace: RunTrace, executed_ms: list[dict]) -> None:
    """
    Best-effort: for any intervention without a node_key, attach it to the
    milestone whose fine-step range covers its step_index. The engine normally
    sets node_key directly; this covers journaled/replayed traces.
    """
    for iv in trace.interventions:
        if iv.node_key:
            continue
        for m in executed_ms:
            if m["fine_start"] <= iv.step_index <= m["fine_end"]:
                iv.node_key = milestone_key(m["kind"], m["value"], m["label"])
                break
