"""
Persistent task-graph memory — MILESTONE granularity.

Each task (currently keyed by its resolved routine_id) has ONE durable graph that
accumulates across runs. The graph is intentionally coarse: many fine clicks
(move / tab / wait …) collapse into a single milestone node such as
"Search: AI agent safety", "Scan results", or "Submit". A new run loads the prior
graph as a REFERENCE, executes, and APPENDS any milestone it performs that the
graph hasn't seen yet — so re-running a same/similar task shows what's already
been done at the level a human reasons about, not click-by-click.

Per-click detail is NOT stored here; the engine still feeds every individual click
to Agent S (see ShepherdExecutionEngine._live_step).

Stored as JSON in data/task_graphs.json. Read/write at routine boundaries only —
never inside the click sequence.
"""
import json
import os
import time
from typing import Optional

from shepherd_types import TaskGraph, TaskGraphNode, TaskGraphEdge, Conditional

_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "task_graphs.json")

# Action types that are sub-actions of whatever milestone is in progress — they
# never start a milestone of their own (mouse moves, field tabbing, dismissals).
_CONTINUATION = {"move", "click", "double_click"}
_URL_HINTS  = ("http://", "https://", "://", "www.", ".com", ".org", ".net", ".io", "localhost")
_SCAN_HINTS = ("page", "form", "result", "load", "search", "browser", "site", "list")


def milestone_key(kind: str, value: Optional[str], label: str) -> str:
    return f"{kind}::{(value or '').strip()}::{label}"


def _looks_like_url(text: str) -> bool:
    t = (text or "").lower()
    return any(h in t for h in _URL_HINTS)


def _host(text: str) -> str:
    t = (text or "").strip().strip("\n")
    for p in ("http://", "https://"):
        if t.startswith(p):
            t = t[len(p):]
    t = t.split("/")[0].split("?")[0].strip()
    return t or "page"


def _classify(step, variables: dict):
    """
    Map one fine step → (kind, label, value). kind=None means 'continuation':
    fold this click into the milestone already in progress.
    """
    a = step.action
    desc = (step.description or "").lower()

    if a == "open_app":
        return "open", f"Open {step.target or 'app'}", step.target

    if a == "browser":
        bs = step.browser_step or {}
        act = (bs.get("action") or "").lower()
        query = bs.get("query") or variables.get("SEARCH_QUERY")
        if act == "search" or "search" in desc:
            return "search", (f"Search: {query}" if query else "Search"), query
        return "navigate", f"Navigate to {_host(bs.get('url', ''))}", _host(bs.get("url", ""))

    if a == "type":
        txt = step.text or ""
        if _looks_like_url(txt) or "navigat" in desc or "url" in desc:
            return "navigate", f"Navigate to {_host(txt)}", _host(txt)
        if "search" in desc or "query" in desc:
            val = (txt or variables.get("SEARCH_QUERY", "")).strip()
            return "search", (f"Search: {val}" if val else "Search"), val or None
        return "fill", "Enter details", None

    if a == "hotkey":
        keys = [k.lower() for k in (step.keys or [])]
        if "return" in keys or "enter" in keys or "submit" in desc or "save" in desc:
            return "submit", "Submit", None
        if ("l" in keys and "cmd" in keys) or "url" in desc:
            return "navigate", "Navigate", None     # focusing the URL bar → part of navigate
        return None, None, None                      # tab / cmd+n / escape → continuation

    if a == "wait":
        if any(h in desc for h in _SCAN_HINTS):
            return "scan", "Scan results", None
        return None, None, None

    if a in _CONTINUATION:
        return None, None, None

    return "interact", desc or a, None


