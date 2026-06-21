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
        sentry_sdk.init(
            dsn=SENTRY_DSN,
            traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.0")),
            send_default_pii=False,
            environment=os.getenv("SENTRY_ENVIRONMENT", "development"),
            release=_release(),
        )
        print("[sentry] Initialized.")
    except Exception as e:
        print(f"[sentry] Init failed (non-fatal): {e}")


def capture(
    exc: BaseException,
    *,
    tags: Optional[dict[str, Any]] = None,
    context: Optional[dict[str, Any]] = None,
    trace_id: Optional[str] = None,
) -> None:
    """
    Capture an exception with optional structured context. No-op when Sentry is
    disabled. Centralizes the feature check + SDK import so call sites stay clean.
    The active Phoenix trace id is linked automatically (or pass `trace_id`).
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
) -> None:
    """
    Report a non-exception failure (e.g. a run that ended status='failed' without
    raising) as a Sentry message with structured context. No-op when disabled.
    The active Phoenix trace id is linked automatically (or pass `trace_id`).
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
            _link_phoenix(scope, trace_id)
            sentry_sdk.capture_message(message, level=level)
    except Exception as e:
        print(f"[sentry] capture_message failed (non-fatal): {e}")
