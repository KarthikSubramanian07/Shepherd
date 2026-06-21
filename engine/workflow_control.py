"""
Workflow control channel — the Control Hub's hook into a live traversal.

Mirrors engine/approvals.py but for the milestone executor. The Control Hub (or a
CLI operator) can, at any milestone boundary:
  • PAUSE — the executor waits at the next milestone for a human directive.
  • INTERVENE — submit a directive that steers a milestone: inject an instruction,
    force a branch (trigger the conditional case), or halt. A directive may target
    a specific node (`target_node`) so "when you reach the projects step, do X" is
    applied at exactly that milestone regardless of timing.
  • REMEMBER — flag the directive `save_as_rule` so it is baked into the workflow
    (via the existing teaching loop) and becomes automatic next time.

`review(turn)` is the gate handed to WorkflowExecutor. It is non-blocking unless a
pause was requested, so an unattended run proceeds autonomously; an operator can
pause and steer in real time.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from shepherd_types import Workflow, TaskGraph, RunTrace, InterventionEvent
from engine.workflow_executor import Intervention

_cv = threading.Condition()
_pending: list[Intervention] = []
_pause = threading.Event()

# How long the executor blocks at a milestone once paused, awaiting a directive.
PAUSE_TIMEOUT_S = 120.0

# ── end-of-run persistence gate ───────────────────────────────────────────────
# After a run bakes `remember` steers into the workflow, the operator decides what
# to do with the result: persist into the reference workflow (default), save it as
# a brand-new workflow, or discard. Unattended runs fall back to `persist` on
# timeout so crystallized judgment calls are never silently lost.
_finalize_cv = threading.Condition()
_finalize_decision: Optional[dict] = None
FINALIZE_TIMEOUT_S = 90.0


# ── Control Hub side ──────────────────────────────────────────────────────────────
def request_pause() -> None:
    _pause.set()


def clear_pause() -> None:
    _pause.clear()


def is_paused() -> bool:
    return _pause.is_set()


def submit_intervention(
    instruction: str = "",
    next_key: str = "",
    scenario: str = "",
    remember: bool = False,
    decision: str = "override",
    target_node: str = "",
) -> None:
    """Queue a human directive. If `target_node` is set it is applied only when the
    traversal reaches that node; otherwise it applies at the next milestone."""
    iv = Intervention(
        decision=decision, instruction=instruction.strip(), next=next_key.strip(),
        scenario=scenario.strip(), remember=bool(remember), target_node=target_node.strip(),
    )
    with _cv:
        _pending.append(iv)
        _cv.notify_all()


def submit_finalize(decision: str = "persist", new_id: str = "", name: str = "") -> None:
    """Control Hub side: the operator's end-of-run choice for the baked workflow.

    decision ∈ {"persist", "save_as_new", "discard"}.
    `new_id` / `name` are only used for save_as_new (a fresh workflow id + label)."""
    global _finalize_decision
    with _finalize_cv:
        _finalize_decision = {
            "decision": (decision or "persist").strip(),
            "new_id":   (new_id or "").strip(),
            "name":     (name or "").strip(),
        }
        _finalize_cv.notify_all()


def reset() -> None:
    """Drop any queued directives and clear pause (between runs / in tests)."""
    global _finalize_decision
    with _cv:
        _pending.clear()
    with _finalize_cv:
        _finalize_decision = None
    _pause.clear()


# ── executor side (the gate) ──────────────────────────────────────────────────────
def review(turn) -> Optional[Intervention]:
    """Gate checked by the executor at each milestone. Consumes a directive that
    targets this node (or is untargeted); if paused with none matching, blocks
    (bounded) until one arrives."""
    node_key = turn.node.key
    with _cv:
        iv = _take_matching(node_key)
        if iv is not None:
            _pause.clear()
            return iv
        if not _pause.is_set():
            return None
        _emit_awaiting(turn)
        deadline = time.time() + PAUSE_TIMEOUT_S
        while _pause.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _cv.wait(timeout=remaining)
            iv = _take_matching(node_key)
            if iv is not None:
                _pause.clear()
                return iv
        return _take_matching(node_key)


def _take_matching(node_key: str) -> Optional[Intervention]:
    """Pop the first pending directive that applies to this node (caller holds _cv)."""
    for i, iv in enumerate(_pending):
        if not iv.target_node or iv.target_node == node_key:
            return _pending.pop(i)
    return None


def _emit_awaiting(turn) -> None:
    try:
        from dashboard.events import event_bus
        event_bus.emit("workflow.awaiting", {
            "step_no": turn.step_no, "node_key": turn.node.key, "label": turn.node.label,
            "options": [{"key": o.key, "label": o.label, "via": o.via, "when": o.when}
                        for o in turn.options],
        })
    except Exception:
        pass


# ── teaching: bake `remember` interventions into the workflow ─────────────────────
def bake(workflow: Workflow, interventions: list[InterventionEvent], run_id: str) -> list[dict]:
    """Bake save_as_rule interventions into the workflow via the existing EDIT-mode
    patch (add_conditional / set_procedure / add_node), reusing the phase-2/3
    teaching loop over a transient TaskGraph view of the workflow. Returns the ops
    actually applied (empty when nothing was flagged to remember)."""
    from engine.task_graph import TaskGraphStore
    from engine import workflow_edit

    teach = [iv for iv in interventions if iv.flag == "save_as_rule" and iv.instruction]
    if not teach:
        return []

    graph = TaskGraph(task_key=workflow.id, routine_id=workflow.id,
                      nodes=list(workflow.nodes), edges=list(workflow.edges))
    store = TaskGraphStore()
    applied: list[dict] = []

    # Forced-branch steers carry a concrete `goto`, so bake them deterministically
    # (add_conditional + the edge) — the LLM/heuristic patch can drop the target,
    # which would leave the taught branch un-routable. Pure instruction steers
    # (no goto) go through the existing EDIT-mode patch.
    branch_ivs = [iv for iv in teach if iv.goto]
    instruction_ivs = [iv for iv in teach if not iv.goto]

    for iv in branch_ivs:
        op = {"op": "add_conditional", "node": iv.node_key,
              "when": iv.scenario or "the taught condition holds",
              "do": iv.instruction, "goto": iv.goto}
        if workflow_edit.apply_patch(store, graph, [op], run_id):
            applied.append(op)
            # record the conditional edge so the branch is a routable option
            store.record_edge(graph, iv.node_key, iv.goto, run_id)
            for e in graph.edges:
                if e.from_key == iv.node_key and e.to_key == iv.goto:
                    e.condition = iv.scenario or e.condition

    if instruction_ivs:
        trace = RunTrace(run_id=run_id, routine_id=workflow.id, interventions=instruction_ivs)
        ops = workflow_edit.build_patch(graph, trace)
        applied += workflow_edit.apply_patch(store, graph, ops, run_id)

    workflow.nodes = graph.nodes
    workflow.edges = graph.edges
    return applied


# ── end-of-run persistence gate ───────────────────────────────────────────────
def _emit_finalize(workflow: Workflow, applied: list[dict]) -> None:
    """Announce that a baked workflow is awaiting the operator's persist choice."""
    try:
        from dashboard.events import event_bus
        event_bus.emit("workflow.finalize", {
            "workflow_id":      workflow.id,
            "name":             workflow.name,
            "current_version":  workflow.version,
            "proposed_version": workflow.version + 1,
            "ops":              applied,
        })
    except Exception:
        pass


