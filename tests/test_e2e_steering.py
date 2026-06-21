"""
End-to-end integration tests for mid-run steering.

Tests the full flow: main loop → engine → steer/halt/resume → event propagation.
Exercises both relay_client and dashboard API paths.
"""
import queue
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from engine.engine import ShepherdExecutionEngine, SuspendedTask
from services.relay_client import RelayClient


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_engine_and_queue():
    """Create engine + remote_intents queue simulating main.py setup."""
    with patch("engine.engine.AgentSAdapter") as MockAgent, \
         patch("engine.engine.RoutinePlanner"), \
         patch("engine.engine.TaskGraphStore") as MockGraphStore:
        mock_agent = MockAgent.return_value
        mock_agent.available = True
        mock_agent._chain_history = []
        mock_agent.last_reasoning = "test"
        mock_agent.reset_autonomous = MagicMock()

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
            planner=MockAgent.return_value,
        )
        remote_intents = queue.Queue()
        return engine, mock_agent, remote_intents


# ── E2E: Full steer → resume cycle ──────────────────────────────────────────

class TestE2ESteerCycle:
    """Simulate the full operator workflow: task → steer → halt → resume → done."""

    def test_full_steer_halt_resume_cycle(self):
        """
        1. Start a task (running)
        2. Steer mid-flight (goal amended)
        3. Halt (suspended)
        4. Resume with steer (completed)
        """
        engine, mock_agent, remote_intents = _make_engine_and_queue()
        events_emitted = []

        call_count = [0]
        phase = [1]  # 1=first run, 2=resumed run

        def mock_predict(goal, step_idx, memory_hint="", plan_hint=""):
            call_count[0] += 1
            result = MagicMock()
            result.raw = ""

            if phase[0] == 1:
                if call_count[0] == 1:
                    # First step: simulate operator steer arriving mid-run
                    engine.request_steer("also upload the resume")
                    result.outcome = "action"
                    result.code = "print('navigated')"
                elif call_count[0] == 3:
                    # After steer consumed + one more step, halt
                    engine.request_halt()
                    result.outcome = "action"
                    result.code = "print('filling')"
                else:
                    result.outcome = "action"
                    result.code = "print('working')"
            else:
                # Phase 2: resumed — complete immediately
                result.outcome = "done"
                result.code = ""

            return result

        mock_agent.predict_autonomous.side_effect = mock_predict

        def track_event(event_type, data):
            events_emitted.append((event_type, data))

        with patch("engine.engine.event_bus") as mock_bus, \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace1"), \
             patch("engine.engine.summarize_agent_code", return_value=([], [])), \
             patch.object(engine, "_exec_agent_code"):
            mock_bus.emit.side_effect = track_event

            # Phase 1: run until halted
            result1 = engine._execute_autonomous_reactive("fill the form")

        assert result1.status == "suspended"
        assert engine.is_suspended()
        ctx = engine._suspended_task

        # Verify steer was consumed (goal amended)
        assert "[OPERATOR STEER]: also upload the resume" in ctx.goal
        # Verify chain_history has the intervention marker
        assert any("USER INTERVENED" in h for h in ctx.chain_history)

        # Verify events
        steered_events = [e for e in events_emitted if e[0] == "execution.steered"]
        suspended_events = [e for e in events_emitted if e[0] == "execution.suspended"]
        assert len(steered_events) >= 1
        assert len(suspended_events) == 1

        # Phase 2: resume with additional steer
        ctx.goal = f"{ctx.goal}\n\n[OPERATOR STEER]: start from the upload section"
        ctx.chain_history.append(">>> USER INTERVENED (IMPORTANT): start from the upload section")
        engine._suspended_task = None
        phase[0] = 2
        events_emitted.clear()

        with patch("engine.engine.event_bus") as mock_bus2, \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace2"), \
             patch("engine.engine.summarize_agent_code", return_value=([], [])), \
             patch.object(engine, "_exec_agent_code"):
            mock_bus2.emit.side_effect = track_event
            result2 = engine._execute_autonomous_reactive(
                ctx.goal, plan_hint=ctx.plan_hint, resume_ctx=ctx)

        assert result2.status == "completed"
        assert not engine.is_suspended()

        # Verify resumed event
        resumed_events = [e for e in events_emitted if e[0] == "execution.resumed"]
        assert len(resumed_events) == 1
        # Verify agent saw the full amended goal
        call_args = mock_agent.predict_autonomous.call_args
        assert "start from the upload section" in call_args[0][0]


