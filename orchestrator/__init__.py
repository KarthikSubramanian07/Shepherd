"""
Multi-agent orchestration for Shepherd.

Run multiple agents on the desktop at once — local Agent S agents serialized on
the single physical desktop, plus isolated Browserbase cloud sessions running in
parallel — with an :class:`ActionArbiter` as the action queue that guarantees no
two agents ever actuate the same surface simultaneously.

See ``docs/MULTI_AGENT.md`` for the design.
"""
from orchestrator.arbiter import (
    ActionArbiter,
    Lease,
    LeaseCancelled,
    PRIORITY_HALT,
    PRIORITY_NORMAL,
)
from orchestrator.orchestrator import Orchestrator
from orchestrator.worker import AgentTask, AgentWorker
from orchestrator import surfaces

__all__ = [
    "ActionArbiter",
    "Lease",
    "LeaseCancelled",
    "PRIORITY_HALT",
    "PRIORITY_NORMAL",
    "Orchestrator",
    "AgentTask",
    "AgentWorker",
    "surfaces",
]