def await_finalize(workflow: Workflow, applied: list[dict]) -> dict:
    """Executor side: announce the proposed bake and block (bounded) for the
    operator's choice. Defaults to `persist` on timeout so an unattended run still
    crystallizes the judgment calls into the reference workflow."""
    global _finalize_decision
    with _finalize_cv:
        _finalize_decision = None
    _emit_finalize(workflow, applied)
    with _finalize_cv:
        deadline = time.time() + FINALIZE_TIMEOUT_S
        while _finalize_decision is None:
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            _finalize_cv.wait(timeout=remaining)
        return _finalize_decision or {"decision": "persist"}


def persist_baked(workflow: Workflow, applied: list[dict], decision: dict) -> dict:
    """Apply the operator's finalize choice to the in-memory (already-baked)
    workflow and persist accordingly. Returns an outcome dict for the event log.

      persist      → bump version + save the reference workflow (default)
      save_as_new  → save a clone under a new id at version 1 (reference untouched)
      discard      → save nothing (the bake is dropped)
    """
    import copy
    from dataclasses import replace
    from engine.workflow_store import WorkflowStore

    choice = (decision or {}).get("decision", "persist")
    store = WorkflowStore()

    if choice == "discard":
        outcome = {"action": "discarded", "workflow_id": workflow.id,
                   "version": workflow.version, "ops": applied}
    elif choice == "save_as_new":
        new_id = (decision.get("new_id") or "").strip() or f"{workflow.id}_COPY_{int(time.time())}"
        name = (decision.get("name") or "").strip() or f"{workflow.name} (copy)"
        clone = replace(
            workflow, id=new_id, name=name, version=1,
            nodes=copy.deepcopy(workflow.nodes), edges=copy.deepcopy(workflow.edges),
            from_graph=workflow.id, created_at=0.0, updated_at=0.0,
        )
        store.save(clone)
        outcome = {"action": "saved_as_new", "workflow_id": new_id,
                   "version": clone.version, "ops": applied}
    else:
        # default: persist into the reference workflow
        workflow.version += 1
        store.save(workflow)
        outcome = {"action": "persisted", "workflow_id": workflow.id,
                   "version": workflow.version, "ops": applied}

    try:
        from dashboard.events import event_bus
        event_bus.emit("workflow.finalized", outcome)
    except Exception:
        pass
    return outcome
