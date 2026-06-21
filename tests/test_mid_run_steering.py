"""
Tests for mid-run steering: steer queue, SuspendedTask, resume, relay routing.
"""
import queue
import time
from unittest.mock import MagicMock, patch


from engine.engine import ShepherdExecutionEngine, SuspendedTask


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_engine():
    """Create a minimal engine with mocked dependencies."""
    with patch("engine.engine.AgentSAdapter") as MockAgent, \
         patch("engine.engine.RoutinePlanner") as MockPlanner, \
         patch("engine.engine.TaskGraphStore") as MockGraphStore:
        mock_agent = MockAgent.return_value
        mock_agent.available = True
        mock_agent._chain_history = []
        mock_agent.last_reasoning = "test action"
        mock_agent.reset_autonomous = MagicMock()

        mock_planner = MockPlanner.return_value

        mock_graph_store = MockGraphStore.return_value
        mock_graph = MagicMock()
        mock_graph.nodes = []
        mock_graph.run_count = 0
        mock_graph_store.load.return_value = mock_graph
        mock_graph_store.is_known.return_value = False

        telemetry = MagicMock()
        telemetry.span.return_value.__enter__ = MagicMock(return_value=MagicMock())
        telemetry.span.return_value.__exit__ = MagicMock(return_value=False)

        engine = ShepherdExecutionEngine(
            coords={}, telemetry=telemetry,
            mode="AUTONOMOUS", agent_s=mock_agent,
            planner=mock_planner,
        )
        return engine, mock_agent


# ── Tests: Engine steer queue ────────────────────────────────────────────────

class TestSteerQueue:
    def test_request_steer_enqueues(self):
        engine, _ = _make_engine()
        engine.request_steer("upload resume.pdf", remember=True)
        assert not engine._steer_queue.empty()
        text, remember = engine._steer_queue.get_nowait()
        assert text == "upload resume.pdf"
        assert remember is True

    def test_request_steer_multiple(self):
        engine, _ = _make_engine()
        engine.request_steer("first steer")
        engine.request_steer("second steer", remember=False)
        items = []
        while not engine._steer_queue.empty():
            items.append(engine._steer_queue.get_nowait())
        assert len(items) == 2
        assert items[0] == ("first steer", True)
        assert items[1] == ("second steer", False)

    def test_is_suspended_false_initially(self):
        engine, _ = _make_engine()
        assert engine.is_suspended() is False

    def test_is_suspended_true_after_assignment(self):
        engine, _ = _make_engine()
        engine._suspended_task = SuspendedTask(
            run_id="abc", task_key="test", goal="test goal",
            plan_hint="", memory_hint="", step_index=3,
            variables={"GOAL": "test"}, executed=[], chain_history=[],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=2,
        )
        assert engine.is_suspended() is True


# ── Tests: Halt produces SuspendedTask ───────────────────────────────────────

class TestHaltSuspend:
    def test_halt_saves_suspended_task(self):
        engine, mock_agent = _make_engine()

        call_count = [0]

        def mock_predict(goal, step_idx, memory_hint="", plan_hint=""):
            call_count[0] += 1
            # After first step completes, set halt so second step sees it
            if call_count[0] == 2:
                engine.request_halt()
            result = MagicMock()
            result.outcome = "action"
            result.code = "pass"
            result.raw = ""
            return result

        mock_agent.predict_autonomous.side_effect = mock_predict

        with patch("engine.engine.event_bus"), \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace123"):
            result = engine._execute_autonomous_reactive("fill form")

        # Should be suspended (halt detected at step boundary before step 3)
        assert result.status == "suspended"
        assert engine._suspended_task is not None
        ctx = engine._suspended_task
        assert ctx.goal == "fill form"
        assert ctx.run_id is not None

    def test_fail_saves_suspended_task(self):
        engine, mock_agent = _make_engine()

        # Make predict return "fail"
        fail_result = MagicMock()
        fail_result.outcome = "fail"
        fail_result.code = ""
        fail_result.raw = "cannot find button"
        mock_agent.predict_autonomous.return_value = fail_result

        with patch("engine.engine.event_bus"), \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace123"):
            result = engine._execute_autonomous_reactive("click button")

        assert result.status == "suspended"
        assert engine._suspended_task is not None
        assert "cannot find button" in (result.error or "")


# ── Tests: Steer integration into goal ───────────────────────────────────────

class TestSteerGoalInjection:
    def test_steer_amends_goal_in_loop(self):
        engine, mock_agent = _make_engine()

        call_count = [0]
        goals_seen = []

        def mock_predict(goal, step_idx, memory_hint="", plan_hint=""):
            call_count[0] += 1
            # After step 1 prediction, simulate a steer arriving (operator sends mid-run)
            if call_count[0] == 1:
                engine.request_steer("also upload resume")
            goals_seen.append(goal)
            result = MagicMock()
            if call_count[0] >= 4:
                result.outcome = "done"
            else:
                result.outcome = "action"
                result.code = "print('ok')"
            result.raw = ""
            return result

        mock_agent.predict_autonomous.side_effect = mock_predict

        with patch("engine.engine.event_bus"), \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace123"), \
             patch("engine.engine.summarize_agent_code", return_value=([], [])), \
             patch.object(engine, "_exec_agent_code"):
            result = engine._execute_autonomous_reactive("fill form")

        # The steer was queued during step 1's predict. It gets consumed either:
        # - In the post-predict steer check (discards step 1's plan, continues loop)
        # - At the top of step 2's iteration (amends goal before predict)
        # Either way, a later predict should see the amended goal
        steer_seen = any("[OPERATOR STEER]: also upload resume" in g for g in goals_seen)
        assert steer_seen, f"Steer not visible in any goal: {goals_seen}"
        # Chain history should have the marker
        assert any("USER INTERVENED" in h for h in mock_agent._chain_history)

    def test_steer_during_api_call_discards_stale_plan(self):
        engine, mock_agent = _make_engine()

        call_count = [0]

        def mock_predict(goal, step_idx, memory_hint="", plan_hint=""):
            call_count[0] += 1
            # On first call, simulate a steer arriving during API call
            if call_count[0] == 1:
                engine.request_steer("change direction")
            result = MagicMock()
            if call_count[0] >= 3:
                result.outcome = "done"
            else:
                result.outcome = "action"
                result.code = "print('ok')"
            result.raw = ""
            return result

        mock_agent.predict_autonomous.side_effect = mock_predict

        with patch("engine.engine.event_bus"), \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace123"), \
             patch("engine.engine.summarize_agent_code", return_value=([], [])), \
             patch.object(engine, "_exec_agent_code"):
            result = engine._execute_autonomous_reactive("original goal")

        # The first prediction's result should have been discarded (steer arrived)
        # So we need at least 3 calls: 1 (stale, discarded) + 2 actual
        assert call_count[0] >= 3


