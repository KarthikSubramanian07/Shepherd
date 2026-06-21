"""
Shared promote helper — the ONE place both relay_client._promote_graph()
and dashboard /api/task-graphs/{task_key}/promote delegate to.

Derives name + intent_patterns from the graph's stored intents (populated by
TaskGraphStore.save with real NL intent text), calls WorkflowStore.promote(),
emits the task.graph.promoted event, and fires an async background task to
generate a human-readable title/description via the LLM.
"""
from __future__ import annotations

from typing import Optional

from shepherd_types import Workflow


def _slugify(text: str) -> str:
    s = "".join(c if c.isalnum() else "_" for c in (text or "").lower()).strip("_")
    return s or "goal"


def workflow_id_for(task_key: str) -> str:
    """Raw (non-generalized) workflow id for a task key — kept for back-compat."""
    slug = task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    return f"WF_{slug.upper()[:40]}"


def general_identity(graph, task_key: str) -> tuple[str, str]:
    """Return (workflow_id, name) for a graph, GENERALIZED.

    A specific goal like "draft a gmail message about plastic" collapses to the
    reusable workflow "draft a gmail message" — so every topic variant promotes
    to ONE general workflow (matching the generalized task-graph keying). Uses
    the graph's real NL intent when present, else the task-key slug, and never
    raises (falls back to the un-generalized source).
    """
    from engine.generalize import generalize_goal

    source = (graph.intents[0] if graph.intents
              else task_key.replace("AUTONOMOUS::", "").replace("_", " "))
    try:
        general = (generalize_goal(source) or source).strip()
    except Exception:
        general = source.strip()
    general = general or source.strip() or task_key
    return f"WF_{_slugify(general).upper()[:40]}", general[:60]


def promote_graph(
    task_key: str,
    graph_store_path: Optional[str] = None,
    wf_store_path: Optional[str] = None,
    *,
    skip_if_unchanged: bool = False,
) -> Optional[Workflow]:
    """Promote a crystallized task graph into a (general) dispatchable workflow.

    Returns the workflow on success, None if the graph isn't ready.
    `skip_if_unchanged` (used by auto-promotion): return None without
    re-promoting when the general workflow already exists and the graph hasn't
    grown — avoids a version bump on every repeat run.
    Fires the async LLM describe task (fire-and-forget) after promotion.
    """
    from engine.task_graph import TaskGraphStore, _PATH as _GRAPH_PATH
    from engine.workflow_store import WorkflowStore, _PATH as _WF_PATH

    gs_path = graph_store_path or _GRAPH_PATH
    ws_path = wf_store_path or _WF_PATH

    store = TaskGraphStore(gs_path)
    graph = store.load(task_key, {})
    if graph.run_count == 0 and not graph.nodes:
        return None

    workflow_id, name = general_identity(graph, task_key)

    wf_store = WorkflowStore(ws_path)
    if skip_if_unchanged:
        existing = wf_store.get(workflow_id)
        if existing and len(existing.nodes) >= len(graph.nodes):
            return None  # already current — don't churn the version

    intent_patterns = list(dict.fromkeys(graph.intents)) if graph.intents else [name]

    wf = wf_store.promote(graph, workflow_id, name, intent_patterns)
    _after_promote(wf, task_key, ws_path)
    return wf


def _after_promote(wf: Workflow, task_key: str, ws_path: str) -> None:
    """Emit the promoted event + fire the async LLM describe (fire-and-forget)."""
    from dashboard.events import event_bus
    event_bus.emit("task.graph.promoted", {
        "task_key":        task_key,
        "workflow_id":     wf.id,
        "name":            wf.name,
        "version":         wf.version,
        "node_count":      len(wf.nodes),
        "intent_patterns": wf.intent_patterns,
    })
    from engine.workflow_describe import generate_description
    generate_description(wf.id, ws_path)


def backfill_workflows(
    min_nodes: int = 2,
    graph_store_path: Optional[str] = None,
    wf_store_path: Optional[str] = None,
) -> list[str]:
    """Promote qualifying task graphs into GENERAL workflows.

    Graphs are grouped by their generalized identity, so many topic-specific
    legacy graphs ("…about dolphins", "…about mosquitos") collapse into ONE
    workflow ("draft a mail app email"); the richest graph in a group is the
    representative and the group's intents become its matching patterns.
    Idempotent: already-promoted graphs (and groups whose workflow exists) are
    skipped, so repeat startups don't churn versions. Returns the ids promoted.
    """
    from engine.task_graph import TaskGraphStore, _PATH as _GRAPH_PATH, _deserialize
    from engine.workflow_store import WorkflowStore, _PATH as _WF_PATH

    gs_path = graph_store_path or _GRAPH_PATH
    ws_path = wf_store_path or _WF_PATH

    store = TaskGraphStore(gs_path)
    wf_store = WorkflowStore(ws_path)
    existing_ids = {w.id for w in wf_store.list()}
    promoted_sources = {w.from_graph for w in wf_store.list() if w.from_graph}

    # Group qualifying, not-yet-promoted graphs by their GENERAL workflow id.
    groups: dict[str, dict] = {}
    for task_key, raw in store.all_graphs().items():
        graph = _deserialize(raw)
        if len(graph.nodes) < min_nodes:
            continue
        if task_key in promoted_sources:
            continue  # cheap skip — no need to generalize an already-promoted graph
        try:
            wid, name = general_identity(graph, task_key)
        except Exception as e:
            print(f"[promote] backfill skipped {task_key} (non-fatal): {e}")
            continue
        if wid in existing_ids:
            continue  # the general workflow already exists
        g = groups.setdefault(wid, {"name": name, "members": []})
        g["members"].append((task_key, graph))

    promoted: list[str] = []
    for wid, g in groups.items():
        members = sorted(g["members"], key=lambda m: len(m[1].nodes), reverse=True)
        rep_key, rep_graph = members[0]
        patterns = list(dict.fromkeys(
            p for _k, gr in members for p in (gr.intents or [])
        )) or [g["name"]]
        try:
            wf = wf_store.promote(rep_graph, wid, g["name"], patterns)
            _after_promote(wf, rep_key, ws_path)
            promoted.append(wf.id)
            if len(members) > 1:
                print(f"[promote] merged {len(members)} graphs -> {wid} ({g['name']!r})")
        except Exception as e:
            print(f"[promote] backfill skipped {wid} (non-fatal): {e}")
    if promoted:
        print(f"[promote] backfilled {len(promoted)} general workflow(s)")
    return promoted
