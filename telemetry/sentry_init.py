import os
from typing import Any, Optional

from config import FEATURES, SENTRY_DSN
from telemetry.phoenix_client import phoenix_trace_url, resolve_project_slug


def _release() -> Optional[str]:
    """Best-effort package version for Sentry release tagging."""
    try:
        from importlib.metadata import version
        return f"shepherd@{version('shepherd')}"
    except Exception:
        return None


def _attach_replay(scope, run_id: Optional[str]) -> None:
    """Attach the agent's session-replay filmstrip (screenshots + manifest) for
    this run to the Sentry scope. Best-effort; never raises."""
    try:
        from telemetry import session_replay
        session_replay.attach_to_scope(scope, run_id)
    except Exception:
        pass


def _link_phoenix(scope, trace_id: Optional[str]) -> None:
    """Attach the Phoenix/OTel trace id to a Sentry scope for cross-linking.

    Falls back to the active OTel span's trace id when none is passed (so events
    raised inside a live span are linked automatically).
    """
    if trace_id is None:
        try:
            from telemetry.telemetry import current_trace_id
            trace_id = current_trace_id()
        except Exception:
            trace_id = None
    if not trace_id:
        return
    trace_url = phoenix_trace_url(trace_id)
    scope.set_tag("phoenix.trace_id", trace_id)
    scope.set_context("phoenix", {
        "trace_id": trace_id,
        "trace_url": trace_url,  # Sentry renders http(s) URLs as clickable links
        "project": resolve_project_slug(),
    })


def init_sentry() -> None:
    if not FEATURES["sentry"]:
        return
    try:
        import sentry_sdk
        from sentry_sdk.integrations.threading import ThreadingIntegration
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            # Tracing ON by default so every run is a performance transaction
            # (override with SENTRY_TRACES_SAMPLE_RATE). The engine runs in a daemon
            # thread, so propagate the scope into it.
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
            send_default_pii=False,
            environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
            release=_release(),
            integrations=[ThreadingIntegration(propagate_scope=True)],
        )
        print("[sentry] Initialized.")
        install()
    except Exception as e:
        print(f"[sentry] Init failed (non-fatal): {e}")


# ── reliability backbone: runs are transactions, halts are structured issues ──

_txns: dict = {}
_spans: dict = {}
_installed = False


def install() -> None:
    """Subscribe to the engine event stream so every run becomes a Sentry
    performance transaction (with a child span per milestone) and every halt a
    structured, queryable issue. No-op without a DSN. Off the click path."""
    global _installed
    if _installed or not FEATURES["sentry"]:
        return
    try:
        from dashboard.events import event_bus
        event_bus.subscribe(_on_event)
        _installed = True
        print("[sentry] reliability tracing active — runs are transactions, halts are issues.")
    except Exception as e:
        print(f"[sentry] install failed (non-fatal): {e}")


def _on_event(event_type: str, data: dict) -> None:
    if not FEATURES["sentry"]:
        return
    try:
        import sentry_sdk
        run_id = data.get("run_id") or ""
        if event_type == "execution.start":
            t = sentry_sdk.start_transaction(op="shepherd.run", name=data.get("routine_id") or "run")
            t.set_tag("run_id", run_id)
            t.set_tag("mode", str(data.get("mode")))
            t.set_data("total_steps", data.get("total_steps"))
            _txns[run_id] = t
        elif event_type == "step.start":
            t = _txns.get(run_id)
            if t:
                _close_span(run_id)
                desc = data.get("description") or data.get("action") or f"step {data.get('index')}"
                sp = t.start_child(op="milestone", description=str(desc))
                sp.set_tag("step_index", str(data.get("index")))
                _spans[run_id] = sp
        elif event_type == "step.complete":
            sp = _spans.get(run_id)
            if sp and data.get("status") == "failed":
                sp.set_status("internal_error")
        elif event_type == "execution.complete":
            _close_span(run_id)
            _finish_txn(run_id, "ok")
            _clear_replay(run_id)
        elif event_type == "execution.halted":
            _close_span(run_id)
            _finish_txn(run_id, "aborted")
            capture_message(
                f"Run halted: {data.get('reason') or 'oversight halt'}",
                level="warning",
                tags={"shepherd.event": "halt", "reason": data.get("reason"),
                      "run_id": run_id, "step_index": data.get("step_index")},
                run_id=run_id,
            )
            _clear_replay(run_id)
    except Exception as e:
        print(f"[sentry] event non-fatal: {e}")


