"""
Band multi-agent messaging — BOUNDARY ONLY.
Router agent publishes start → engine executes ENTIRE click sequence locally →
engine publishes completion. Zero Band calls inside the click sequence.
VERIFY: Band API/SDK at event — no confirmed public Python SDK.
"""
import json
from config import FEATURES, BAND_API_KEY, BAND_ROOM_KEY
from shepherd_types import ResolvedRoutine, ExecutionResult


def publish_routine_start(resolved: ResolvedRoutine) -> None:
    if not FEATURES["band"]:
        return
    try:
        _publish("routine.start", {
            "routine_id": resolved.routine_id,
            "variables":  resolved.variables,
            "confidence": resolved.confidence,
        })
    except Exception as e:
        print(f"[band] publish_start non-fatal: {e}")


def publish_routine_complete(result: ExecutionResult) -> None:
    if not FEATURES["band"]:
        return
    try:
        _publish("routine.complete", {
            "routine_id":  result.routine_id,
            "run_id":      result.run_id,
            "status":      result.status,
            "duration_ms": result.duration_ms,
        })
    except Exception as e:
        print(f"[band] publish_complete non-fatal: {e}")


def _publish(event_type: str, payload: dict) -> None:
    """
    VERIFY: replace with real Band SDK calls once API is confirmed.
    Placeholder uses httpx until SDK surface is confirmed at event.
    """
    import httpx
    # VERIFY: endpoint, auth scheme, message body format
    httpx.post(
        f"https://api.band.us/v1/rooms/{BAND_ROOM_KEY}/messages",  # VERIFY
        headers={"Authorization": f"Bearer {BAND_API_KEY}"},
        json={"type": event_type, "data": json.dumps(payload)},
        timeout=5.0,
    ).raise_for_status()