# ── Tests: Resume ────────────────────────────────────────────────────────────

class TestResume:
    def test_resume_restores_context(self):
        engine, mock_agent = _make_engine()

        # Set up a suspended task
        engine._suspended_task = SuspendedTask(
            run_id="run123", task_key="key123",
            goal="fill form\n\n[OPERATOR STEER]: also upload resume",
            plan_hint="", memory_hint="", step_index=3,
            variables={"GOAL": "fill form"},
            executed=[],
            chain_history=["turn 0: opened Chrome", "turn 1: clicked Apply",
                          ">>> USER INTERVENED (IMPORTANT): also upload resume"],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=2,
        )

        # Make predict return done immediately
        done_result = MagicMock()
        done_result.outcome = "done"
        done_result.code = ""
        done_result.raw = ""
        mock_agent.predict_autonomous.return_value = done_result

        ctx = engine._suspended_task
        engine._suspended_task = None

        with patch("engine.engine.event_bus") as mock_bus, \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace123"):
            result = engine._execute_autonomous_reactive(
                ctx.goal, plan_hint=ctx.plan_hint, resume_ctx=ctx)

        # Should NOT have called reset_autonomous (preserve memory)
        mock_agent.reset_autonomous.assert_not_called()
        # Chain history should be restored
        assert "turn 0: opened Chrome" in mock_agent._chain_history
        # execution.resumed event should have been emitted
        resumed_calls = [c for c in mock_bus.emit.call_args_list
                        if c[0][0] == "execution.resumed"]
        assert len(resumed_calls) == 1


# ── Tests: Relay client steer routing ────────────────────────────────────────

class TestRelaySteerRouting:
    def test_steer_while_running_calls_request_steer(self):
        engine, _ = _make_engine()
        remote_intents = queue.Queue()

        from services.relay_client import RelayClient
        client = RelayClient(engine, remote_intents)

        # Engine is NOT suspended
        engine._suspended_task = None
        client._apply_command("steer", {"text": "do X instead", "remember": True})

        # Should have put steer in queue
        text, remember = engine._steer_queue.get_nowait()
        assert text == "do X instead"
        assert remember is True

    def test_steer_while_suspended_sends_resume(self):
        engine, mock_agent = _make_engine()
        remote_intents = queue.Queue()

        from services.relay_client import RelayClient
        client = RelayClient(engine, remote_intents)

        # Engine IS suspended
        engine._suspended_task = SuspendedTask(
            run_id="abc", task_key="key", goal="original",
            plan_hint="", memory_hint="", step_index=2,
            variables={}, executed=[], chain_history=["turn 0: did X"],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=1,
        )

        with patch("dashboard.events.event_bus"):
            client._apply_command("steer", {"text": "try the other button", "remember": False})

        # Should have amended suspended task's goal
        assert "[OPERATOR STEER]: try the other button" in engine._suspended_task.goal
        # Should have put __RESUME__ in intent queue
        assert remote_intents.get_nowait() == "__RESUME__"

    def test_new_task_command_halts_and_queues(self):
        engine, _ = _make_engine()
        remote_intents = queue.Queue()

        from services.relay_client import RelayClient
        client = RelayClient(engine, remote_intents)

        with patch("engine.approvals.set_decision"), \
             patch("dashboard.events.event_bus"):
            client._apply_command("new_task", {"text": "open Slack"})

        # Should have set halt flag
        assert engine._halt_flag.is_set()
        # Should have queued the new intent
        assert remote_intents.get_nowait() == "open Slack"


# ── Tests: Queue drain on fresh start ────────────────────────────────────────

class TestQueueDrain:
    def test_stale_steers_drained_on_new_task(self):
        engine, mock_agent = _make_engine()

        # Put stale steers in queue
        engine.request_steer("stale 1")
        engine.request_steer("stale 2")

        # Make predict return done immediately
        done_result = MagicMock()
        done_result.outcome = "done"
        done_result.code = ""
        done_result.raw = ""
        mock_agent.predict_autonomous.return_value = done_result

        with patch("engine.engine.event_bus"), \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace123"):
            result = engine._execute_autonomous_reactive("brand new task")

        # Stale steers should have been drained — agent shouldn't see them
        # (they won't appear in the goal since they're drained before the loop)
        call_args = mock_agent.predict_autonomous.call_args
        goal_seen = call_args[0][0]
        assert "stale 1" not in goal_seen
        assert "stale 2" not in goal_seen