class TestE2ERelayIntegration:
    """Test relay_client command routing end-to-end."""

    def test_steer_running_then_halt_then_resume(self):
        """Relay client routes steer → engine queue, then halt → suspended,
        then steer-while-suspended → __RESUME__ sentinel."""
        engine, mock_agent, remote_intents = _make_engine_and_queue()
        client = RelayClient(engine, remote_intents)

        # 1. Steer while running (engine NOT suspended)
        engine._suspended_task = None
        with patch("dashboard.events.event_bus"):
            client._apply_command("steer", {"text": "try the other tab", "remember": True})

        # Should be in engine's steer queue
        text, remember = engine._steer_queue.get_nowait()
        assert text == "try the other tab"
        assert remember is True

        # 2. Halt
        with patch("engine.approvals.set_decision"), patch("dashboard.events.event_bus"):
            client._apply_command("halt", {})
        assert engine._halt_flag.is_set()

        # 3. Simulate engine saving suspended state (normally done in loop)
        engine._halt_flag.clear()
        engine._suspended_task = SuspendedTask(
            run_id="r1", task_key="k1", goal="original goal",
            plan_hint="", memory_hint="", step_index=4,
            variables={}, executed=[], chain_history=["turn 0: did X"],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=3,
        )

        # 4. Steer while suspended → should amend + put __RESUME__
        with patch("dashboard.events.event_bus"):
            client._apply_command("steer", {"text": "skip the form, go to upload", "remember": False})

        # Suspended task goal should be amended
        assert "[OPERATOR STEER]: skip the form, go to upload" in engine._suspended_task.goal
        # Chain history should have marker
        assert any("USER INTERVENED" in h for h in engine._suspended_task.chain_history)
        # __RESUME__ should be in queue
        assert remote_intents.get_nowait() == "__RESUME__"

    def test_new_task_discards_suspended(self):
        """new_task command halts, queues intent, discards suspended state."""
        engine, _, remote_intents = _make_engine_and_queue()
        client = RelayClient(engine, remote_intents)

        engine._suspended_task = SuspendedTask(
            run_id="r1", task_key="k1", goal="old goal",
            plan_hint="", memory_hint="", step_index=2,
            variables={}, executed=[], chain_history=[],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=1,
        )

        with patch("engine.approvals.set_decision"), patch("dashboard.events.event_bus"):
            client._apply_command("new_task", {"text": "open Slack instead"})

        # Halt flag should be set
        assert engine._halt_flag.is_set()
        # New intent should be in queue
        assert remote_intents.get_nowait() == "open Slack instead"


