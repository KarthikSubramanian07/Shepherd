"""
Milestone-graph executor (design §2 / phase 5).

Traverses a promoted Workflow node-by-node instead of replaying recorded clicks.
The key property the executor guarantees is the **single-message advance**: at
every milestone the worker is handed, in ONE prompt, the node's instruction +
resolved inputs + taught procedure/conditionals **and a preview of where it can
go next** (successor milestones and conditional branches). The worker returns ONE
structured message — what it did, that the milestone is done, and the next node /
branch it chose — so there is no extra round-trip just to decide "what now". The
executor applies it and immediately moves on.

The worker is pluggable (`Worker` protocol):
  • AgentSWorker — real GUI actuation (Agent S grounds + acts per milestone).
  • LLMWorker    — headless/test reasoning over an observable environment via the
                   modular LLM layer (Gemma by default).
  • ScriptedWorker — deterministic, network-free worker for tests.

Conditions stay natural-language clauses the worker reads in-context (no separate
predicate engine), exactly as the taught layer stores them.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

from shepherd_types import Workflow, TaskGraphNode
from engine import workflow_store as WS
from engine import llm

END = "END"


# ── worker I/O ──────────────────────────────────────────────────────────────────
@dataclass
class NextOption:
    """One place the worker may go from the current milestone — surfaced in the
    SAME turn so the worker can pick it without another round-trip."""
    key: str                         # target node key (or END)
    label: str
    via: str = "edge"                # "edge" | "conditional"
    when: Optional[str] = None       # NL guard, set for conditional branches
    do: Optional[str] = None         # NL action for a conditional branch


@dataclass
class WorkerTurn:
    """Everything handed to the worker for one milestone — instruction + inputs +
    taught knowledge + the forward preview."""
    goal: str
    step_no: int
    node: TaskGraphNode
    resolved: dict[str, str]         # required inputs the profile/KB could fill
    missing: list[str]               # required inputs it could NOT fill
    options: list[NextOption]        # successors + conditional branches
    profile: dict[str, str]          # known KB at this point


@dataclass
class WorkerResult:
    """The worker's ONE returned message: actuated + marked done + chose next."""
    did: str = ""
    status: str = "done"             # "done" | "blocked"
    next: str = END                  # chosen successor node key, or END
    branch: Optional[str] = None     # `when` of a conditional taken (else None)
    extracted: dict[str, str] = field(default_factory=dict)  # new KB learned
    reason: str = ""


class Worker(Protocol):
    def act(self, turn: WorkerTurn) -> WorkerResult: ...


# ── run record ───────────────────────────────────────────────────────────────────
@dataclass
class WorkflowStepRecord:
    step_no: int
    node_key: str
    label: str
    status: str
    did: str
    branch: Optional[str]
    chose_next: str
    extracted: dict[str, str]


@dataclass
class WorkflowRun:
    workflow_id: str
    status: str                      # "completed" | "blocked" | "aborted"
    path: list[WorkflowStepRecord]
    profile: dict[str, str]
    blocked_on: Optional[str] = None
    started_at: float = 0.0
    ended_at: float = 0.0

    @property
    def visited_keys(self) -> list[str]:
        return [s.node_key for s in self.path]


# ── option assembly ───────────────────────────────────────────────────────────────
def options_for(workflow: Workflow, node: TaskGraphNode) -> list[NextOption]:
    """Forward preview from a node: its outgoing edges plus any taught conditional
    branches (deduped by target). Conditional/taught targets are flagged so the
    worker knows the NL guard to evaluate against the live screen."""
    opts: list[NextOption] = []
    seen: set[str] = set()

    for c in node.conditionals:
        if not c.goto:
            continue
        tgt = WS.node_by_key(workflow, c.goto)
        if tgt and tgt.key not in seen:
            opts.append(NextOption(key=tgt.key, label=tgt.label, via="conditional",
                                   when=c.when, do=c.do))
            seen.add(tgt.key)

    for edge, tgt in WS.successors(workflow, node.key):
        if tgt.key in seen:
            continue
        opts.append(NextOption(
            key=tgt.key, label=tgt.label,
            via="conditional" if edge.condition else "edge",
            when=edge.condition,
        ))
        seen.add(tgt.key)

    return opts


