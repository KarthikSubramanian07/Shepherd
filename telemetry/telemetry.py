"""
ShepherdTelemetry — Arize Phoenix OpenTelemetry spans + Control Hub trace graph.
Never crashes the demo: all Phoenix calls wrapped in try/except.
Phoenix is a DEV instrument — open it in a separate browser window, not embedded in the Control Hub.

Local setup:
  1. Terminal 1: ./scripts/serve_phoenix.sh
  2. Terminal 2: uv run python main.py
  3. Open http://localhost:6006 → project "shepherd" → Traces
  4. Or Control Hub → center panel → Traces tab
"""
import contextlib
import logging
import time
from typing import Optional

from opentelemetry import trace
from opentelemetry.trace import format_span_id, format_trace_id

from config import FEATURES, ARIZE_PROJECT_NAME, PHOENIX_COLLECTOR_ENDPOINT
from dashboard.events import event_bus
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


def _current_span_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return format_span_id(ctx.span_id)
    return None


def _emit_span_start(name: str, trace_id: str, span_id: str, parent_span_id: str | None) -> None:
    event_bus.emit("trace.span.start", {
        "trace_id":       trace_id,
        "span_id":        span_id,
        "parent_span_id": parent_span_id,
        "name":           name,
        "started_at":     time.time(),
    })


def _emit_span_end(
    name: str, trace_id: str, span_id: str,
    duration_ms: int, status: str, attributes: dict,
) -> None:
    event_bus.emit("trace.span.end", {
        "trace_id":    trace_id,
        "span_id":     span_id,
        "name":        name,
        "duration_ms": duration_ms,
        "status":      status,
        "attributes":  attributes,
    })


def _collect_attrs(span, prefixes: tuple[str, ...] = ()) -> dict:
    attrs: dict[str, str] = {}
    raw = getattr(span, "_attributes", None) or getattr(span, "attributes", None)
    if not raw:
        return attrs
    for k, v in raw.items():
        key = str(k)
        if not prefixes or key.startswith(prefixes):
            attrs[key] = str(v)
    return attrs


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
    def span(self, name: str, *, oi_kind: Optional[str] = None):
        if self._tracer is None:
            yield _Noop()
            return
        parent_span_id = _current_span_id()
        t0 = time.perf_counter()
        status = "ok"
        span_kwargs: dict = {}
        if oi_kind:
            try:
                from openinference.semconv.trace import OpenInferenceSpanKindValues
                span_kwargs["openinference_span_kind"] = getattr(
                    OpenInferenceSpanKindValues, oi_kind.upper()
                )
            except (ImportError, AttributeError):
                pass
        entered = False
        try:
            with self._tracer.start_as_current_span(name, **span_kwargs) as s:
                sc = s.get_span_context()
                trace_id = format_trace_id(sc.trace_id)
                span_id = format_span_id(sc.span_id)
                try:
                    _emit_span_start(name, trace_id, span_id, parent_span_id)
                except Exception:
                    pass
                entered = True
                try:
                    yield s
                except Exception:
                    status = "error"
                    raise
                finally:
                    dur = int((time.perf_counter() - t0) * 1000)
                    try:
                        _emit_span_end(
                            name, trace_id, span_id, dur, status,
                            _collect_attrs(s, ("routine.", "action.", "step.", "workflow.", "error.")),
                        )
                    except Exception:
                        pass
        except Exception as e:
            if entered:
                raise
            print(f"[arize] span {name!r} failed (non-fatal): {e}")
            yield _Noop()

    def record(self, result: ExecutionResult, steps: list[StepRecord] | None = None) -> None:
        if self._tracer is None:
            return
        try:
            parent_span_id = _current_span_id()
            t0 = time.perf_counter()
            with self._tracer.start_as_current_span("routine.summary") as span:
                sc = span.get_span_context()
                trace_id = format_trace_id(sc.trace_id)
                span_id = format_span_id(sc.span_id)
                _emit_span_start("routine.summary", trace_id, span_id, parent_span_id)

                span.set_attribute("routine.id",          result.routine_id)
                span.set_attribute("routine.status",      result.status)
                span.set_attribute("routine.duration_ms", result.duration_ms)
                span.set_attribute("steps.completed",     result.steps_completed)
                for k, v in result.variables.items():
                    span.set_attribute(f"routine.variable.{k}", v)
                if result.error:
                    span.set_attribute("error.message", result.error)

                for step in (steps or []):
                    with self.span(f"step.{step.index}") as s:
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

                dur = int((time.perf_counter() - t0) * 1000)
                st = "error" if result.error else "ok"
                _emit_span_end(
                    "routine.summary", trace_id, span_id, dur, st,
                    _collect_attrs(span, ("routine.", "error.")),
                )
        except Exception as e:
            print(f"[arize] record failed (non-fatal): {e}")
