"""
GhostTelemetry — Arize Phoenix OpenTelemetry spans.
Never crashes the demo: all Phoenix calls wrapped in try/except.
Phoenix is a DEV instrument — open it in a separate browser window, not embedded in the Control Hub.
"""
import contextlib
from config import FEATURES, ARIZE_PROJECT_NAME
from shepherd_types import ExecutionResult, StepRecord


class _Noop:
    def set_attribute(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


class GhostTelemetry:
    def __init__(self) -> None:
        self._tracer = None
        if FEATURES["arize"]:
            try:
                from phoenix.otel import register
                tp = register(project_name=ARIZE_PROJECT_NAME, auto_instrument=True)
                self._tracer = tp.get_tracer("shepherd")
                print(f"[arize] Phoenix tracer active — project: {ARIZE_PROJECT_NAME}")
            except Exception as e:
                print(f"[arize] Phoenix unavailable (non-fatal): {e}")

    @contextlib.contextmanager
    def span(self, name: str):
        if self._tracer is None:
            yield _Noop()
            return
        try:
            with self._tracer.start_as_current_span(name) as s:
                yield s
        except Exception:
            yield _Noop()

    def record(self, result: ExecutionResult, steps: list[StepRecord] | None = None) -> None:
        if self._tracer is None:
            return
        try:
            with self._tracer.start_as_current_span("routine.summary") as span:
                span.set_attribute("routine.id",          result.routine_id)
                span.set_attribute("routine.status",      result.status)
                span.set_attribute("routine.duration_ms", result.duration_ms)
                span.set_attribute("steps.completed",     result.steps_completed)
                for k, v in result.variables.items():
                    span.set_attribute(f"routine.variable.{k}", v)
                if result.error:
                    span.set_attribute("error.message", result.error)
        except Exception as e:
            print(f"[arize] record failed (non-fatal): {e}")