# ── executor ───────────────────────────────────────────────────────────────────
class WorkflowExecutor:
    def __init__(self, worker: Worker, event_emit=None, max_steps: int = 50) -> None:
        self._worker = worker
        self._max_steps = max_steps
        self._emit = event_emit or (lambda *_a, **_k: None)

    def run(
        self,
        workflow: Workflow,
        goal: str = "",
        params: Optional[dict[str, str]] = None,
        profile: Optional[dict[str, str]] = None,
    ) -> WorkflowRun:
        started = time.time()
        kb: dict[str, str] = {**(params or {}), **(profile or {})}
        path: list[WorkflowStepRecord] = []

        start_key = workflow.start_key or WS.derive_start_key(workflow.nodes, workflow.edges)
        cur = WS.node_by_key(workflow, start_key)
        if cur is None:
            return WorkflowRun(workflow.id, "aborted", path, kb,
                               blocked_on="no start node", started_at=started,
                               ended_at=time.time())

        self._emit("workflow.start", {
            "workflow_id": workflow.id, "name": workflow.name,
            "start": start_key, "goal": goal,
        })

        status = "completed"
        blocked_on: Optional[str] = None
        visits: dict[str, int] = {}

        for step_no in range(self._max_steps):
            visits[cur.key] = visits.get(cur.key, 0) + 1
            resolved = {k: kb[k] for k in cur.requires if k in kb}
            missing = [k for k in cur.requires if k not in kb]
            options = options_for(workflow, cur)

            turn = WorkerTurn(
                goal=goal, step_no=step_no, node=cur,
                resolved=resolved, missing=missing,
                options=options, profile=dict(kb),
            )
            result = self._worker.act(turn)

            if result.extracted:
                kb.update(result.extracted)

            rec = WorkflowStepRecord(
                step_no=step_no, node_key=cur.key, label=cur.label,
                status=result.status, did=result.did, branch=result.branch,
                chose_next=result.next, extracted=dict(result.extracted),
            )
            path.append(rec)
            self._emit("workflow.step", {
                "workflow_id": workflow.id, "step_no": step_no,
                "node_key": cur.key, "label": cur.label, "kind": cur.kind,
                "status": result.status, "did": result.did,
                "branch": result.branch, "next": result.next,
                "extracted": list(result.extracted.keys()),
                "options": [{"key": o.key, "label": o.label, "via": o.via, "when": o.when}
                            for o in options],
            })

            if result.status == "blocked":
                status, blocked_on = "blocked", result.reason or cur.label
                break

            # ── advance: validate the worker's chosen next against the preview ──
            nxt = (result.next or END).strip()
            if nxt == END or not options:
                break
            chosen = self._resolve_next(nxt, options)
            if chosen is None:
                # Worker named an unknown target — fall back to the common path
                # (first option) so a fuzzy answer never strands the run.
                chosen = options[0].key
            if visits.get(chosen, 0) >= 3:
                status, blocked_on = "aborted", f"loop at {chosen}"
                break
            cur = WS.node_by_key(workflow, chosen)
            if cur is None:
                break

        else:
            status, blocked_on = "aborted", "max steps exceeded"

        self._emit("workflow.done", {
            "workflow_id": workflow.id, "status": status,
            "steps": len(path), "blocked_on": blocked_on,
        })
        return WorkflowRun(workflow.id, status, path, kb, blocked_on,
                           started_at=started, ended_at=time.time())

    @staticmethod
    def _resolve_next(ref: str, options: list[NextOption]) -> Optional[str]:
        ref_l = ref.strip().lower()
        for o in options:                      # exact key
            if o.key == ref:
                return o.key
        for o in options:                      # label match
            if o.label.strip().lower() == ref_l:
                return o.key
        for o in options:                      # substring (abbreviated refs)
            if ref_l and (ref_l in o.key.lower() or ref_l in o.label.lower()):
                return o.key
        return None


# ── LLM worker (Gemma) — reasons over an observable environment ───────────────────
class Environment(Protocol):
    """The world the worker perceives/acts on. Real impl = the live screen via
    Agent S; test impl = a mock that returns page text per milestone."""
    def observe(self, turn: WorkerTurn) -> str: ...


_WORKER_SYSTEM = (
    "You are a worker executing ONE milestone of a saved workflow, then deciding "
    "where to go next IN THE SAME REPLY. You are given the milestone instruction, "
    "the inputs available to you, any taught procedure and conditional clauses "
    "(\"if <when> then <do>\"), what you can currently see, and the list of next "
    "options (each with a key; conditional options carry a `when` guard). "
    "Do the milestone, then choose exactly one next option by its key (or \"END\"). "
    "If a conditional option's `when` is true given what you see, take it. If you "
    "learned a value needed later (e.g. a projects summary), return it under "
    "\"extracted\". Reply with ONE JSON object only:\n"
    '{"did": "...", "status": "done|blocked", "next": "<option key or END>", '
    '"branch": "<when text if you took a conditional, else null>", '
    '"extracted": {"key": "value"}, "reason": "..."}'
)


