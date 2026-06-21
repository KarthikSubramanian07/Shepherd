#!/usr/bin/env python3
"""
The Shepherd — main entry loop.
Voice/typed intent → router → engine → telemetry + memory + dashboard.

Usage:
  python main.py
  python main.py --mode LOCKED   # force deterministic fallback
"""
import os
import sys
import time
import threading

from config import FEATURES, EXECUTION_MODE, DASHBOARD_PORT
from shepherd_types import Intent, ResolvedRoutine
from router.router import ShepherdIntentRouter
from engine.engine import ShepherdExecutionEngine
from engine.coords import load_coords
from engine.routines import load_routines
from telemetry.telemetry import ShepherdTelemetry
from telemetry.sentry_init import init_sentry
from telemetry.memory import ExecutionMemory
from telemetry.evolution import RoutineEvolution
from dashboard.events import event_bus


def _get_intent_text(engine: ShepherdExecutionEngine) -> str:
    """
    Returns the raw intent text from Deepgram (voice) or typed input.
    Arms the stop-command listener before recording so 'stop' halts during execution.
    """
    if FEATURES["deepgram"]:
        try:
            from services.deepgram_input import listen_and_transcribe, listen_for_stop_command
            listen_for_stop_command(halt_callback=engine.request_halt)
            transcript = listen_and_transcribe()
            if transcript:
                return transcript
            print("[deepgram] Empty transcript — falling back to typed input.")
        except Exception as e:
            print(f"[deepgram] {e} — falling back to typed input.")
    return input("Intent → ").strip()


def _record_mode(routine_id: str) -> None:
    """
    Record a human demonstration for a routine and save it to data/routines.json.
    Usage: python main.py --record ROUTINE_FORM_FILL
    Controls: Cmd+Shift+M = mark step boundary  |  Cmd+Shift+Q = stop
    """
    import json
    from engine.recorder import DemonstrationRecorder

    print(f"\n[record] Demonstration mode — routine: {routine_id}")
    print("[record] Cmd+Shift+M = mark step  |  Cmd+Shift+Q = stop\n")

    narration_fn = None
    if FEATURES["deepgram"]:
        try:
            from services.deepgram_input import listen_and_transcribe
            narration_fn = lambda: listen_and_transcribe(4.0)
            print("[record] Deepgram active — speak step instructions after each Cmd+Shift+M\n")
        except Exception as e:
            print(f"[record] Deepgram unavailable ({e}) — no narration this session\n")

    recorder = DemonstrationRecorder(get_narration_fn=narration_fn)
    recorder.start()

    try:
        while recorder._running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    steps = recorder.stop()
    if not steps:
        print("[record] No steps recorded — exiting.")
        return

    print(f"\n[record] {len(steps)} steps captured. Saving to routines.json…")

    steps_data = [
        {
            "index":           s.index,
            "action":          s.action,
            "target":          s.target,
            "text":            s.text,
            "timestamp":       s.timestamp,
            "instruction":     s.instruction,
            "screenshot_path": s.screenshot_path,
        }
        for s in steps
    ]

    routines_path = os.path.join(os.path.dirname(__file__), "data", "routines.json")
    with open(routines_path) as f:
        routines = json.load(f)

    matched = False
    for r in routines:
        if r["routine_id"] == routine_id:
            r["demonstration"] = steps_data
            matched = True
            break

    if not matched:
        print(f"[record] Routine '{routine_id}' not found. Available: "
              f"{[r['routine_id'] for r in routines]}")
        return

    with open(routines_path, "w") as f:
        json.dump(routines, f, indent=2)

    print(f"[record] ✓ Saved {len(steps)} steps → {routine_id}.demonstration")
    print(f"[record]   Screenshots: data/screenshots/step_NNN.png")
    print(f"[record]   Run 'python main.py' to execute with Agent S against this recording.\n")


def main() -> None:
    # ── Parse args ────────────────────────────────────────────────────────────
    if "--record" in sys.argv:
        idx = sys.argv.index("--record")
        rid = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else "ROUTINE_FORM_FILL"
        _record_mode(rid)
        sys.exit(0)

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
    evolution = RoutineEvolution()
    engine    = ShepherdExecutionEngine(coords=coords, telemetry=telemetry, mode=mode, evolution=evolution)

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
            from services.overshoot_vision import start_vision_stream
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

            plan = router.resolve_plan(intent)
            event_bus.emit("plan.resolved", {
                "kind": plan.kind, "target": plan.target,
                "confidence": plan.confidence, "source": plan.source,
                "matched": plan.matched, "params": plan.params,
            })

            # ── Dispatch a saved WORKFLOW (preferred) — traverse the graph ─────
            if plan.kind == "WORKFLOW":
                workflow = router._workflows.get(plan.target)
                if workflow is not None:
                    print(f"[router] → WORKFLOW {plan.target}  confidence={plan.confidence} ({plan.source})")
                    result = engine.execute_workflow(
                        workflow, goal=intent.raw_text, params=plan.params
                    )
                    telemetry.record(result, engine.last_step_records)
                    print(f"[shepherd] {result.status.upper()} — {result.steps_completed} milestones in {result.duration_ms}ms\n")
                    continue

            if plan.kind != "ROUTINE":
                print("[router] No routine matched. Try: 'fill form', 'open browser', or 'demo'\n")
                event_bus.emit("intent.unmatched", {"raw_text": intent.raw_text})
                continue

            resolved = ResolvedRoutine(
                routine_id=plan.target, variables=plan.params,
                confidence=plan.confidence, matched_keywords=plan.matched,
            )
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
            if FEATURES["deepgram"]:
                try:
                    from services.deepgram_input import stop_listener
                    stop_listener()
                except Exception:
                    pass

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
        from services.band_boundary import publish_routine_start
        publish_routine_start(resolved)
    except Exception as e:
        print(f"[band] start non-fatal: {e}")


def _band_complete(result) -> None:
    try:
        from services.band_boundary import publish_routine_complete
        publish_routine_complete(result)
    except Exception as e:
        print(f"[band] complete non-fatal: {e}")


if __name__ == "__main__":
    main()
