#!/usr/bin/env python3
"""
Live end-to-end test for mid-run steering.

Starts a minimal Shepherd engine, sends steer/halt/resume commands via the
dashboard HTTP API, and verifies the full lifecycle works end-to-end.

This test exercises:
1. Engine loop with real steer queue consumption
2. Dashboard API endpoints (/api/steer, /api/new_task)
3. SuspendedTask creation on halt/fail
4. Resume via __RESUME__ sentinel
5. Goal amendment propagation through both paths

Usage: .venv/bin/python tests/e2e_live_steering.py
"""
import json
import os
import queue
import sys
import threading
import time
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("EXECUTION_MODE", "AUTONOMOUS")
os.environ.setdefault("LLM_PROVIDER", "gemini")


def main():
    print("=" * 70)
    print("E2E LIVE STEERING TEST")
    print("=" * 70)

    from dotenv import load_dotenv
    load_dotenv()

    # ── Patch heavy deps that require display/network ────────────────────
    # We patch Agent S to simulate LLM responses without a real display.
    # This tests the STEERING INFRASTRUCTURE (queue, events, API, resume)
    # which is the new code, not Agent S itself.

    from engine.engine import ShepherdExecutionEngine, SuspendedTask
    from dashboard.server import register_intent_queue, register_engine
    from dashboard.events import event_bus

    events_captured = []
    original_emit = event_bus.emit

    def capture_event(event_type, data=None):
        events_captured.append({"type": event_type, "data": data, "ts": time.time()})
        if event_type in ("execution.steered", "execution.suspended", "execution.resumed",
                          "execution.start", "execution.complete", "step.start", "step.complete"):
            print(f"  [EVENT] {event_type}: {json.dumps(data or {}, default=str)[:100]}")

    event_bus.emit = capture_event

    # Create engine with mocked Agent S (simulates LLM responses)
    mock_telemetry = MagicMock()
    mock_telemetry.span.return_value.__enter__ = MagicMock(return_value=MagicMock())
    mock_telemetry.span.return_value.__exit__ = MagicMock(return_value=False)

    with patch("engine.engine.AgentSAdapter") as MockAgent, \
         patch("engine.engine.TaskGraphStore") as MockGraphStore:
        mock_agent = MockAgent.return_value
        mock_agent.available = True
        mock_agent._chain_history = []
        mock_agent.last_reasoning = "Planning next action based on goal"
        mock_agent.reset_autonomous = MagicMock()

        mock_graph_store = MockGraphStore.return_value
        mock_graph = MagicMock()
        mock_graph.nodes = []
        mock_graph.run_count = 0
        mock_graph_store.load.return_value = mock_graph
        mock_graph_store.is_known.return_value = False

        engine = ShepherdExecutionEngine(
            coords={}, telemetry=mock_telemetry, mode="AUTONOMOUS",
            agent_s=mock_agent, planner=MagicMock(),
        )

    remote_intents: "queue.Queue[str]" = queue.Queue()
    register_intent_queue(remote_intents)
    register_engine(engine)

    # Start dashboard
    import uvicorn
    from dashboard.server import app

    dashboard_thread = threading.Thread(
        target=uvicorn.run, args=(app,),
        kwargs={"host": "127.0.0.1", "port": 52150, "log_level": "warning"},
        daemon=True,
    )
    dashboard_thread.start()
    time.sleep(1.5)

    import requests
    BASE = "http://127.0.0.1:52150"

    print(f"\n✓ Dashboard started on {BASE}")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 1: Steer while running — verify goal amendment
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "─" * 70)
    print("TEST 1: Start task → Steer mid-run → verify goal amended")
    print("─" * 70)

    step_count = [0]
    goals_seen = []
    steered = threading.Event()

    def mock_predict_phase1(goal, step_idx, memory_hint="", plan_hint=""):
        step_count[0] += 1
        goals_seen.append(goal)
        print(f"    [predict] step {step_count[0]}, goal[:60]: {goal[:60]}...")
        result = MagicMock()
        result.raw = ""

        if step_count[0] == 2:
            # Signal: steer should be visible by now
            steered.set()

        if step_count[0] >= 5:
            result.outcome = "done"
            result.code = ""
        else:
            result.outcome = "action"
            result.code = "print('step action')"

        # Simulate a small delay (like a real API call)
        time.sleep(0.3)
        return result

    mock_agent.predict_autonomous.side_effect = mock_predict_phase1

    # Start task in background
    task_result = [None]

    def run_task_1():
        with patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace-e2e-1"), \
             patch("engine.engine.summarize_agent_code", return_value=([], [])):
            task_result[0] = engine._execute_autonomous_reactive(
                "Open Chrome and navigate to google.com")

    task_thread = threading.Thread(target=run_task_1, daemon=True)
    task_thread.start()

    # Wait a beat, then send steer via API
    time.sleep(0.8)
    print("  → Sending steer via POST /api/steer")
    r = requests.post(f"{BASE}/api/steer", json={
        "text": "also search for Python tutorials",
        "remember": True,
    }, timeout=5)
    print(f"  ← Response: {r.status_code} {r.json()}")
    assert r.status_code == 200, f"Steer failed: {r.text}"
    assert r.json()["action"] == "steered"

    task_thread.join(timeout=15)
    assert task_result[0] is not None, "Task did not complete"
    print(f"\n  Task completed: status={task_result[0].status}")

    # Verify steer was consumed
    steer_in_goals = any("[OPERATOR STEER]: also search for Python tutorials" in g
                         for g in goals_seen)
    print(f"  Steer visible in predict goals: {steer_in_goals}")
    assert steer_in_goals, f"Steer not seen! Goals: {goals_seen}"

    # Verify chain_history marker
    marker_in_history = any("USER INTERVENED" in h for h in mock_agent._chain_history)
    print(f"  USER INTERVENED marker in chain_history: {marker_in_history}")
    assert marker_in_history

    # Verify events
    steered_events = [e for e in events_captured if e["type"] == "execution.steered"]
    print(f"  execution.steered events: {len(steered_events)}")
    assert len(steered_events) >= 1
    print("  ✓ TEST 1 PASSED")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 2: Halt → SuspendedTask created
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "─" * 70)
    print("TEST 2: Halt mid-run → verify SuspendedTask saved")
    print("─" * 70)

    events_captured.clear()
    step_count[0] = 0
    goals_seen.clear()
    mock_agent._chain_history = []

    def mock_predict_phase2(goal, step_idx, memory_hint="", plan_hint=""):
        step_count[0] += 1
        goals_seen.append(goal)
        result = MagicMock()
        result.raw = ""
        result.outcome = "action"
        result.code = "print('working')"
        time.sleep(0.3)
        return result

    mock_agent.predict_autonomous.side_effect = mock_predict_phase2

    def run_task_2():
        with patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace-e2e-2"), \
             patch("engine.engine.summarize_agent_code", return_value=([], [])):
            task_result[0] = engine._execute_autonomous_reactive(
                "Fill out the application form on LinkedIn")

    task_thread = threading.Thread(target=run_task_2, daemon=True)
    task_thread.start()

    time.sleep(1.5)  # let a few steps run
    print(f"  Steps completed before halt: {step_count[0]}")

    # Halt via engine directly (simulates relay_client receiving halt command)
    print("  → Requesting halt")
    engine.request_halt()
    task_thread.join(timeout=10)

    print(f"  Task completed: status={task_result[0].status}")
    assert task_result[0].status == "suspended"
    assert engine.is_suspended()

    ctx = engine._suspended_task
    print(f"  ✓ SuspendedTask saved:")
    print(f"    run_id: {ctx.run_id}")
    print(f"    goal: {ctx.goal[:50]}...")
    print(f"    step_index: {ctx.step_index}")
    print(f"    steps_done: {ctx.steps_done}")
    print(f"    chain_history length: {len(ctx.chain_history)}")

    suspended_events = [e for e in events_captured if e["type"] == "execution.suspended"]
    assert len(suspended_events) == 1
    print(f"  ✓ execution.suspended event emitted")
    print("  ✓ TEST 2 PASSED")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 3: Resume via /api/steer (suspended path) + __RESUME__ sentinel
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "─" * 70)
    print("TEST 3: Resume suspended task via /api/steer")
    print("─" * 70)

    events_captured.clear()

    print("  → Sending steer to suspended engine: 'skip form, upload resume instead'")
    r = requests.post(f"{BASE}/api/steer", json={
        "text": "skip form, upload resume instead",
        "remember": False,
    }, timeout=5)
    print(f"  ← Response: {r.status_code} {r.json()}")
    assert r.status_code == 200
    assert r.json()["action"] == "resumed"

    # Verify __RESUME__ in queue
    sentinel = remote_intents.get(timeout=3)
    assert sentinel == "__RESUME__"
    print(f"  ✓ __RESUME__ sentinel in intent queue")

    # Verify goal was amended on the suspended task
    assert "[OPERATOR STEER]: skip form, upload resume instead" in ctx.goal
    print(f"  ✓ Suspended task goal amended")

    # Verify chain_history marker added
    assert any("USER INTERVENED" in h for h in ctx.chain_history)
    print(f"  ✓ Chain history marker added")

    # Simulate main loop consuming the resume
    print("  → Simulating main loop resume (calling _execute_autonomous_reactive with ctx)")
    engine._suspended_task = None  # main loop clears this
    step_count[0] = 0
    goals_seen.clear()

    def mock_predict_resumed(goal, step_idx, memory_hint="", plan_hint=""):
        step_count[0] += 1
        goals_seen.append(goal)
        result = MagicMock()
        result.raw = ""
        if step_count[0] >= 2:
            result.outcome = "done"
            result.code = ""
        else:
            result.outcome = "action"
            result.code = "print('resuming')"
        time.sleep(0.2)
        return result

    mock_agent.predict_autonomous.side_effect = mock_predict_resumed
    mock_agent.reset_autonomous.reset_mock()

    with patch("engine.engine.rlog"), \
         patch("engine.engine.submit_trace"), \
         patch("engine.engine.current_trace_id", return_value="trace-e2e-3"), \
         patch("engine.engine.summarize_agent_code", return_value=([], [])):
        result = engine._execute_autonomous_reactive(
            ctx.goal, plan_hint=ctx.plan_hint, resume_ctx=ctx)

    assert result.status == "completed"
    print(f"  ✓ Resumed task completed: status={result.status}")

    # Verify reset_autonomous was NOT called (chain memory preserved)
    mock_agent.reset_autonomous.assert_not_called()
    print(f"  ✓ reset_autonomous NOT called (memory preserved)")

    # Verify agent saw the full amended goal
    assert any("skip form, upload resume instead" in g for g in goals_seen)
    print(f"  ✓ Agent saw amended goal on resume")

    # Verify execution.resumed event
    resumed_events = [e for e in events_captured if e["type"] == "execution.resumed"]
    assert len(resumed_events) == 1
    print(f"  ✓ execution.resumed event emitted")
    print("  ✓ TEST 3 PASSED")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 4: /api/new_task — compound halt + fresh intent
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "─" * 70)
    print("TEST 4: POST /api/new_task → halt + queue new intent")
    print("─" * 70)

    # Set up a fake suspended state to verify it gets cleared
    engine._suspended_task = SuspendedTask(
        run_id="old", task_key="k", goal="old task",
        plan_hint="", memory_hint="", step_index=5,
        variables={}, executed=[], chain_history=["turn 0: old"],
        interventions=[], graph=MagicMock(), halted_at=time.time(),
        steps_done=4,
    )
    engine._halt_flag.clear()

    print("  → POST /api/new_task: 'open Slack and check messages'")
    r = requests.post(f"{BASE}/api/new_task", json={
        "text": "open Slack and check messages",
    }, timeout=5)
    print(f"  ← Response: {r.status_code} {r.json()}")
    assert r.status_code == 200
    assert r.json()["action"] == "new_task"

    # Halt flag should be set
    assert engine._halt_flag.is_set()
    print(f"  ✓ Halt flag set")

    # New intent should be in queue
    intent = remote_intents.get(timeout=3)
    assert intent == "open Slack and check messages"
    print(f"  ✓ New intent queued: '{intent}'")
    print("  ✓ TEST 4 PASSED")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 5: Fail → suspended (operator can steer past failure)
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "─" * 70)
    print("TEST 5: Agent reports FAIL → SuspendedTask created (not terminal)")
    print("─" * 70)

    events_captured.clear()
    engine._halt_flag.clear()
    engine._suspended_task = None
    mock_agent._chain_history = []

    def mock_predict_fail(goal, step_idx, memory_hint="", plan_hint=""):
        result = MagicMock()
        result.raw = "Cannot find the upload button"
        result.outcome = "fail"
        result.code = ""
        return result

    mock_agent.predict_autonomous.side_effect = mock_predict_fail

    with patch("engine.engine.rlog"), \
         patch("engine.engine.submit_trace"), \
         patch("engine.engine.current_trace_id", return_value="trace-e2e-5"), \
         patch("engine.engine.summarize_agent_code", return_value=([], [])):
        result = engine._execute_autonomous_reactive("Upload resume to LinkedIn")

    assert result.status == "suspended"
    assert engine.is_suspended()
    ctx = engine._suspended_task
    print(f"  ✓ Fail produces suspended state (not terminal)")
    print(f"    reason in event: agent_reported_fail")

    suspended_events = [e for e in events_captured if e["type"] == "execution.suspended"]
    assert len(suspended_events) == 1
    assert suspended_events[0]["data"]["reason"] == "agent_reported_fail"
    print(f"  ✓ execution.suspended event with reason='agent_reported_fail'")
    print("  ✓ TEST 5 PASSED")

    # ══════════════════════════════════════════════════════════════════════
    # TEST 6: Post-predict steer check (steer during API call)
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "─" * 70)
    print("TEST 6: Steer arrives during predict → stale plan discarded")
    print("─" * 70)

    events_captured.clear()
    engine._halt_flag.clear()
    engine._suspended_task = None
    mock_agent._chain_history = []
    step_count[0] = 0
    goals_seen.clear()

    def mock_predict_with_race(goal, step_idx, memory_hint="", plan_hint=""):
        step_count[0] += 1
        goals_seen.append(goal)
        result = MagicMock()
        result.raw = ""

        if step_count[0] == 1:
            # Simulate steer arriving DURING the predict API call
            engine.request_steer("change to dark mode first")

        if step_count[0] >= 4:
            result.outcome = "done"
            result.code = ""
        else:
            result.outcome = "action"
            result.code = "print('action')"
        return result

    mock_agent.predict_autonomous.side_effect = mock_predict_with_race

    with patch("engine.engine.rlog"), \
         patch("engine.engine.submit_trace"), \
         patch("engine.engine.current_trace_id", return_value="trace-e2e-6"), \
         patch("engine.engine.summarize_agent_code", return_value=([], [])):
        result = engine._execute_autonomous_reactive("Open settings page")

    # The steer should have been consumed and visible in later predictions
    steer_seen = any("[OPERATOR STEER]: change to dark mode first" in g for g in goals_seen)
    assert steer_seen, f"Post-predict steer not consumed! Goals: {goals_seen}"
    print(f"  ✓ Post-predict steer consumed and visible in subsequent predictions")

    # Verify step.complete with deviation was emitted
    step_completes = [e for e in events_captured
                      if e["type"] == "step.complete"
                      and e.get("data", {}).get("deviation") == "plan_discarded_steer"]
    assert len(step_completes) >= 1
    print(f"  ✓ step.complete with deviation='plan_discarded_steer' emitted")
    print("  ✓ TEST 6 PASSED")

    # ══════════════════════════════════════════════════════════════════════
    # SUMMARY
    # ══════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("ALL 6 TESTS PASSED ✓")
    print("=" * 70)
    print("\nVerified end-to-end:")
    print("  1. Steer consumed at step boundary, goal + chain_history amended")
    print("  2. Halt creates SuspendedTask with full context")
    print("  3. Resume via /api/steer → __RESUME__ sentinel → continuation")
    print("  4. /api/new_task = compound halt + fresh intent dispatch")
    print("  5. Agent FAIL → suspended (not terminal, operator can steer past)")
    print("  6. Post-predict steer discards stale plan + emits deviation event")
    print("=" * 70)


if __name__ == "__main__":
    main()