class LLMWorker:
    """Worker backed by the modular LLM layer (Gemma by default). Perceives the
    environment, then returns the single-message advance. Falls back to a
    heuristic decision if the LLM is unavailable or unparsable."""

    def __init__(self, env: Environment) -> None:
        self._env = env

    def act(self, turn: WorkerTurn) -> WorkerResult:
        observation = self._env.observe(turn)
        if not llm.available():
            return _heuristic_act(turn, observation)
        try:
            text = llm.complete(_WORKER_SYSTEM, [("user", _render_turn(turn, observation))])
            obj = llm.parse_json_object(text)
            return WorkerResult(
                did=str(obj.get("did", "")),
                status="blocked" if str(obj.get("status")) == "blocked" else "done",
                next=str(obj.get("next") or END),
                branch=obj.get("branch") or None,
                extracted={str(k): str(v) for k, v in (obj.get("extracted") or {}).items()},
                reason=str(obj.get("reason", "")),
            )
        except Exception as e:
            print(f"[workflow_executor] LLM worker fell back to heuristic: {e}")
            return _heuristic_act(turn, observation)


def _render_turn(turn: WorkerTurn, observation: str) -> str:
    n = turn.node
    lines = [
        f"GOAL: {turn.goal}",
        f"MILESTONE: {n.label}",
        f"INSTRUCTION: {n.instruction or n.label}",
    ]
    if n.procedure:
        lines.append(f"TAUGHT PROCEDURE: {n.procedure}")
    if n.conditionals:
        lines.append("TAUGHT CONDITIONALS:")
        for c in n.conditionals:
            goto = f" (go to {c.goto})" if c.goto else ""
            lines.append(f"  - if {c.when} then {c.do}{goto}")
    if turn.resolved:
        lines.append(f"INPUTS AVAILABLE: {turn.resolved}")
    if turn.missing:
        lines.append(f"INPUTS MISSING: {turn.missing}")
    lines.append(f"KNOWN VALUES: {turn.profile}")
    lines.append(f"WHAT YOU SEE:\n{observation}")
    lines.append("NEXT OPTIONS:")
    for o in turn.options:
        guard = f" [take if: {o.when}]" if o.when else ""
        lines.append(f"  - key={o.key} | {o.label} | via={o.via}{guard}")
    lines.append('  - key=END | finish the workflow')
    return "\n".join(lines)


class AgentSWorker:
    """Real-GUI worker: Agent S grounds + actuates the milestone, and the next
    node is chosen deterministically from the previewed options (a conditional
    branch when a required input is missing, else the common edge) — so there is
    still no extra round-trip just to route. Actuation is delegated to `actuate`
    (the engine's exec helper); both Agent S and actuate may be None, in which
    case this degrades to the heuristic advance."""

    def __init__(self, agent_s=None, actuate=None) -> None:
        self._agent_s = agent_s
        self._actuate = actuate

    def act(self, turn: WorkerTurn) -> WorkerResult:
        n = turn.node
        did = ""
        if self._agent_s is not None and getattr(self._agent_s, "available", False):
            try:
                instruction = _render_turn(turn, "(use the live screen)")
                code = self._agent_s.plan_action(instruction, turn.step_no, "")
                if code and self._actuate is not None:
                    self._actuate(code)
                    did = f"actuated {n.label}"
            except Exception as e:
                print(f"[workflow_executor] Agent S actuation failed: {e}")
        base = _heuristic_act(turn, "")
        if did:
            base.did = did
        return base


def _heuristic_act(turn: WorkerTurn, observation: str) -> WorkerResult:
    """Network-free fallback: take a conditional branch when its required input is
    missing (the canonical "unknown field → taught research" case); otherwise
    follow the common edge; END when nothing remains."""
    extracted: dict[str, str] = {}
    obs = observation or ""

    # If a milestone instruction asks to read/extract values, lift "key: value"
    # pairs out of the observation so later milestones can use them.
    for line in obs.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            k, v = k.strip().lower().replace(" ", "_"), v.strip()
            if k and v and (k in turn.node.requires or "summary" in k or "project" in k):
                extracted[k] = v

    # Prefer a conditional option whose required target input is still missing.
    for o in turn.options:
        if o.via == "conditional" and turn.missing:
            return WorkerResult(
                did=f"{turn.node.label}: blocked on {turn.missing}, taking taught branch",
                status="done", next=o.key, branch=o.when, extracted=extracted,
                reason="missing required input → taught conditional",
            )
    edges = [o for o in turn.options if o.via == "edge"]
    if edges:
        return WorkerResult(did=f"did {turn.node.label}", status="done",
                            next=edges[0].key, extracted=extracted)
    return WorkerResult(did=f"did {turn.node.label}", status="done", next=END,
                        extracted=extracted)
