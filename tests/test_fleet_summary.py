"""Fleet session summaries (issue #21): title, workflow badge, recent-steps peek.

Mirrors the integration style of tests/test_remote_workflow.py — exercises the
coordinator's AgentConn/Hub event application and verifies the new fields appear
in the snapshot.
"""
import asyncio
from unittest.mock import patch

from coordinator.server import AgentConn, Hub
from coordinator.title_gen import _truncate_goal


def _conn() -> AgentConn:
    return AgentConn(agent_id="a1", name="A1", host="box", ws=None)


def _ev(t, **data):
    return {"type": t, "data": data}


def test_snapshot_includes_title_and_recent_steps_fields():
    """The snapshot always contains `title` and `recentSteps` keys."""
    conn = _conn()
    snap = conn.snapshot()
    assert "title" in snap
    assert "recentSteps" in snap
    assert snap["title"] is None
    assert snap["recentSteps"] == []


def test_recent_steps_accumulated_from_step_start():
    """step.start events populate recentSteps with the last 3 descriptions."""
    hub = Hub()
    conn = _conn()

    hub.apply_event(conn, _ev("execution.start", run_id="R1", goal="test goal"))
    hub.apply_event(conn, _ev("step.start", index=0, description="Open browser", total=5))
    hub.apply_event(conn, _ev("step.start", index=1, description="Navigate to site", total=5))
    hub.apply_event(conn, _ev("step.start", index=2, description="Click login", total=5))
    hub.apply_event(conn, _ev("step.start", index=3, description="Enter credentials", total=5))

    snap = conn.snapshot()
    steps = snap["recentSteps"]
    assert len(steps) == 3
    assert steps[0]["description"] == "Navigate to site"
    assert steps[1]["description"] == "Click login"
    assert steps[2]["description"] == "Enter credentials"
    assert steps[2]["index"] == 3


def test_recent_steps_reset_on_new_execution():
    """A new execution.start resets the recent steps."""
    hub = Hub()
    conn = _conn()

    hub.apply_event(conn, _ev("execution.start", run_id="R1", goal="first run"))
    hub.apply_event(conn, _ev("step.start", index=0, description="Step A"))
    assert len(conn.snapshot()["recentSteps"]) == 1

    hub.apply_event(conn, _ev("execution.start", run_id="R2", goal="second run"))
    assert conn.snapshot()["recentSteps"] == []


def test_title_generation_triggered_on_execution_start():
    """Title gen fires on execution.start when a goal is available."""
    hub = Hub()
    conn = _conn()

    with patch("coordinator.server.generate_title_async") as mock_gen:
        hub.apply_event(conn, _ev("execution.start", run_id="R1", goal="Apply to Acme SWE"))
        mock_gen.assert_called_once_with(conn, "Apply to Acme SWE")


def test_title_generation_triggered_on_first_step_if_no_goal():
    """If no goal at execution.start, title gen fires on the first step.start."""
    hub = Hub()
    conn = _conn()

    with patch("coordinator.server.generate_title_async") as mock_gen:
        hub.apply_event(conn, _ev("execution.start", run_id="R1"))
        mock_gen.assert_not_called()

        hub.apply_event(conn, _ev("step.start", index=0, description="Open job listing"))
        mock_gen.assert_called_once_with(conn, "Open job listing")


def test_title_not_regenerated_after_first_trigger():
    """Title generation fires at most once per run."""
    hub = Hub()
    conn = _conn()

    with patch("coordinator.server.generate_title_async") as mock_gen:
        hub.apply_event(conn, _ev("execution.start", run_id="R1", goal="Apply to job"))
        hub.apply_event(conn, _ev("step.start", index=0, description="Open browser"))
        hub.apply_event(conn, _ev("step.start", index=1, description="Navigate"))
        assert mock_gen.call_count == 1


def test_title_truncation():
    """Truncation produces a sane short title."""
    short = "Apply to job"
    assert _truncate_goal(short) == short

    long = "This is a very long goal description that goes on and on about what the agent should do right now"
    result = _truncate_goal(long)
    assert len(result) <= 65  # _MAX_TITLE_LEN + "…"
    assert result.endswith("…")


def test_intent_received_captures_goal_text():
    """intent.received sets _goal_text for later title generation."""
    hub = Hub()
    conn = _conn()
    hub.apply_event(conn, _ev("intent.received", raw_text="apply to the job", source="cli"))
    assert conn._goal_text == "apply to the job"


def test_default_title_uses_raw_intent_text():
    """With TITLE_GEN_LLM off (default), title is the raw intent/goal text."""
    from coordinator.title_gen import generate_title_async

    conn = _conn()
    conn.run_id = "R1"

    # Default path: no LLM, just use the goal text directly.
    with patch("coordinator.title_gen.TITLE_GEN_LLM", False):
        generate_title_async(conn, "apply to the Acme SWE role")

    assert conn.title == "apply to the Acme SWE role"


def test_title_gen_sync_fallback_when_llm_unavailable():
    """When LLM is not available, falls back to truncated goal."""
    from coordinator.title_gen import _generate_title_sync

    with patch("engine.llm.available", return_value=False):
        result = _generate_title_sync("Apply to the Acme SWE position")
        assert result == "Apply to the Acme SWE position"


def test_generate_title_async_llm_mode():
    """With TITLE_GEN_LLM on, title is set asynchronously via LLM."""
    from coordinator.title_gen import generate_title_async

    conn = _conn()
    conn.run_id = "R1"

    with patch("coordinator.title_gen.TITLE_GEN_LLM", True), \
         patch("coordinator.title_gen._generate_title_sync", return_value="Applying to Acme"):
        async def _run():
            generate_title_async(conn, "apply to acme")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        asyncio.run(_run())

    assert conn.title == "Applying to Acme"


def test_generate_title_async_skips_stale_write():
    """If a new run starts before LLM title gen finishes, the write is skipped."""
    from coordinator.title_gen import generate_title_async

    conn = _conn()
    conn.run_id = "R1"

    with patch("coordinator.title_gen.TITLE_GEN_LLM", True), \
         patch("coordinator.title_gen._generate_title_sync", return_value="Old title"):
        async def _run():
            generate_title_async(conn, "old goal")
            # Simulate a new run starting before the task completes.
            conn.run_id = "R2"
            conn.title = None
            await asyncio.sleep(0)
            await asyncio.sleep(0)
        asyncio.run(_run())

    # The stale title should NOT have been written.
    assert conn.title is None


def test_title_set_immediately_in_snapshot():
    """With default config, title appears in snapshot right after execution.start."""
    hub = Hub()
    conn = _conn()

    hub.apply_event(conn, _ev("execution.start", run_id="R1", goal="Book flight to NYC"))
    snap = conn.snapshot()
    assert snap["title"] == "Book flight to NYC"


def test_intent_text_preserved_through_execution_start():
    """intent.received goal text persists into execution.start for title."""
    hub = Hub()
    conn = _conn()

    hub.apply_event(conn, _ev("intent.received", raw_text="apply to the job", source="cli"))
    hub.apply_event(conn, _ev("execution.start", run_id="R1"))

    snap = conn.snapshot()
    assert snap["title"] == "apply to the job"
