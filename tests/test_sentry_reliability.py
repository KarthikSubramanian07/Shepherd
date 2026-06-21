"""
Sentry reliability backbone — must be a clean no-op when Sentry is disabled, and
the event handler must never raise on the engine's event stream.
"""
from telemetry import sentry_init


def test_capture_intervention_noop_without_sentry(monkeypatch):
    monkeypatch.setitem(sentry_init.FEATURES, "sentry", False)
    # None of these may raise when Sentry is off.
    sentry_init.capture_intervention(decision="halt", reason="secret email", run_id="r1", step_index=4)
    sentry_init._on_event("execution.start", {"run_id": "r1", "routine_id": "x", "mode": "LIVE", "total_steps": 5})
    sentry_init._on_event("execution.halted", {"run_id": "r1", "reason": "external_send", "step_index": 4})


def test_event_handler_tolerates_missing_fields(monkeypatch):
    monkeypatch.setitem(sentry_init.FEATURES, "sentry", False)
    # Malformed/partial events must not raise.
    sentry_init._on_event("step.start", {})
    sentry_init._on_event("execution.complete", {})
    sentry_init._on_event("unknown.event", {"run_id": "r"})
