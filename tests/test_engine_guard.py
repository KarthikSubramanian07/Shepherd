"""
Engine ↔ arbiter integration: every actuation runs inside the injected lease
guard, and a solo engine (no guard) behaves unchanged.
"""
from contextlib import contextmanager, nullcontext

from engine.engine import ShepherdExecutionEngine
from shepherd_types import RoutineStep


class _FakeTelemetry:
    def span(self, *a, **k):
        return nullcontext()


class _FakeAgentS:
    """Stand-in so the test never constructs the real gui-agents adapter."""
    available = False

    def reset(self):
        pass

    def reset_autonomous(self):
        pass


def _engine(guard=None):
    return ShepherdExecutionEngine(
        coords={}, telemetry=_FakeTelemetry(), mode="AUTONOMOUS",
        agent_s=_FakeAgentS(), actuation_guard=guard,
        agent_id="agent-test", surface="LOCAL_DESKTOP",
    )


def test_actuation_runs_inside_guard():
    calls = {"in": 0, "out": 0}

    @contextmanager
    def guard():
        calls["in"] += 1
        try:
            yield
        finally:
            calls["out"] += 1

    eng = _engine(guard)
    eng._exec_agent_code("x = 1 + 1")                       # agent-code path
    eng._dispatch(RoutineStep(action="hotkey", keys=[]), {})  # deterministic path

    assert calls == {"in": 2, "out": 2}


def test_solo_engine_has_noop_guard():
    eng = _engine(guard=None)
    # Must not raise — the no-op guard wraps actuation transparently.
    eng._exec_agent_code("y = 2")


def test_actuation_blocks_then_cancels_on_halt():
    """When another agent holds the desktop, actuation blocks; a halt (cancel)
    surfaces as LeaseCancelled out of _exec_agent_code — never actuating."""
    import threading
    import time

    from orchestrator import surfaces
    from orchestrator.arbiter import ActionArbiter, LeaseCancelled

    arb = ActionArbiter()
    held = arb.acquire(surfaces.LOCAL_DESKTOP, "other")  # someone else holds it
    eng = _engine(guard=arb.guard(surfaces.LOCAL_DESKTOP, "agent-test"))
    flags = {}

    def actuate():
        try:
            eng._exec_agent_code("x = 1")   # blocks waiting for the lease
            flags["ran"] = True
        except LeaseCancelled:
            flags["cancelled"] = True

    t = threading.Thread(target=actuate)
    t.start()
    time.sleep(0.05)
    arb.cancel("agent-test")                # the halt path
    t.join(timeout=2)

    assert flags.get("cancelled") is True
    assert "ran" not in flags               # the code never executed
    arb.release(held)
