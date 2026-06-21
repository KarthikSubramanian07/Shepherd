#!/usr/bin/env python3
"""
The Shepherd — main entry loop.
Voice/typed intent → router → engine → telemetry + memory + dashboard.

Usage:
  python main.py
  python main.py --mode LOCKED      # force deterministic fallback
  python main.py --mode AUTONOMOUS  # free-form Agent S goals (no routine required)
"""
import os
import queue
import sys
import time
import threading

from config import (
    FEATURES, EXECUTION_MODE, DASHBOARD_PORT,
    AUTONOMOUS_ON_UNMATCHED, EXIT_WHEN_DONE,
)
from shepherd_types import Intent
from router.router import ShepherdIntentRouter
from engine.engine import ShepherdExecutionEngine
from engine.coords import load_coords
from engine.routines import load_routines
from telemetry.telemetry import ShepherdTelemetry
from telemetry.sentry_init import init_sentry
from telemetry.memory import ExecutionMemory
from telemetry.evolution import RoutineEvolution
from dashboard.events import event_bus


def _get_intent_text(
    engine: ShepherdExecutionEngine,
    remote_intents: "queue.Queue[str]",
) -> str:
    """
    Returns the raw intent text from a remote Command Center, Deepgram (voice),
    or typed input. Arms the stop-command listener before recording so 'stop'
    halts during execution.
    """
    # A command-center intent already waiting takes priority.
    try:
        return remote_intents.get_nowait()
    except queue.Empty:
        pass

    # Remote-driven mode: the operated machine may be headless, so block on the
    # relay's intent queue instead of local stdin. The voice 'stop' listener is
    # still armed so a spoken halt works during execution.
    if FEATURES["remote"]:
        if FEATURES["deepgram"]:
            try:
                from services.deepgram_input import listen_for_stop_command
                listen_for_stop_command(halt_callback=engine.request_halt)
            except Exception:
                pass
        while True:
            try:
                return remote_intents.get(timeout=0.5)
            except queue.Empty:
                continue

    if FEATURES["deepgram"]:
        try:
            from services.deepgram_input import listen_and_transcribe
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
    print("[record]   Screenshots: data/screenshots/step_NNN.png")
    print("[record]   Run 'python main.py' to execute with Agent S against this recording.\n")


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

    print("\n=== THE SHEPHERD ===")
    print(f"Mode: {mode}  |  Active features: {[k for k, v in FEATURES.items() if v]}\n")

    # ── Init ──────────────────────────────────────────────────────────────────
    init_sentry()
    # Warn loudly if Screen Recording isn't granted — otherwise Agent S is blind
    # (screenshots show only desktop + menu bar) and silently spins on every task.
    from engine.permissions import preflight as _perm_preflight
    _perm_preflight()
    telemetry = ShepherdTelemetry()
    memory    = ExecutionMemory()
    load_routines()          # pre-warm cache
    coords    = load_coords()
    router    = ShepherdIntentRouter()
    evolution = RoutineEvolution()
    engine    = ShepherdExecutionEngine(coords=coords, telemetry=telemetry, mode=mode, evolution=evolution)
    remote_intents: "queue.Queue[str]" = queue.Queue()

    # ── Start dashboard ───────────────────────────────────────────────────────
    try:
        from dashboard.server import start_dashboard
        threading.Thread(target=start_dashboard, daemon=True).start()
        print(f"[dashboard] Control Hub → http://localhost:{DASHBOARD_PORT}\n")
    except Exception as e:
        print(f"[dashboard] Could not start: {e}")

    # ── Start the coordinator relay (outbound; never blocks engine) ───────────
    if FEATURES["remote"]:
        try:
            from services.relay_client import start_relay_client
            from config import COORDINATOR_URL, AGENT_ID, AGENT_PAIRING_CODE
            start_relay_client(engine, remote_intents)
            print(f"[relay] Dialing coordinator {COORDINATOR_URL} as '{AGENT_ID}'")
            print("[relay] ┌──────────────────────────────────────────────┐")
            print(f"[relay] │  Command Center session code:  {AGENT_PAIRING_CODE:<13} │")
            print("[relay] └──────────────────────────────────────────────┘\n")
        except Exception as e:
            print(f"[relay] Could not start: {e}")

    # ── Start Overshoot vision stream (parallel, never blocks engine) ─────────
    if FEATURES["overshoot"]:
        try:
            from services.overshoot_vision import start_vision_stream
            threading.Thread(target=start_vision_stream, daemon=True).start()
        except Exception as e:
            event_bus.emit("vision.offline", {"reason": str(e)})

    # ── Main loop ─────────────────────────────────────────────────────────────
    print("Speak an intent or type it. Ctrl-C to quit.")
    if mode == "AUTONOMOUS":
        print("Mode AUTONOMOUS — any intent runs as a free-form Agent S goal.\n")
    else:
        print("Routines: 'fill form', 'open browser', 'demo'")
        if AUTONOMOUS_ON_UNMATCHED:
            print("Unmatched intents fall back to autonomous Agent S.\n")
        else:
            print("Set AUTONOMOUS_ON_UNMATCHED=true or --mode AUTONOMOUS for free-form goals.\n")

    while True:
        try:
            raw = _get_intent_text(engine, remote_intents)
            if not raw:
                continue

            intent = Intent(raw_text=raw, timestamp=time.time())
            event_bus.emit("intent.received", {"raw_text": intent.raw_text, "source": intent.source})

            effective_mode = engine.effective_mode()

            if effective_mode == "AUTONOMOUS":
                if not engine._agent_s.available:
                    print("[autonomous] Agent S unavailable — set AGENT_S_* keys in .env\n")
                    event_bus.emit("intent.unmatched", {"raw_text": intent.raw_text, "reason": "agent_s_unavailable"})
                    continue
                print(f"[autonomous] goal: {raw}")
                event_bus.emit("routine.resolved", {
                    "routine_id":      "AUTONOMOUS",
                    "confidence":      1.0,
                    "matched_keywords": [],
                    "variables":       {"GOAL": raw},
                })
                # Arm the spoken-stop listener now — mic is free since intent was already captured
                if FEATURES["deepgram"]:
                    try:
                        from services.deepgram_input import listen_for_stop_command
                        listen_for_stop_command(halt_callback=engine.request_halt)
                    except Exception:
                        pass
                result = engine.execute_autonomous(raw)
                _after_run(engine, telemetry, memory, result, confidence=1.0)
                if _should_end_session():
                    print("[shepherd] Task complete — ending session.\n")
                    break
                continue

            resolved = router.resolve(intent)
            if resolved is None:
                if AUTONOMOUS_ON_UNMATCHED and engine._agent_s.available:
                    print(f"[router] No routine matched — autonomous fallback for: {raw}")
                    event_bus.emit("intent.autonomous_fallback", {"raw_text": intent.raw_text})
                    event_bus.emit("routine.resolved", {
                        "routine_id":      "AUTONOMOUS",
                        "confidence":      0.0,
                        "matched_keywords": [],
                        "variables":       {"GOAL": raw},
                    })
                    result = engine.execute_autonomous(raw)
                    _after_run(engine, telemetry, memory, result, confidence=0.0)
                    if _should_end_session():
                        print("[shepherd] Task complete — ending session.\n")
                        break
                    continue
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

            # Arm the spoken-stop listener now — mic is free since intent was captured
            if FEATURES["deepgram"]:
                try:
                    from services.deepgram_input import listen_for_stop_command
                    listen_for_stop_command(halt_callback=engine.request_halt)
                except Exception:
                    pass

            # ── Execute (synchronous, blocking) ───────────────────────────────
            result = engine.execute(resolved)
            _after_run(engine, telemetry, memory, result, confidence=resolved.confidence)
            if _should_end_session():
                print("[shepherd] Task complete — ending session.\n")
                break

        except KeyboardInterrupt:
            print("\n[shepherd] Bye.")
            break
        except Exception as e:
            print(f"[shepherd] Unhandled error: {e}")
            if FEATURES["sentry"]:
                import sentry_sdk
                sentry_sdk.capture_exception(e)


def _should_end_session() -> bool:
    """
    End the program once a task finishes (EXIT_WHEN_DONE), unless we're a remote
    agent — the command center keeps the session alive to serve more goals.
    """
    return EXIT_WHEN_DONE and not FEATURES["remote"]


def _after_run(engine, telemetry, memory, result, confidence: float) -> None:
    """Post-execution bookkeeping — always outside the click path."""
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
    memory.store(result, engine.last_step_records, confidence=confidence)
    print(f"[shepherd] {result.status.upper()} — {result.steps_completed} steps in {result.duration_ms}ms\n")


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
