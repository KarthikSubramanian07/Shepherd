from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Intent:
    raw_text: str
    timestamp: float
    source: str = "typed"  # "typed" | "voice"


@dataclass
class ResolvedRoutine:
    routine_id: str
    variables: dict[str, str]
    confidence: float
    matched_keywords: list[str]


@dataclass
class BatchField:
    """One sub-step inside a batch_fill action."""
    tabs: int = 1          # how many Tab presses before typing
    text: Optional[str] = None    # text to type (None = tab-only, e.g. to skip a field)
    description: Optional[str] = None   # field label for Agent S prompt


@dataclass
class RoutineStep:
    action: str  # "move"|"click"|"double_click"|"type"|"hotkey"|"open_app"|"wait"|"browser"|"batch_fill"
    target: Optional[str] = None
    text: Optional[str] = None
    keys: Optional[list[str]] = None
    seconds: Optional[float] = None
    browser_step: Optional[dict] = None
    monitor_trigger: Optional[str] = None  # planted: "credential"|"captcha"|"phishing"|"stuck"
    description: Optional[str] = None
    fields: Optional[list[BatchField]] = None  # used by batch_fill


@dataclass
class RecordedStep:
    index: int
    action: str
    target: Optional[str]
    text: Optional[str]
    timestamp: float
    instruction: Optional[str] = None      # spoken narration attached at this step
    screenshot_path: Optional[str] = None  # captured at step boundary by recorder


@dataclass
class RoutineDefinition:
    routine_id: str
    description: str
    variables: list[str]
    steps: list[RoutineStep]
    demonstration: Optional[list[RecordedStep]] = None
    step_instructions: Optional[dict[int, str]] = None
    high_stakes_steps: list[int] = field(default_factory=list)
    mode: str = "LIVE"  # "LIVE" | "LOCKED"


@dataclass
class StepRecord:
    index: int
    action: str
    target: Optional[str]
    status: str  # "completed"|"failed"|"halted"|"flagged"
    started_at: float
    duration_ms: int
    error: Optional[str] = None
    monitor_verdict: Optional[str] = None
    deviation: Optional[str] = None  # description if agent diverged from recorded demonstration


@dataclass
class NodeStats:
    routine_id: str
    step_index: int
    success_count: int = 0
    failure_count: int = 0
    halt_count: int = 0
    deviation_count: int = 0
    approval_count: int = 0
    total_duration_ms: int = 0
    execution_count: int = 0


@dataclass
class ExecutionResult:
    routine_id: str
    status: str  # "completed"|"failed"|"aborted"
    steps_completed: int
    error: Optional[str]
    duration_ms: int
    variables: dict[str, str]
    started_at: float
    ended_at: float
    run_id: str = ""


@dataclass
class ReplayRecord:
    run_id: str
    routine_id: str
    started_at: float
    ended_at: float
    steps: list[StepRecord]
    variables: dict[str, str]
    confidence: float
    sentry_event_id: Optional[str] = None


@dataclass
class Conditional:
    """
    A natural-language conditional clause carried by a milestone — read by the
    agent in-context ("if <when> → do <do>"), NOT a separate predicate engine.
    Baked by the coalescer when a human resolves a block/deviation with the
    save_as_rule flag. `goto` optionally reuses another node's action.
    """
    when: str                         # NL guard, e.g. "you don't have the project info"
    do: str                           # NL action, e.g. "research the user's GitHub …"
    goto: Optional[str] = None        # key of another node whose action to reuse
    source: str = "taught"            # "taught" | "observed"


@dataclass
class TaskGraphNode:
    """
    One coarse milestone the task has performed (e.g. "Search: AI agent safety",
    "Scan results", "Submit") — NOT a single click. Many fine clicks collapse into
    one node. Accumulates across runs.

    Beyond the observed milestone it may carry TAUGHT knowledge — a standard
    `procedure` and `conditionals` baked from human interventions — turning the
    passively-observed graph into an opinionated, self-improving workflow.
    """
    key: str                          # stable signature: kind::value::label
    kind: str                         # open|navigate|search|scan|fill|submit|interact
    label: str                        # human label, e.g. "Search: AI agent safety"
    value: Optional[str] = None       # captured payload (search term, URL host, …)
    times_seen: int = 0
    last_status: Optional[str] = None
    fine_steps: int = 0               # # of clicks that collapsed into this milestone
    first_run_id: str = ""
    last_run_id: str = ""
    # ── taught / workflow layer ────────────────────────────────────────────────
    instruction: str = ""             # NL instruction handed to Agent S (defaults to label)
    requires: list[str] = field(default_factory=list)        # profile/KB keys needed here
    conditionals: list[Conditional] = field(default_factory=list)  # if <when> → do <do>
    procedure: Optional[str] = None   # taught standard procedure for this milestone
    optional: bool = False
    source: str = "observed"          # "observed" | "taught"


@dataclass
class TaskGraphEdge:
    """
    A directed transition between two milestones (from_key -> to_key), accumulated
    across runs. A milestone with several outgoing edges is a BRANCH POINT — e.g.
    "Sign in" -> {"Dashboard loaded" | "Sign-in rejected"} across different runs.
    times_seen weights the common path so the UI can emphasize it.
    """
    from_key: str
    to_key: str
    times_seen: int = 0
    last_run_id: str = ""
    condition: Optional[str] = None   # NL guard on a conditional/taught branch


@dataclass
class TaskGraph:
    """
    Durable per-task memory at MILESTONE granularity. A new run loads this as a
    reference, executes against it, and appends any milestone it performs that
    isn't already a node. (Per-click detail is still fed to Agent S separately.)
    """
    task_key: str                     # currently the resolved routine_id
    routine_id: str
    nodes: list[TaskGraphNode] = field(default_factory=list)
    edges: list[TaskGraphEdge] = field(default_factory=list)
    run_count: int = 0
    intents: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_id: str = ""


@dataclass
class InterventionEvent:
    """
    A point where the agent blocked and a human resolved it. Captured cheaply on
    the hot path; consumed by the coalescer to (optionally) bake a standard
    procedure into the workflow.

    flag gates persistence:
      "one_off"      — journal only; do not modify the workflow
      "save_as_rule" — bake a conditional clause {when: scenario, do: instruction}
    """
    step_index: int
    trigger: str = ""             # scenario signature, e.g. "credential" | "unknown_field"
    decision: str = ""            # "approve" | "halt" | "override"
    instruction: str = ""         # human override / taught procedure (NL)
    flag: str = "one_off"         # "one_off" | "save_as_rule"
    node_key: str = ""            # milestone the block attaches to (for EDIT-mode baking)
    scenario: str = ""            # NL description of the scenario, falls back to trigger
    ts: float = 0.0


@dataclass
class RunTrace:
    """
    The cheap, durable record of a single run — actions performed plus the
    interventions/deviations encountered. Written to the journal at the run
    boundary and handed to the async coalescer (NEVER segmented on the hot path).
    """
    run_id: str
    routine_id: str
    variables: dict[str, str] = field(default_factory=dict)
    status: str = "completed"
    started_at: float = 0.0
    ended_at: float = 0.0
    executed: list[RoutineStep] = field(default_factory=list)
    interventions: list[InterventionEvent] = field(default_factory=list)
    deviations: list[dict] = field(default_factory=list)
