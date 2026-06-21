"""
Tests for the refactored workflow dispatch (design §0): a dispatched workflow runs
**through the batched agentic loop**, not a separate per-milestone executor.

These assert the guarantees the refactor exists to provide:
  1. No more messages than normal — the agent batches across consecutive milestones in
     ONE planning call (it reports the extra milestones it finished in `completed`), so
     a workflow run costs no more LLM calls than the same goal with no workflow. A
     milestone only forces its own turn when it is a decision point (taught conditional).
  2. Single-message advance — the agent emits its actions AND the `next` node in the
     same reply, so advancing the graph costs zero extra routing round-trips.
  3. Conditional self-routing — the agent's returned `next`/`branch` moves the cursor,
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
    """Stands in for AgentSAdapter and models a *real batched agent*: given the
    forward milestone plan it finishes as many consecutive milestones as it can in one
    call (reporting them in `completed`), stopping only AT a decision point (a node
    carrying a taught conditional) so the branch is evaluated with that node as the
    cursor. At a decision point it takes the conditional when its input is missing,
    else the default edge; it supplies the projects summary at the RESEARCH node."""

    def __init__(self):
        self.calls = []          # one entry per plan_workflow_chain call
        self.last_reasoning = "did the milestone"

    def reset(self):
        pass

    def reset_autonomous(self):
        pass

    def plan_workflow_chain(self, goal, step_no, instruction, forward=None,
                            options=None, resolved=None, missing=None):
        forward = forward or []
        options = options or []
        resolved = resolved or {}
        self.calls.append({"instruction": instruction, "forward": list(forward),
                           "options": list(options)})

        completed: list[str] = []
        extracted: dict[str, str] = {}
        branch = None
        chosen_next = "END"

        for i, m in enumerate(forward):
            conds = m.get("conditionals") or []
            if conds:
                if i == 0:
                    # We are AT the decision node this turn — evaluate the guard.
                    if "projects_summary" not in resolved:
                        chosen_next = conds[0]["goto"]
                        branch = conds[0]["when"]
                    else:
                        completed.append(m["key"])
                        default = [o for o in options if o.get("via") != "conditional"]
                        chosen_next = default[0]["key"] if default else "END"
                else:
                    # Decision node downstream — stop the batch before it so it
                    # becomes the cursor and gets its own evaluation turn.
                    chosen_next = m["key"]
                break
            completed.append(m["key"])
            if m["key"] == RESEARCH:
                extracted = {"projects_summary": "Built Shepherd, VectorRoute, GraphBake"}

        return AutonomousStepResult(
            outcome="action", code="pyautogui.click(1, 1)", raw="acted",
            next=chosen_next, branch=branch, extracted=extracted, completed=completed,
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


def test_dispatch_batches_across_milestones_fewer_calls_than_nodes():
    """Summary case: projects summary already known → straight line, and the agent
    BATCHES consecutive milestones in one call, so the run costs fewer LLM calls than
    there are milestones (the regression fix — a workflow is no slower than no
    workflow). OPEN+FILL collapse into one call; only PROJECTS (a taught decision
    point) forces its own turn."""
    wf = build_workflow()
    agent, _engine, result, exec_calls, events = _run(wf, params={
        "applicant_name": "Alex Johnson", "applicant_email": "alex@example.com",
        "projects_summary": "Prior summary already on file",
    })

    assert result.status == "completed"

    # Every milestone still appears in the trace — OPEN+FILL and the SUBMIT tail were
    # replayed as done from the batches that covered them.
    visited = [d["node_key"] for d in _wf_steps(events)]
    assert visited == [OPEN, FILL, PROJECTS, SUBMIT]

    # Fewer planning calls than milestones: OPEN+FILL batched together, PROJECTS is a
    # decision turn, SUBMIT batched. 4 milestones, 3 calls — never one-per-field.
    assert len(agent.calls) == 3 < len(visited)
    assert len(exec_calls) == 3                       # one batched action exec per call


def test_dispatch_self_routes_down_taught_conditional_branch():
    """No summary on file → the agent's returned `next`/`branch` routes down the
    taught conditional to RESEARCH, learns the summary, and carries it forward. The
    decision point + research digression are the ONLY places extra turns are spent."""
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

    # Five milestones, but only three calls: OPEN+FILL batched, PROJECTS (decision),
    # RESEARCH+SUBMIT batched. The branch adds turns only where it genuinely must.
    assert len(agent.calls) == 3 < len(visited)


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