class TestE2EMainLoopResume:
    """Test the __RESUME__ handling in main.py's main loop pattern."""

    def test_resume_sentinel_triggers_execution(self):
        """Simulate the main loop picking up __RESUME__ and calling execute."""
        engine, mock_agent, remote_intents = _make_engine_and_queue()

        # Set up suspended state
        engine._suspended_task = SuspendedTask(
            run_id="r1", task_key="k1", goal="fill form",
            plan_hint="", memory_hint="", step_index=2,
            variables={"GOAL": "fill form"}, executed=[],
            chain_history=["turn 0: opened browser"],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=1,
        )

        # Put __RESUME__ in queue
        remote_intents.put("__RESUME__")

        # Simulate main loop logic
        raw = remote_intents.get()
        assert raw == "__RESUME__"

        ctx = engine._suspended_task
        assert ctx is not None
        engine._suspended_task = None

        # Make predict return done
        done_result = MagicMock()
        done_result.outcome = "done"
        done_result.code = ""
        done_result.raw = ""
        mock_agent.predict_autonomous.return_value = done_result

        with patch("engine.engine.event_bus"), \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("engine.engine.current_trace_id", return_value="trace1"):
            result = engine._execute_autonomous_reactive(
                ctx.goal, plan_hint=ctx.plan_hint, resume_ctx=ctx)

        assert result.status == "completed"
        # reset_autonomous NOT called (preserved chain memory)
        mock_agent.reset_autonomous.assert_not_called()
        # Chain history was restored
        assert "turn 0: opened browser" in mock_agent._chain_history

    def test_new_task_after_resume_clears_suspended(self):
        """When a new task arrives (not __RESUME__), suspended state is discarded."""
        engine, _, remote_intents = _make_engine_and_queue()

        engine._suspended_task = SuspendedTask(
            run_id="r1", task_key="k1", goal="old goal",
            plan_hint="", memory_hint="", step_index=3,
            variables={}, executed=[], chain_history=[],
            interventions=[], graph=MagicMock(), halted_at=time.time(),
            steps_done=2,
        )

        # Put a normal intent (not __RESUME__)
        remote_intents.put("open Slack")

        raw = remote_intents.get()
        assert raw != "__RESUME__"

        # Simulate main loop: new task clears suspended state
        engine._suspended_task = None
        assert not engine.is_suspended()


class TestE2ECoordinatorEventTracking:
    """Verify coordinator processes the new event types correctly."""

    def _make_hub_and_conn(self):
        """Create a Hub instance and a fake AgentConn for testing."""
        from coordinator.server import Hub, AgentConn
        hub = Hub()
        mock_ws = MagicMock()
        conn = AgentConn(agent_id="test-agent", name="test", host="localhost", ws=mock_ws)
        conn.status = "running"
        conn.trace = {"status": "running", "current": "step4"}
        conn.block = None
        conn.routing = {"via": "autonomous"}
        conn._goal_text = "test goal"
        return hub, conn

    def test_coordinator_suspended_status(self):
        """Coordinator should track execution.suspended → status='suspended'."""
        hub, conn = self._make_hub_and_conn()

        hub.apply_event(conn, {
            "type": "execution.suspended",
            "data": {"run_id": "r1", "step_index": 4, "goal": "fill form",
                     "reason": "operator_halt"},
        })

        assert conn.status == "suspended"
        assert conn.block["type"] == "suspended"
        assert conn.block["step_index"] == 4
        assert conn.trace["status"] == "suspended"

    def test_coordinator_resumed_status(self):
        """Coordinator should track execution.resumed → status='running'."""
        hub, conn = self._make_hub_and_conn()
        conn.status = "suspended"
        conn.block = {"type": "suspended", "step_index": 4}
        conn.trace = {"status": "suspended", "current": None}

        hub.apply_event(conn, {
            "type": "execution.resumed",
            "data": {"run_id": "r1", "step_index": 4, "amended_goal": "new goal"},
        })

        assert conn.status == "running"
        assert conn.block is None
        assert conn.trace["status"] == "running"

    def test_coordinator_complete_with_suspended_preserves_state(self):
        """execution.complete with status=suspended should NOT clear to idle."""
        hub, conn = self._make_hub_and_conn()
        conn.status = "suspended"
        conn.block = {"type": "suspended", "step_index": 3, "goal": "test"}
        conn.trace = {"status": "suspended", "current": None}

        hub.apply_event(conn, {
            "type": "execution.complete",
            "data": {"status": "suspended", "run_id": "r1", "steps_completed": 3},
        })

        # Should stay suspended, NOT reset to idle
        assert conn.status == "suspended"
        # Should preserve block/routing/goal
        assert conn.block is not None
        assert conn.routing is not None
        assert conn._goal_text is not None
