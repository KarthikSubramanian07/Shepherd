"""Fleet execution-trace accumulator: verifies that events from multiple agents
build separate per-agent trace dicts and are served correctly via the REST endpoint."""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from dashboard import fleet_trace as ft
from dashboard.events import event_bus
from dashboard import server


# ── helpers ──────────────────────────────────────────────────────────────────

def _reset():
    """Clear all fleet trace state between tests."""
    with ft._lock:
        ft._traces.clear()
        ft._run_to_agent.clear()


def _get(path):
    async def go():
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get(path)
            return r.status_code, r.json()
    return asyncio.run(go())


# ── tests: accumulator ──────────────────────────────────────────────────────

def test_execution_start_creates_trace():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-001", "run_id": "run-a",
        "routine_id": "AUTONOMOUS", "mode": "AUTONOMOUS",
        "total_steps": 10, "goal": "find flights",
    })
    tr = ft.get_trace("agent-001")
    assert tr is not None
    assert tr["runId"] == "run-a"
    assert tr["status"] == "running"
    assert tr["nodes"] == []


def test_step_events_build_nodes():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-002", "run_id": "run-b",
        "routine_id": "BB", "mode": "BROWSERBASE",
    })
    event_bus.emit("step.start", {
        "agent_id": "agent-002", "run_id": "run-b", "index": 0,
        "action": "goto", "description": "goto https://example.com", "total": 20,
    })
    event_bus.emit("step.complete", {
        "agent_id": "agent-002", "run_id": "run-b", "index": 0,
        "status": "completed", "duration_ms": 1200,
    })
    event_bus.emit("step.start", {
        "agent_id": "agent-002", "run_id": "run-b", "index": 1,
        "action": "click", "description": "click Sign in",
    })
    tr = ft.get_trace("agent-002")
    assert len(tr["nodes"]) == 2
    assert tr["nodes"][0]["action"] == "goto"
    assert tr["nodes"][0]["status"] == "completed"
    assert tr["nodes"][0]["durationMs"] == 1200
    assert tr["nodes"][1]["action"] == "click"
    assert tr["nodes"][1]["status"] == "running"
    assert tr["current"] == 1


def test_multiple_agents_are_separate():
    _reset()
    ft.install()
    for aid, rid in [("agent-a", "run-1"), ("agent-b", "run-2")]:
        event_bus.emit("execution.start", {
            "agent_id": aid, "run_id": rid,
            "routine_id": "R", "mode": "AUTONOMOUS",
        })
        event_bus.emit("step.start", {
            "agent_id": aid, "run_id": rid, "index": 0,
            "action": "goto", "description": f"goto for {aid}",
        })
    tr_a = ft.get_trace("agent-a")
    tr_b = ft.get_trace("agent-b")
    assert tr_a["runId"] == "run-1"
    assert tr_b["runId"] == "run-2"
    assert tr_a["nodes"][0]["description"] == "goto for agent-a"
    assert tr_b["nodes"][0]["description"] == "goto for agent-b"


def test_run_id_fallback_maps_to_agent():
    """Engine step events carry run_id but not agent_id; the accumulator should
    resolve agent_id from the run_id→agent_id map seeded by execution.start."""
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-003", "run_id": "run-c",
        "routine_id": "AUTO", "mode": "AUTONOMOUS",
    })
    # Emit step with run_id only (no agent_id) — simulates engine events
    event_bus.emit("step.start", {
        "run_id": "run-c", "index": 0,
        "action": "agent_s", "description": "plan step",
    })
    tr = ft.get_trace("agent-003")
    assert len(tr["nodes"]) == 1
    assert tr["nodes"][0]["action"] == "agent_s"


def test_execution_complete_sets_status():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-004", "run_id": "run-d",
        "routine_id": "R", "mode": "BROWSERBASE",
    })
    event_bus.emit("execution.complete", {
        "agent_id": "agent-004", "run_id": "run-d",
        "status": "completed", "steps_completed": 5,
    })
    tr = ft.get_trace("agent-004")
    assert tr["status"] == "completed"
    assert tr["current"] is None


def test_step_error_marks_node():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-005", "run_id": "run-e",
        "routine_id": "R", "mode": "BROWSERBASE",
    })
    event_bus.emit("step.start", {
        "agent_id": "agent-005", "run_id": "run-e", "index": 0,
        "action": "click", "description": "click button",
    })
    event_bus.emit("step.error", {
        "agent_id": "agent-005", "run_id": "run-e", "index": 0,
        "error": "element not found",
    })
    tr = ft.get_trace("agent-005")
    assert tr["nodes"][0]["status"] == "error"
    assert tr["nodes"][0]["error"] == "element not found"


def test_thinking_event_updates_node():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-006", "run_id": "run-f",
        "routine_id": "R", "mode": "BROWSERBASE",
    })
    event_bus.emit("step.start", {
        "agent_id": "agent-006", "run_id": "run-f", "index": 0,
        "action": "goto", "description": "navigate",
    })
    event_bus.emit("step.agent_s_thinking", {
        "agent_id": "agent-006", "run_id": "run-f", "index": 0,
        "description": "I need to navigate to the search page",
    })
    tr = ft.get_trace("agent-006")
    assert tr["nodes"][0]["thinking"] == "I need to navigate to the search page"


def test_remove_trace_cleans_up():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-007", "run_id": "run-g",
        "routine_id": "R", "mode": "BROWSERBASE",
    })
    assert ft.get_trace("agent-007") is not None
    ft.remove_trace("agent-007")
    assert ft.get_trace("agent-007") is None


def test_no_trace_returns_none():
    _reset()
    assert ft.get_trace("nonexistent") is None


# ── tests: REST endpoint ────────────────────────────────────────────────────

def test_rest_trace_returns_data():
    _reset()
    ft.install()
    event_bus.emit("execution.start", {
        "agent_id": "agent-010", "run_id": "run-x",
        "routine_id": "R", "mode": "BROWSERBASE",
    })
    event_bus.emit("step.start", {
        "agent_id": "agent-010", "run_id": "run-x", "index": 0,
        "action": "goto", "description": "goto example.com",
    })
    status, body = _get("/api/fleet/agent-010/trace")
    assert status == 200
    assert body["trace"] is not None
    assert body["trace"]["runId"] == "run-x"
    assert len(body["trace"]["nodes"]) == 1


def test_rest_trace_missing_agent():
    _reset()
    status, body = _get("/api/fleet/nonexistent/trace")
    assert status == 200
    assert body["trace"] is None
