"""
EDIT-mode coalescing — the teaching loop's "bake" step.

When the agent traces an EXISTING workflow and a human resolves a block or
deviation with the `save_as_rule` flag, this turns those interventions into a
PATCH (ops referencing existing node keys) and applies it. The workflow thus
self-improves WITHOUT re-recording, and node keys stay stable — we patch, never
rebuild, so the graph doesn't churn (design §4).

Two layers, mirroring engine/milestones.py:
  • build_patch()     — asks the LLM for a richer patch (add taught research
    nodes, set procedures, add branches). Provider-agnostic via engine/llm.py.
  • heuristic_patch() — deterministic fallback used when there is no LLM key or
    the call fails: every save_as_rule intervention becomes an `add_conditional`
    op on the node it attaches to; `one_off` → `noop` (journal only).
apply_patch() executes the ops against the graph via TaskGraphStore.

The human's flag is the gate: only `save_as_rule` bakes; `one_off` never touches
the workflow.
"""
from __future__ import annotations

from shepherd_types import RunTrace, InterventionEvent, TaskGraph
from engine import llm
from engine.task_graph import TaskGraphStore, milestone_key

_OPS = {"add_conditional", "set_procedure", "add_node", "add_branch", "noop"}

_SYSTEM = """\
You maintain a high-level WORKFLOW (milestone graph) by emitting a PATCH that
bakes human teaching into it. You are in EDIT mode: the workflow already exists,
so you REFERENCE existing node keys and never rebuild it.

You are given: the base workflow's nodes (key — label), and a list of human
INTERVENTIONS, each tagged with the node it occurred at, the scenario, the human's
resolution instruction, and a flag.

RULES:
  - Only bake interventions flagged save_as_rule. For one_off, emit a noop.
  - Prefer add_conditional: attach an "if <when> → do <do>" clause to the node so
    next run the agent auto-resolves instead of blocking.
  - Use set_procedure when the resolution is a standard procedure for that node.
  - Use add_node only when the resolution introduces a genuinely new milestone
    (e.g. a research detour); reference where it attaches with "after".
  - Keep `when` = the scenario, `do` = the human's instruction (concise NL).

OUTPUT: ONLY a JSON array of ops, no prose. Op shapes:
  {"op":"add_conditional","node":"<key>","when":"<nl>","do":"<nl>","goto":<key|null>}
  {"op":"set_procedure","node":"<key>","procedure":"<nl>","requires":[<str>]}
  {"op":"add_node","after":"<key>","kind":"research","label":"<=6 words","condition":"<nl>","procedure":"<nl>","requires":[<str>]}
  {"op":"add_branch","from":"<key>","to":"<key>","condition":"<nl>"}
  {"op":"noop","reason":"<why>"}"""


def _scenario(iv: InterventionEvent) -> str:
    return (iv.scenario or iv.trigger or "the agent is blocked").strip()


# ── heuristic (deterministic) ───────────────────────────────────────────────────
def heuristic_patch(graph: TaskGraph, trace: RunTrace) -> list[dict]:
    """One op per intervention: save_as_rule → add_conditional, else noop."""
    ops: list[dict] = []
    for iv in trace.interventions:
        if iv.flag == "save_as_rule" and iv.instruction and iv.node_key:
            ops.append({
                "op":   "add_conditional",
                "node": iv.node_key,
                "when": _scenario(iv),
                "do":   iv.instruction.strip(),
                "goto": None,
            })
        else:
            ops.append({
                "op": "noop",
                "reason": "one_off" if iv.flag != "save_as_rule" else "no node/instruction",
            })
    return ops


# ── LLM patch (richer) ──────────────────────────────────────────────────────────
def _render_workflow(graph: TaskGraph) -> str:
    lines = [f'  key="{n.key}"  ({n.kind}) {n.label}' for n in graph.nodes]
    return ("WORKFLOW NODES (use the exact key string in ops):\n"
            + ("\n".join(lines) if lines else "  (none)"))


