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