def summarize(steps, variables: dict):
    """
    Collapse a fine step sequence into coarse milestones.
    Returns (milestones, mapping) where milestones is a list of dicts
    {kind, label, value, fine} and mapping is fine-step-index → milestone-index.
    """
    milestones: list[dict] = []
    mapping: dict[int, int] = {}
    cur: Optional[dict] = None

    for i, step in enumerate(steps):
        kind, label, value = _classify(step, variables)

        if kind is None:                                   # continuation click
            if cur is None:
                cur = {"kind": "interact", "label": "Interact", "value": None, "fine": 0}
                milestones.append(cur)
            cur["fine"] += 1
            mapping[i] = len(milestones) - 1
            continue

        if cur is not None and cur["kind"] == kind:         # extend same milestone
            cur["fine"] += 1
            if value and not cur["value"]:                  # upgrade to the more specific label
                cur["value"], cur["label"] = value, label
        else:                                               # new milestone
            cur = {"kind": kind, "label": label, "value": value, "fine": 1}
            milestones.append(cur)
        mapping[i] = len(milestones) - 1

    return milestones, mapping


class TaskGraphStore:
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
        tmp = self._path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self._path)  # atomic — never leaves a half-written graph

    # ── lookup ─────────────────────────────────────────────────────────────────
    def task_key(self, routine_id: str, variables: dict) -> str:
        # Same/similar task == same resolved routine.
        return routine_id

    def load(self, routine_id: str, variables: dict, intent_text: str = "") -> TaskGraph:
        key = self.task_key(routine_id, variables)
        raw = self._load_all().get(key)
        if raw:
            return _deserialize(raw)
        return TaskGraph(task_key=key, routine_id=routine_id, created_at=time.time())

    @staticmethod
    def is_known(graph: TaskGraph) -> bool:
        return graph.run_count > 0

    @staticmethod
    def node_by_key(graph: TaskGraph, key: str) -> Optional[TaskGraphNode]:
        for n in graph.nodes:
            if n.key == key:
                return n
        return None

    # ── mutation (boundary-only) ────────────────────────────────────────────────
    def record_milestone(self, graph: TaskGraph, kind: str, label: str, value,
                         fine_steps: int, status: str, run_id: str) -> tuple[str, TaskGraphNode]:
        """Merge a milestone into the graph. Returns ('matched'|'appended', node)."""
        key = milestone_key(kind, value, label)
        existing = self.node_by_key(graph, key)
        if existing is not None:
            existing.times_seen += 1
            existing.last_status = status
            existing.fine_steps  = fine_steps
            existing.last_run_id = run_id
            return "matched", existing
        node = TaskGraphNode(
            key=key, kind=kind, label=label, value=value,
            times_seen=1, last_status=status, fine_steps=fine_steps,
            first_run_id=run_id, last_run_id=run_id,
            instruction=label, source="observed",
        )
        graph.nodes.append(node)
        return "appended", node

    def record_edge(self, graph: TaskGraph, from_key: str, to_key: str, run_id: str) -> None:
        """Merge a directed transition from_key -> to_key. Across runs this builds
        the workflow DAG; a node with multiple outgoing edges is a branch point."""
        if not from_key or not to_key or from_key == to_key:
            return
        for e in graph.edges:
            if e.from_key == from_key and e.to_key == to_key:
                e.times_seen += 1
                e.last_run_id = run_id
                return
        graph.edges.append(
            TaskGraphEdge(from_key=from_key, to_key=to_key, times_seen=1, last_run_id=run_id)
        )

    # ── teaching loop: bake taught knowledge into a node (EDIT mode) ─────────────
    def set_procedure(self, graph: TaskGraph, node_key: str, procedure: str,
                      requires: Optional[list[str]] = None) -> bool:
        """Attach/replace a node's standard procedure. Returns True if applied."""
        node = self.node_by_key(graph, node_key)
        if node is None or not procedure:
            return False
        node.procedure = procedure
        node.source = "taught"
        if requires:
            node.requires = sorted(set(node.requires) | set(requires))
        return True

    def add_conditional(self, graph: TaskGraph, node_key: str, when: str, do: str,
                        goto: Optional[str] = None) -> bool:
        """Bake a conditional clause (if <when> → do <do>) onto a node. Idempotent
        on (when, do) so re-coalescing the same run never duplicates a clause."""
        node = self.node_by_key(graph, node_key)
        if node is None or not (when and do):
            return False
        for c in node.conditionals:
            if c.when.strip().lower() == when.strip().lower() and \
               c.do.strip().lower() == do.strip().lower():
                return False
        node.conditionals.append(Conditional(when=when, do=do, goto=goto, source="taught"))
        node.source = "taught"
        return True

    def save(self, graph: TaskGraph, intent_text: str, variables: dict, run_id: str) -> None:
        graph.run_count += 1
        graph.updated_at = time.time()
        graph.last_run_id = run_id
        if intent_text and intent_text not in graph.intents:
            graph.intents = (graph.intents + [intent_text])[-10:]
        if variables:
            graph.variables = dict(variables)
        try:
            all_graphs = self._load_all()
            all_graphs[graph.task_key] = _serialize(graph)
            self._save_all(all_graphs)
        except Exception as e:
            print(f"[task_graph] save failed (non-fatal): {e}")


