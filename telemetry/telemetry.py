"""
ShepherdTelemetry — Arize Phoenix OpenTelemetry spans.
Never crashes the demo: all Phoenix calls wrapped in try/except.
Phoenix is a DEV instrument — open it in a separate browser window, not embedded in the Control Hub.

Local setup:
  1. Terminal 1: ./scripts/serve_phoenix.sh
  2. Terminal 2: uv run python main.py
  3. Open http://localhost:6006 → project "shepherd" → Traces
"""
import contextlib
import logging
from config import FEATURES, ARIZE_PROJECT_NAME, PHOENIX_COLLECTOR_ENDPOINT
from shepherd_types import ExecutionResult, StepRecord

# Suppress OTel export errors — Phoenix not running is non-fatal noise
logging.getLogger("opentelemetry.sdk.trace.export").setLevel(logging.CRITICAL)
logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.CRITICAL)


class _Noop:
    def set_attribute(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass


def _traces_endpoint(base: str) -> str:
    base = base.rstrip("/")
    if base.endswith("/v1/traces"):
        return base
    return f"{base}/v1/traces"


class ShepherdTelemetry:
    def __init__(self) -> None:
        self._tracer = None
        if FEATURES["arize"]:
            try:
                from phoenix.otel import register
                endpoint = _traces_endpoint(PHOENIX_COLLECTOR_ENDPOINT)
                tp = register(
                    project_name=ARIZE_PROJECT_NAME,
                    endpoint=endpoint,
                    protocol="http/protobuf",
                    auto_instrument=True,
                )
                self._tracer = tp.get_tracer("shepherd")
                print(f"[arize] Phoenix tracer active — project: {ARIZE_PROJECT_NAME} → {endpoint}")
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

                for step in (steps or []):
                    with self._tracer.start_as_current_span(f"step.{step.index}") as s:
                        s.set_attribute("step.index",       step.index)
                        s.set_attribute("step.action",      step.action or "")
                        s.set_attribute("step.status",      step.status)
                        s.set_attribute("step.duration_ms", step.duration_ms)
                        if step.target:
                            s.set_attribute("step.target", step.target)
                        if step.deviation:
                            s.set_attribute("step.deviation", step.deviation)
                        if step.error:
                            s.set_attribute("step.error", step.error)
        except Exception as e:
            print(f"[arize] record failed (non-fatal): {e}")
