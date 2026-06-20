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
class RoutineStep:
    action: str  # "move"|"click"|"double_click"|"type"|"hotkey"|"open_app"|"wait"|"browser"
    target: Optional[str] = None
    text: Optional[str] = None
    keys: Optional[list[str]] = None
    seconds: Optional[float] = None
    browser_step: Optional[dict] = None
    monitor_trigger: Optional[str] = None  # planted: "credential"|"captcha"|"phishing"|"stuck"
    description: Optional[str] = None


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
class TaskGraphNode:
    """
    One coarse milestone the task has performed (e.g. "Search: AI agent safety",
    "Scan results", "Submit") — NOT a single click. Many fine clicks collapse into
    one node. Accumulates across runs.
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
    run_count: int = 0
    intents: list[str] = field(default_factory=list)
    variables: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    last_run_id: str = ""
