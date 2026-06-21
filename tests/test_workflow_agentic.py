"""
Tests for the refactored workflow dispatch (design §0): a dispatched workflow runs
**through the batched agentic loop**, not a separate per-milestone executor.

These assert the two guarantees the refactor exists to provide:
  1. Single-message advance — exactly ONE planning call per milestone turn (the agent
     emits its action AND the `next` node in the same reply), so advancing the graph
     costs zero extra routing round-trips.
  2. Conditional self-routing — the agent's returned `next`/`branch` moves the cursor,
     including down a taught conditional branch, and `extracted` KB flows forward.

The planner is faked (no GUI / no LLM) and `_exec_agent_code` is a no-op, so the loop
logic is exercised deterministically. Event emission is captured to confirm the
`workflow.*` stream the live trace graph + coordinator consume is unchanged.
"""
import contextlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dashboard.events import event_bus
from shepherd_types import AutonomousStepResult
from engine.engine import ShepherdExecutionEngine
from tests.mock_job_app import build_workflow, OPEN, FILL, PROJECTS, RESEARCH, SUBMIT


class _NoopSpan:
    def set_attribute(self, *_a, **_k):
        pass


class _NoopTelemetry:
    def span(self, *_a, **_k):
        return contextlib.nullcontext(_NoopSpan())


class FakeAgentS:
    """Stands in for AgentSAdapter: records each plan_workflow_chain call and routes
    deterministically — takes a taught conditional when its required input is missing,
    else the default edge; supplies the projects summary at the RESEARCH node."""

    def __init__(self):
        self.calls = []          # one entry per plan_workflow_chain call
        self.last_reasoning = "did the milestone"

    def reset(self):
        pass

    def reset_autonomous(self):
        pass

    def plan_workflow_chain(self, goal, step_no, instruction, next_label="",
                            options=None, resolved=None, missing=None):
        options = options or []
        resolved = resolved or {}
        self.calls.append({"instruction": instruction, "options": list(options)})

        extracted = {}
        if instruction.strip().lower().startswith("open the projects page"):
            extracted = {"projects_summary": "Built Shepherd, VectorRoute, GraphBake"}

        cond = [o for o in options if o.get("via") == "conditional"]
        default = [o for o in options if o.get("via") != "conditional"]
        branch = None
        if cond and "projects_summary" not in resolved:
            chosen_next = cond[0]["key"]
            branch = cond[0]["when"]
        elif default:
            chosen_next = default[0]["key"]
        elif options:
            chosen_next = options[0]["key"]
        else:
            chosen_next = "END"

        return AutonomousStepResult(
            outcome="action", code="pyautogui.click(1, 1)", raw="acted",
            next=chosen_next, branch=branch, extracted=extracted,
        )


def _run(workflow, params):
    agent = FakeAgentS()
    engine = ShepherdExecutionEngine(coords={}, telemetry=_NoopTelemetry(), mode="LIVE",
                                     agent_s=agent)
    exec_calls = []
    engine._exec_agent_code = lambda code: exec_calls.append(code)  # no GUI

    events = []
    collector = lambda msg: events.append((msg["type"], msg["data"]))
    event_bus.subscribe(collector)
    try:
        result = engine.execute_workflow(workflow, goal="apply to this job", params=params)
    finally:
        event_bus.unsubscribe(collector)
    return agent, engine, result, exec_calls, events


def _wf_steps(events):
    return [d for (t, d) in events if t == "workflow.step"]


def test_dispatch_runs_through_agentic_loop_one_call_per_milestone():
    """Summary case: projects summary already known → straight line, and every
    milestone advances on a SINGLE planning call (no extra routing round-trip)."""
    wf = build_workflow()
    agent, _engine, result, exec_calls, events = _run(wf, params={
        "applicant_name": "Alex Johnson", "applicant_email": "alex@example.com",
        "projects_summary": "Prior summary already on file",
    })

    assert result.status == "completed"

    # Straight line — the taught RESEARCH branch is skipped because the input is known.
    visited = [d["node_key"] for d in _wf_steps(events)]
    assert visited == [OPEN, FILL, PROJECTS, SUBMIT]

    # Single-message advance: exactly one planning call per milestone (4 nodes → 4
    # calls). If routing took a second round-trip there would be more calls than nodes.
    assert len(agent.calls) == len(visited) == 4
    assert len(exec_calls) == 4                       # one batched action exec per node


def test_dispatch_self_routes_down_taught_conditional_branch():
    """No summary on file → the agent's returned `next`/`branch` routes down the
    taught conditional to RESEARCH, learns the summary, and carries it forward —
    still one planning call per milestone."""
    wf = build_workflow()
    agent, _engine, result, _exec, events = _run(wf, params={
        "applicant_name": "Alex Johnson", "applicant_email": "alex@example.com",
    })

    assert result.status == "completed"

    steps = _wf_steps(events)
    visited = [d["node_key"] for d in steps]
    assert visited == [OPEN, FILL, PROJECTS, RESEARCH, SUBMIT]

    # The PROJECTS milestone took the conditional in the SAME message it acted in.
    projects_step = next(s for s in steps if s["node_key"] == PROJECTS)
    assert projects_step["next"] == RESEARCH
    assert projects_step["branch"]                    # named the guard it matched

    # The RESEARCH milestone surfaced the learned summary as extracted KB.
    research_step = next(s for s in steps if s["node_key"] == RESEARCH)
    assert "projects_summary" in research_step["extracted"]

    # Still one planning call per milestone — five nodes, five calls.
    assert len(agent.calls) == len(visited) == 5


def test_dispatch_emits_workflow_event_stream():
    """The agentic loop emits the same workflow.* events the executor did, so the
    live trace graph + coordinator are unaffected."""
    wf = build_workflow()
    _agent, _engine, _result, _exec, events = _run(wf, params={
        "applicant_name": "Alex", "applicant_email": "a@x.com",
        "projects_summary": "known",
    })
    types = [t for (t, _d) in events]
    assert types[0] == "workflow.start"
    assert "workflow.node.enter" in types
    assert "workflow.step" in types
    assert types.count("workflow.done") == 1
    done = next(d for (t, d) in events if t == "workflow.done")
    assert done["status"] == "completed"