def _render_interventions(trace: RunTrace) -> str:
    rows = []
    for iv in trace.interventions:
        rows.append(
            f"  node={iv.node_key or '?'} flag={iv.flag} "
            f"scenario={_scenario(iv)!r} resolution={iv.instruction!r}"
        )
    return "INTERVENTIONS:\n" + ("\n".join(rows) if rows else "  (none)")


def _llm_patch(graph: TaskGraph, trace: RunTrace) -> list[dict]:
    user = f"{_render_workflow(graph)}\n\n{_render_interventions(trace)}"
    text = llm.complete(_SYSTEM, [("user", user)], prefill="[")
    raw = llm.parse_json_array(text)
    ops = [op for op in raw if isinstance(op, dict) and op.get("op") in _OPS]
    if not ops:
        raise ValueError("no valid ops in LLM patch")
    return ops


def build_patch(graph: TaskGraph, trace: RunTrace) -> list[dict]:
    """LLM patch when a key is configured; deterministic heuristic otherwise."""
    if not trace.interventions:
        return []
    if llm.available():
        try:
            return _llm_patch(graph, trace)
        except Exception as e:  # noqa: BLE001
            print(f"[workflow_edit] LLM patch failed (using heuristic): {e}")
    return heuristic_patch(graph, trace)


def _resolve_node_key(graph: TaskGraph, ref: str) -> str:
    """Map a model-provided node reference to a real graph key. Reasoning models
    often abbreviate the key (e.g. "fill" instead of "fill::::Fill details"), so
    resolve by exact key → exact label → unique kind."""
    if not ref:
        return ""
    ref = ref.strip()
    by_key = {n.key: n for n in graph.nodes}
    if ref in by_key:
        return ref
    for n in graph.nodes:
        if n.label.strip().lower() == ref.lower():
            return n.key
    same_kind = [n for n in graph.nodes if n.kind.lower() == ref.lower()]
    if len(same_kind) == 1:
        return same_kind[0].key
    return ""


# ── apply ───────────────────────────────────────────────────────────────────────
def apply_patch(store: TaskGraphStore, graph: TaskGraph, ops: list[dict],
                run_id: str) -> list[dict]:
    """Execute patch ops against the graph. Returns the ops actually applied."""
    applied: list[dict] = []
    for op in ops:
        kind = op.get("op")
        if kind == "add_conditional":
            node = _resolve_node_key(graph, op.get("node", ""))
            if store.add_conditional(graph, node, op.get("when", ""),
                                     op.get("do", ""), op.get("goto")):
                applied.append(op)
        elif kind == "set_procedure":
            node = _resolve_node_key(graph, op.get("node", ""))
            if store.set_procedure(graph, node, op.get("procedure", ""),
                                   op.get("requires")):
                applied.append(op)
        elif kind == "add_node":
            if _apply_add_node(store, graph, op, run_id):
                applied.append(op)
        elif kind == "add_branch":
            frm = _resolve_node_key(graph, op.get("from", ""))
            to = _resolve_node_key(graph, op.get("to", ""))
            if frm and to:
                store.record_edge(graph, frm, to, run_id)
                if op.get("condition"):
                    for e in graph.edges:
                        if e.from_key == frm and e.to_key == to:
                            e.condition = op["condition"]
                applied.append(op)
        # noop / unknown → skip
    return applied


def _apply_add_node(store: TaskGraphStore, graph: TaskGraph, op: dict, run_id: str) -> bool:
    label = (op.get("label") or "").strip()
    kind = (op.get("kind") or "interact").strip()
    if not label:
        return False
    key = milestone_key(kind, None, label)
    node = store.node_by_key(graph, key)
    if node is None:
        _, node = store.record_milestone(graph, kind, label, None, 0, "taught", run_id)
    node.source = "taught"
    if op.get("procedure"):
        node.procedure = op["procedure"]
    if op.get("requires"):
        node.requires = sorted(set(node.requires) | set(op["requires"]))
    after = _resolve_node_key(graph, op.get("after", ""))
    if after:
        store.record_edge(graph, after, key, run_id)
        if op.get("condition"):
            for e in graph.edges:
                if e.from_key == after and e.to_key == key:
                    e.condition = op["condition"]
    return True