def serialize_node(n: TaskGraphNode) -> dict:
    """Serialize one milestone node. Shared by TaskGraph + Workflow persistence."""
    return {
        "key":          n.key,
        "kind":         n.kind,
        "label":        n.label,
        "value":        n.value,
        "times_seen":   n.times_seen,
        "last_status":  n.last_status,
        "fine_steps":   n.fine_steps,
        "first_run_id": n.first_run_id,
        "last_run_id":  n.last_run_id,
        "instruction":  n.instruction,
        "requires":     n.requires,
        "conditionals": [
            {"when": c.when, "do": c.do, "goto": c.goto, "source": c.source}
            for c in n.conditionals
        ],
        "procedure":    n.procedure,
        "optional":     n.optional,
        "source":       n.source,
    }


def serialize_edge(e: TaskGraphEdge) -> dict:
    """Serialize one transition edge. Shared by TaskGraph + Workflow persistence."""
    return {
        "from":        e.from_key,
        "to":          e.to_key,
        "times_seen":  e.times_seen,
        "last_run_id": e.last_run_id,
        "condition":   e.condition,
    }


def edge_from_raw(e: dict) -> TaskGraphEdge:
    return TaskGraphEdge(
        from_key=e["from"], to_key=e["to"],
        times_seen=e.get("times_seen", 0), last_run_id=e.get("last_run_id", ""),
        condition=e.get("condition"),
    )


def _serialize(g: TaskGraph) -> dict:
    return {
        "task_key":    g.task_key,
        "routine_id":  g.routine_id,
        "run_count":   g.run_count,
        "intents":     g.intents,
        "variables":   g.variables,
        "created_at":  g.created_at,
        "updated_at":  g.updated_at,
        "last_run_id": g.last_run_id,
        "edges": [serialize_edge(e) for e in g.edges],
        "nodes": [serialize_node(n) for n in g.nodes],
    }


def _deserialize(raw: dict) -> TaskGraph:
    return TaskGraph(
        task_key=raw["task_key"],
        routine_id=raw["routine_id"],
        run_count=raw.get("run_count", 0),
        intents=raw.get("intents", []),
        variables=raw.get("variables", {}),
        created_at=raw.get("created_at", 0.0),
        updated_at=raw.get("updated_at", 0.0),
        last_run_id=raw.get("last_run_id", ""),
        nodes=[_node_from_raw(n) for n in raw.get("nodes", [])],
        edges=[edge_from_raw(e) for e in raw.get("edges", [])],
    )


def _node_from_raw(n: dict) -> TaskGraphNode:
    """Rehydrate a node, tolerating graphs persisted before the taught/workflow
    fields existed (they default cleanly)."""
    return TaskGraphNode(
        key=n["key"], kind=n["kind"], label=n["label"], value=n.get("value"),
        times_seen=n.get("times_seen", 0), last_status=n.get("last_status"),
        fine_steps=n.get("fine_steps", 0),
        first_run_id=n.get("first_run_id", ""), last_run_id=n.get("last_run_id", ""),
        instruction=n.get("instruction") or n["label"],
        requires=n.get("requires", []),
        conditionals=[
            Conditional(when=c["when"], do=c["do"], goto=c.get("goto"),
                        source=c.get("source", "taught"))
            for c in n.get("conditionals", [])
        ],
        procedure=n.get("procedure"),
        optional=n.get("optional", False),
        source=n.get("source", "observed"),
    )
