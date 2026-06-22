"""
Tests for agent-initiated help signal: the agent can proactively suspend
itself when it encounters obstacles requiring human assistance (CAPTCHAs,
login walls, missing credentials, unknown field values).
"""
from unittest.mock import MagicMock, patch

from engine.agent_s_adapter import _terminal_outcome
from engine.engine import ShepherdExecutionEngine
from shepherd_types import AutonomousStepResult


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
        mock_agent.observations = []

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


# ── Tests: _terminal_outcome parses "help" ───────────────────────────────────

class TestTerminalOutcomeHelp:
    def test_help_uppercase(self):
        assert _terminal_outcome("HELP") == "help"

    def test_help_lowercase(self):
        assert _terminal_outcome("help") == "help"

    def test_help_with_period(self):
        assert _terminal_outcome("HELP.") == "help"

    def test_help_is_not_actionable(self):
        from engine.agent_s_adapter import _is_actionable
        assert _is_actionable("HELP") is False

    def test_done_still_works(self):
        assert _terminal_outcome("DONE") == "done"

    def test_fail_still_works(self):
        assert _terminal_outcome("FAIL") == "fail"

    def test_wait_still_works(self):
        assert _terminal_outcome("WAIT") == "wait"

    def test_action_still_works(self):
        assert _terminal_outcome("pyautogui.click(100, 200)") == "action"


# ── Tests: AutonomousStepResult with help outcome ────────────────────────────

class TestHelpStepResult:
    def test_help_outcome_basic(self):
        result = AutonomousStepResult(
            outcome="help",
            raw="I see a CAPTCHA on the Google search page",
        )
        assert result.outcome == "help"
        assert "CAPTCHA" in result.raw

    def test_help_outcome_with_reasoning(self):
        result = AutonomousStepResult(
            outcome="help",
            raw="I need login credentials for this portal — the page shows a login form",
        )
        assert result.outcome == "help"
        assert "credentials" in result.raw

    def test_help_outcome_workflow(self):
        result = AutonomousStepResult(
            outcome="help",
            raw="The form requires a social security number but I don't have it",
            next="SAME",
            branch=None,
            extracted={},
            completed=[],
        )
        assert result.outcome == "help"
        assert result.next == "SAME"


# ── Tests: Engine help → suspended ───────────────────────────────────────────

class TestEngineHelpSuspend:
    def test_help_creates_suspended_task(self):
        engine, mock_agent = _make_engine()

        events_emitted = []
        original_emit = None
        try:
            from dashboard.events import event_bus
            original_emit = event_bus.emit
            event_bus.emit = lambda t, d=None: events_emitted.append((t, d))

            # Agent returns "help" on first step
            mock_agent.predict_autonomous = MagicMock(
                return_value=AutonomousStepResult(
                    outcome="help",
                    raw="I see a CAPTCHA that I cannot solve",
                )
            )

            with patch("engine.engine.AUTONOMOUS_MAX_STEPS", 5), \
                 patch("engine.engine.AUTONOMOUS_PLAN_FIRST", False), \
                 patch("engine.engine._cfg") as mock_cfg, \
                 patch("engine.engine.rlog"), \
                 patch("engine.engine.submit_trace"), \
                 patch("telemetry.agent_trace.summarize_agent_code",
                        return_value=(None, None)):
                mock_cfg.FEATURES = {}

                result = engine.execute_autonomous("test help flow")

            # Should be suspended, not failed
            assert result.status == "suspended"
            assert engine._suspended_task is not None
            assert engine._suspended_task.goal == "test help flow"

            # Should have emitted step.help_requested and execution.suspended
            help_events = [e for e in events_emitted if e[0] == "step.help_requested"]
            suspend_events = [e for e in events_emitted
                              if e[0] == "execution.suspended"]
            assert len(help_events) >= 1
            assert "CAPTCHA" in help_events[0][1]["help_message"]
            assert len(suspend_events) >= 1
            assert suspend_events[0][1]["reason"] == "agent_requested_help"
            assert "CAPTCHA" in suspend_events[0][1]["help_message"]

        finally:
            if original_emit:
                from dashboard.events import event_bus
                event_bus.emit = original_emit

    def test_help_preserves_chain_history(self):
        engine, mock_agent = _make_engine()

        call_count = 0
        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                mock_agent._chain_history.append("pressed: click(100,200)")
                return AutonomousStepResult(outcome="action", code="pyautogui.click(100, 200)")
            return AutonomousStepResult(outcome="help", raw="Need credentials")

        mock_agent.predict_autonomous = MagicMock(side_effect=side_effect)

        with patch("engine.engine.AUTONOMOUS_MAX_STEPS", 5), \
             patch("engine.engine.AUTONOMOUS_PLAN_FIRST", False), \
             patch("engine.engine._cfg") as mock_cfg, \
             patch("engine.engine.rlog"), \
             patch("engine.engine.submit_trace"), \
             patch("dashboard.events.event_bus.emit"), \
             patch("telemetry.agent_trace.summarize_agent_code",
                    return_value=(None, None)):
            mock_cfg.FEATURES = {}

            # Mock the code execution
            with patch.object(engine, "_exec_agent_code"):
                result = engine.execute_autonomous("test preserve history")

        assert result.status == "suspended"
        # Chain history from step 1 should be preserved in SuspendedTask
        assert len(engine._suspended_task.chain_history) >= 1
        assert "click(100,200)" in engine._suspended_task.chain_history[0]


# ── Tests: Help outcome in prompt ────────────────────────────────────────────

class TestHelpPromptInclusion:
    def test_autonomous_prompt_includes_help(self):
        """Verify the Agent S chained planning prompt mentions 'help' status."""
        from engine.agent_s_adapter import AgentSAdapter
        import inspect
        # The help prompt is in _plan_chain (the chained planning method)
        real_source = inspect.getsource(AgentSAdapter._plan_chain)
        assert "help" in real_source
        assert "captcha" in real_source.lower()

    def test_workflow_prompt_includes_help(self):
        """Verify the workflow chain prompt mentions 'help' status."""
        from engine.agent_s_adapter import AgentSAdapter
        import inspect
        source = inspect.getsource(AgentSAdapter.plan_workflow_chain)
        assert "help" in source
        assert "captcha" in source.lower()