def _clear_replay(run_id: str) -> None:
    """Free this run's replay buffer once the run has ended (and any capture that
    needed it has already fired inline at the failing step)."""
    try:
        from telemetry import session_replay
        session_replay.clear(run_id)
    except Exception:
        pass


def _close_span(run_id: str) -> None:
    sp = _spans.pop(run_id, None)
    if sp:
        try:
            sp.finish()
        except Exception:
            pass


def _finish_txn(run_id: str, status: str) -> None:
    t = _txns.pop(run_id, None)
    if t:
        try:
            t.set_status(status)
            t.finish()
        except Exception:
            pass


def capture_intervention(
    *,
    decision: str,
    reason: str,
    run_id: str,
    step_index: Optional[int] = None,
    trigger: Optional[str] = None,
    verdict: Optional[str] = None,
    milestone: Optional[str] = None,
    screenshot_png: Optional[bytes] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Record an oversight intervention (halt / flag / approve) as a structured,
    searchable Sentry issue: tags for the dashboard (decision, trigger, verdict),
    the screenshot attached, the policy/verifier verdicts in context, and a
    clickable Phoenix trace link. The reliability story made literal. No-op when
    Sentry is off."""
    if not FEATURES["sentry"]:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            scope.set_level("warning")
            tags = {
                "shepherd.event": "intervention", "decision": decision,
                "trigger": trigger, "verdict": verdict,
                "run_id": run_id, "step_index": step_index,
            }
            for k, v in tags.items():
                if v is not None:
                    scope.set_tag(k, str(v))
            scope.set_context("intervention", {
                "decision": decision, "reason": reason, "verdict": verdict,
                "trigger": trigger, "milestone": milestone, "step_index": step_index,
            })
            if screenshot_png:
                try:
                    scope.add_attachment(bytes=screenshot_png, filename="halt.png", content_type="image/png")
                except Exception:
                    pass
            _attach_replay(scope, run_id)
            _link_phoenix(scope, trace_id)
            sentry_sdk.capture_message(f"Oversight {decision}: {reason}", level="warning")
    except Exception as e:
        print(f"[sentry] capture_intervention failed (non-fatal): {e}")


def capture(
    exc: BaseException,
    *,
    tags: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """
    Capture an exception with optional structured context. No-op when Sentry is
    disabled. Centralizes the feature check + SDK import so call sites stay clean.
    The active Phoenix trace id is linked automatically (or pass `trace_id`), and
    the agent's session-replay filmstrip (screenshots leading up to the failure)
    is attached when a run id is available.
    """
    if not FEATURES["sentry"]:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            for k, v in (tags or {}).items():
                if v is not None:
                    scope.set_tag(k, str(v))
            if context:
                scope.set_context("execution", context)
            _attach_replay(scope, run_id or (context or {}).get("run_id"))
            _link_phoenix(scope, trace_id)
            sentry_sdk.capture_exception(exc)
    except Exception as e:
        print(f"[sentry] capture failed (non-fatal): {e}")


def capture_message(
    message: str,
    *,
    level: str = "error",
    tags: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> None:
    """
    Report a non-exception failure (e.g. a run that ended status='failed' without
    raising) as a Sentry message with structured context. No-op when disabled.
    The active Phoenix trace id is linked automatically (or pass `trace_id`), and
    the agent's session-replay filmstrip is attached when a run id is available.
    """
    if not FEATURES["sentry"]:
        return
    try:
        import sentry_sdk
        with sentry_sdk.push_scope() as scope:
            for k, v in (tags or {}).items():
                if v is not None:
                    scope.set_tag(k, str(v))
            if context:
                scope.set_context("execution", context)
            _attach_replay(scope, run_id or (context or {}).get("run_id"))
            _link_phoenix(scope, trace_id)
            sentry_sdk.capture_message(message, level=level)
    except Exception as e:
        print(f"[sentry] capture_message failed (non-fatal): {e}")
