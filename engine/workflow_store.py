"""
Workflow store — the DISPATCHABLE artifact layer (design §1, phase 4).

A Workflow is a TaskGraph that has been *promoted*: named, versioned, given
`intent_patterns` the router matches against, and treated as the unit the
milestone executor traverses. Routines stay exact demos; TaskGraphs stay
passively observed; Workflows are the opinionated, form-agnostic thing dispatch
points at.

Persisted as JSON in data/workflows.json (boundary writes only — never on the
click path). Node/edge (de)serialization is shared with engine.task_graph so the
taught layer (procedure / conditionals / source) round-trips identically.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from typing import Optional

from shepherd_types import Workflow, TaskGraph, TaskGraphNode, TaskGraphEdge
from engine.task_graph import (
    serialize_node, serialize_edge, _node_from_raw, edge_from_raw,
)

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "workflows.json")

# Serializes concurrent writers (e.g. several async workflow_describe tasks
# finishing at once) so they can't race on the temp file / clobber each other.
_SAVE_LOCK = threading.Lock()


def derive_start_key(nodes: list[TaskGraphNode], edges: list[TaskGraphEdge]) -> str:
    """Entry node = the first node with no *observed* incoming edge (conditional/
    taught edges don't count as the normal entry). Falls back to the first node."""
    if not nodes:
        return ""
    incoming = {e.to_key for e in edges if not e.condition}
    for n in nodes:
        if n.key not in incoming:
            return n.key
    return nodes[0].key


def successors(workflow: Workflow, node_key: str) -> list[tuple[TaskGraphEdge, TaskGraphNode]]:
    """Outgoing transitions from a node as (edge, target_node) pairs, common path
    first (higher times_seen), so the executor previews the likely route first."""
    by_key = {n.key: n for n in workflow.nodes}
    out = [
        (e, by_key[e.to_key])
        for e in workflow.edges
        if e.from_key == node_key and e.to_key in by_key
    ]
    out.sort(key=lambda ev: ev[0].times_seen, reverse=True)
    return out


def node_by_key(workflow: Workflow, key: str) -> Optional[TaskGraphNode]:
    for n in workflow.nodes:
        if n.key == key:
            return n
    return None


class WorkflowStore:
    def __init__(self, path: str = _PATH) -> None:
        self._path = path

    # ── persistence ──────────────────────────────────────────────────────────
    def _load_all(self) -> dict:
        try:
            with open(self._path) as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_all(self, data: dict) -> None:
        # Atomic + concurrency-safe: write to a UNIQUE temp file in the same dir,
        # then os.replace. A shared "<path>.tmp" name races when several writers
        # run at once (one's replace consumes the temp before another's), which is
        # the "No such file or directory: workflows.json.tmp" error. The lock
        # also prevents last-writer-wins clobbering of concurrent updates.
        d = os.path.dirname(self._path)
        os.makedirs(d, exist_ok=True)
        with _SAVE_LOCK:
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".workflows.", suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, self._path)
            except Exception:
                if os.path.exists(tmp):
                    os.remove(tmp)
                raise

    def list(self) -> list[Workflow]:
        return [_deserialize(raw) for raw in self._load_all().values()]

    def get(self, workflow_id: str) -> Optional[Workflow]:
        raw = self._load_all().get(workflow_id)
        return _deserialize(raw) if raw else None

    def save(self, workflow: Workflow) -> None:
        workflow.updated_at = time.time()
        if not workflow.created_at:
            workflow.created_at = workflow.updated_at
        if not workflow.start_key:
            workflow.start_key = derive_start_key(workflow.nodes, workflow.edges)
        data = self._load_all()
        data[workflow.id] = _serialize(workflow)
        self._save_all(data)

    # ── promotion: TaskGraph → Workflow ──────────────────────────────────────
    def promote(
        self,
        graph: TaskGraph,
        workflow_id: str,
        name: str,
        intent_patterns: list[str],
        params: Optional[list[str]] = None,
    ) -> Workflow:
        """Promote an observed/taught TaskGraph into a dispatchable Workflow.

        Nodes/edges are copied as-is (preserving taught procedure/conditionals);
        re-promoting an existing id bumps `version` so dispatch can pin a known
        revision. The node list order is the graph's, with the derived start node
        first so traversal begins at the entry milestone."""
        existing = self.get(workflow_id)
        version = (existing.version + 1) if existing else 1
        start = derive_start_key(graph.nodes, graph.edges)
        nodes = sorted(graph.nodes, key=lambda n: 0 if n.key == start else 1)
        wf = Workflow(
            id=workflow_id,
            name=name,
            intent_patterns=intent_patterns,
            params=params or sorted(graph.variables.keys()),
            nodes=nodes,
            edges=list(graph.edges),
            version=version,
            from_graph=graph.task_key,
            start_key=start,
            created_at=existing.created_at if existing else 0.0,
        )
        self.save(wf)
        return wf


def _serialize(w: Workflow) -> dict:
    return {
        "id":              w.id,
        "name":            w.name,
        "description":     w.description,
        "intent_patterns": w.intent_patterns,
        "params":          w.params,
        "version":         w.version,
        "from_graph":      w.from_graph,
        "start_key":       w.start_key,
        "created_at":      w.created_at,
        "updated_at":      w.updated_at,
        "nodes":           [serialize_node(n) for n in w.nodes],
        "edges":           [serialize_edge(e) for e in w.edges],
    }


def _deserialize(raw: dict) -> Workflow:
    return Workflow(
        id=raw["id"],
        name=raw.get("name", raw["id"]),
        description=raw.get("description", ""),
        intent_patterns=raw.get("intent_patterns", []),
        params=raw.get("params", []),
        version=raw.get("version", 1),
        from_graph=raw.get("from_graph", ""),
        start_key=raw.get("start_key", ""),
        created_at=raw.get("created_at", 0.0),
        updated_at=raw.get("updated_at", 0.0),
        nodes=[_node_from_raw(n) for n in raw.get("nodes", [])],
        edges=[edge_from_raw(e) for e in raw.get("edges", [])],
    )
