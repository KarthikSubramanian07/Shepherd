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


def promote_graph(
    task_key: str,
    graph_store_path: Optional[str] = None,
    wf_store_path: Optional[str] = None,
) -> Optional[Workflow]:
    """Promote a crystallized task graph into a dispatchable workflow.

    Returns the workflow on success, None if the graph isn't ready.
    Fires the async LLM describe task (fire-and-forget) after promotion.
    """
    from engine.task_graph import TaskGraphStore, _PATH as _GRAPH_PATH
    from engine.workflow_store import WorkflowStore, _PATH as _WF_PATH
    from dashboard.events import event_bus

    gs_path = graph_store_path or _GRAPH_PATH
    ws_path = wf_store_path or _WF_PATH

    store = TaskGraphStore(gs_path)
    graph = store.load(task_key, {})
    if graph.run_count == 0 and not graph.nodes:
        return None

    raw_name = graph.intents[0] if graph.intents else task_key.replace("AUTONOMOUS::", "")
    name = raw_name.strip()[:60]
    intent_patterns = list(graph.intents) if graph.intents else [raw_name]

    slug = task_key.replace("AUTONOMOUS::", "").replace(" ", "_")
    workflow_id = f"WF_{slug.upper()[:40]}"

    wf_store = WorkflowStore(ws_path)
    wf = wf_store.promote(graph, workflow_id, name, intent_patterns)

    event_bus.emit("task.graph.promoted", {
        "task_key":        task_key,
        "workflow_id":     wf.id,
        "name":            wf.name,
        "version":         wf.version,
        "node_count":      len(wf.nodes),
        "intent_patterns": wf.intent_patterns,
    })

    # Fire-and-forget: enrich with LLM-generated title/description/patterns.
    from engine.workflow_describe import generate_description
    generate_description(wf.id, ws_path)

    return wf
