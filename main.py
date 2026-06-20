#!/usr/bin/env python3
"""
The Shepherd — main entry loop.
Voice/typed intent → router → engine → telemetry + memory + dashboard.

Usage:
  python main.py
  python main.py --mode LOCKED   # force deterministic fallback
"""
import sys
import time
import threading

from config import FEATURES, EXECUTION_MODE, DASHBOARD_PORT
from shepherd_types import Intent
from router.router import ShepherdIntentRouter
from engine.engine import ShepherdExecutionEngine
from engine.coords import load_coords
from engine.routines import load_routines
from telemetry.telemetry import ShepherdTelemetry
from telemetry.sentry_init import init_sentry
from telemetry.memory import ExecutionMemory
from dashboard.events import event_bus


def _get_intent_text(engine: ShepherdExecutionEngine) -> str:
    """
    Returns the raw intent text from Deepgram (voice) or typed input.
    Arms the stop-command listener before recording so 'stop' halts during execution.
    """
    if FEATURES["deepgram"]:
        try:
            from integrations.deepgram_input import listen_and_transcribe, listen_for_stop_command
            listen_for_stop_command(halt_callback=engine.request_halt)
            transcript = listen_and_transcribe()
            if transcript:
                return transcript
            print("[deepgram] Empty transcript — falling back to typed input.")
        except Exception as e:
            print(f"[deepgram] {e} — falling back to typed input.")
    return input("Intent → ").strip()


def main() -> None:
    # ── Parse args ────────────────────────────────────────────────────────────
    mode = EXECUTION_MODE
    if "--mode" in sys.argv:
        idx = sys.argv.index("--mode")
        if idx + 1 < len(sys.argv):
            mode = sys.argv[idx + 1].upper()

    print(f"\n=== THE SHEPHERD ===")
    print(f"Mode: {mode}  |  Active features: {[k for k, v in FEATURES.items() if v]}\n")

    # ── Init ──────────────────────────────────────────────────────────────────
    init_sentry()
    telemetry = ShepherdTelemetry()
    memory    = ExecutionMemory()
    load_routines()          # pre-warm cache
    coords    = load_coords()
    router    = ShepherdIntentRouter()
    engine    = ShepherdExecutionEngine(coords=coords, telemetry=telemetry, mode=mode)

    # ── Start dashboard ───────────────────────────────────────────────────────
    try:
        from dashboard.server import start_dashboard
        threading.Thread(target=start_dashboard, daemon=True).start()
        print(f"[dashboard] Control Hub → http://localhost:{DASHBOARD_PORT}\n")
    except Exception as e:
        print(f"[dashboard] Could not start: {e}")

    # ── Start Overshoot vision stream (parallel, never blocks engine) ─────────
    if FEATURES["overshoot"]:
        try:
            from integrations.overshoot_vision import start_vision_stream
            threading.Thread(target=start_vision_stream, daemon=True).start()
        except Exception as e:
            event_bus.emit("vision.offline", {"reason": str(e)})

    # ── Main loop ─────────────────────────────────────────────────────────────
    print("Speak an intent or type it. Ctrl-C to quit.")
    print("Routines: 'fill form', 'open browser', 'demo'\n")

    while True:
        try:
            raw = _get_intent_text(engine)
            if not raw:
                continue

            intent = Intent(raw_text=raw, timestamp=time.time())
            event_bus.emit("intent.received", {"raw_text": intent.raw_text, "source": intent.source})

            resolved = router.resolve(intent)
            if resolved is None:
                print("[router] No routine matched. Try: 'fill form', 'open browser', or 'demo'\n")
                event_bus.emit("intent.unmatched", {"raw_text": intent.raw_text})
                continue

            print(f"[router] → {resolved.routine_id}  confidence={resolved.confidence}")
            event_bus.emit("routine.resolved", {
                "routine_id":      resolved.routine_id,
                "confidence":      resolved.confidence,
                "matched_keywords": resolved.matched_keywords,
                "variables":       resolved.variables,
            })

            # Band: publish start at boundary (fire-and-forget, never blocks engine)
            if FEATURES["band"]:
                threading.Thread(
                    target=_band_start, args=(resolved,), daemon=True
                ).start()

            # ── Execute (synchronous, blocking) ───────────────────────────────
            result = engine.execute(resolved)

            # ── Post-execution (all outside the click path) ───────────────────
            if FEATURES["band"]:
                threading.Thread(
                    target=_band_complete, args=(result,), daemon=True
                ).start()

            telemetry.record(result, engine.last_step_records)
            memory.store(result, engine.last_step_records, confidence=resolved.confidence)

            print(f"[shepherd] {result.status.upper()} — {result.steps_completed} steps in {result.duration_ms}ms\n")

        except KeyboardInterrupt:
            print("\n[shepherd] Bye.")
            break
        except Exception as e:
            print(f"[shepherd] Unhandled error: {e}")
            if FEATURES["sentry"]:
                import sentry_sdk
                sentry_sdk.capture_exception(e)


def _band_start(resolved) -> None:
    try:
        from integrations.band_boundary import publish_routine_start
        publish_routine_start(resolved)
    except Exception as e:
        print(f"[band] start non-fatal: {e}")


def _band_complete(result) -> None:
    try:
        from integrations.band_boundary import publish_routine_complete
        publish_routine_complete(result)
    except Exception as e:
        print(f"[band] complete non-fatal: {e}")


if __name__ == "__main__":
    main()
