#!/usr/bin/env python3
"""
The Shepherd — main entry loop.
Voice/typed intent → router → engine → telemetry + memory + dashboard.

Front door is controlled by USE_ROUTER (config / .env):
  USE_ROUTER=false (default)  free-form autonomous Agent S goals (no routing)
  USE_ROUTER=true             match a saved workflow/routine first, autonomous on no match
  ROUTINE_REPLAY=vision|deterministic  how a matched routine is driven (LIVE|LOCKED)
  MATCH_WORKFLOWS / MATCH_ROUTINES  enable each routing source separately (both on by default)

Usage:
  python main.py
  python main.py --mode LOCKED      # one-off override: force deterministic replay
  python main.py --mode AUTONOMOUS  # one-off override: free-form Agent S goals
"""
import os
import queue
import sys
import time
import threading

from config import (
    FEATURES, EXECUTION_MODE, DASHBOARD_PORT, USE_ROUTER, ROUTINE_REPLAY,
    AUTONOMOUS_ON_UNMATCHED, EXIT_WHEN_DONE, BACKEND_URL, CONSOLE_LOG,
    MATCH_WORKFLOWS, MATCH_ROUTINES,
)
from shepherd_types import Intent, ResolvedRoutine
from router.router import ShepherdIntentRouter
from engine.engine import ShepherdExecutionEngine
from engine.coords import load_coords
from engine.routines import load_routines
from telemetry.telemetry import ShepherdTelemetry
from telemetry.sentry_init import (
    init_sentry,
    capture as sentry_capture,
    capture_message as sentry_capture_message,
)
from telemetry.memory import ExecutionMemory
from telemetry.evolution import RoutineEvolution
from dashboard.events import event_bus


def _stdin_producer(
    engine: ShepherdExecutionEngine,
    remote_intents: "queue.Queue[str]",
    idle: "threading.Event",
) -> None:
    """
    Read typed (or spoken) goals from the command line and feed them into the
    SHARED intent queue — the same queue the frontend / coordinator / poller feed.
    Runs in a background thread so the CLI and the frontend can both drive the agent
    at the same time. Exits quietly if there's no interactive stdin (headless).

    Only prompts while the agent is idle (`idle` is set), so the "Intent ->" prompt
    never interleaves with a run's log output.
    """
    while True:
        idle.wait()   # hold the prompt until the current run finishes
        if FEATURES["deepgram"]:
            try:
                from services.deepgram_input import listen_and_transcribe, listen_for_stop_command
                listen_for_stop_command(halt_callback=engine.request_halt)
                transcript = listen_and_transcribe()
                if transcript:
                    idle.clear()
                    remote_intents.put(transcript)
                    continue
            except Exception as e:
                print(f"[deepgram] {e} — using typed input.")
        try:
            line = input("Intent → ").strip()
        except EOFError:
            print("[shepherd] No interactive stdin — taking goals from the frontend only.")
            return
        if line:
            idle.clear()   # a run is about to start; don't reprompt until it's done
            remote_intents.put(line)


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

    # --listen: don't prompt on stdin; take goals only from the dashboard /api/intent
    # (or coordinator), and keep serving across goals. Drive entirely from the frontend.
    global _listen_mode
    listen = "--listen" in sys.argv
    _listen_mode = listen

    # Describe the front door in the un-bundled terms (router on/off + replay style),
    # falling back to the legacy mode label for a one-off --mode override.
    if "--mode" in sys.argv:
        front_door = f"--mode override: {mode}"
    elif USE_ROUTER:
        sources = "+".join(
            s for s, on in (("workflows", MATCH_WORKFLOWS), ("routines", MATCH_ROUTINES)) if on
        ) or "none"
        front_door = f"router ON (match={sources}, replay={ROUTINE_REPLAY}, autonomous fallback) -> {mode}"
    else:
        front_door = f"router OFF (free-form autonomous) -> {mode}"
    print("\n=== THE SHEPHERD ===")
    print(f"Front door: {front_door}  |  Active features: {[k for k, v in FEATURES.items() if v]}\n")

    # ── Init ──────────────────────────────────────────────────────────────────
    init_sentry()
    # Comprehensive stdout logging — one subscriber prints every workflow event.
    if CONSOLE_LOG:
        from telemetry.console_log import start_console_logging
        start_console_logging()
    # Warn loudly if Screen Recording isn't granted — otherwise Agent S is blind
    # (screenshots show only desktop + menu bar) and silently spins on every task.
    from engine.permissions import preflight as _perm_preflight
    _perm_preflight()
    telemetry = ShepherdTelemetry()
    memory    = ExecutionMemory()
    load_routines()          # pre-warm cache
    coords    = load_coords()
    # Backfill: promote previously-crystallized task graphs into dispatchable
    # workflows BEFORE the router indexes them, so they appear in the Workflows
    # page and are matchable immediately (no manual bake-out). Idempotent.
    from config import AUTO_PROMOTE_WORKFLOWS, AUTO_PROMOTE_MIN_NODES
    if AUTO_PROMOTE_WORKFLOWS:
        try:
            from engine.workflow_promote import backfill_workflows
            backfill_workflows(AUTO_PROMOTE_MIN_NODES)
        except Exception as e:
            print(f"[promote] backfill skipped (non-fatal): {e}")
    router    = ShepherdIntentRouter()
    evolution = RoutineEvolution()
    engine    = ShepherdExecutionEngine(coords=coords, telemetry=telemetry, mode=mode, evolution=evolution)
    remote_intents: "queue.Queue[str]" = queue.Queue()

    # ── Dashboard backend ───────────────────────────────────────────────────────
    # If BACKEND_URL is set, a separate persistent backend owns the dashboard/API;
    # stream events to it (and don't bind the port here). Otherwise run the
    # all-in-one in-process dashboard as before.
    # Resolve where this agent's dashboard/API lives. Explicit BACKEND_URL wins;
    # otherwise, if the port is already serving (e.g. ./scripts/serve.sh is up),
    # attach to that backend instead of crashing on a bind conflict.
    attach_url = BACKEND_URL
    if not attach_url and _port_in_use(DASHBOARD_PORT):
        attach_url = f"http://localhost:{DASHBOARD_PORT}"
        print(f"[backend] :{DASHBOARD_PORT} already serving — attaching to it "
              f"(not starting another dashboard).")

    if attach_url:
        try:
            from dashboard.forwarder import start_forwarding, start_intent_polling
            start_forwarding(attach_url)
            # Pull goals submitted from the frontend (POST /api/intent) into this
            # agent's queue — the reverse channel for separately-running agents.
            start_intent_polling(attach_url, remote_intents)
            print(f"[backend] streaming to backend at {attach_url}\n")
        except Exception as e:
            print(f"[backend] Could not attach to backend: {e}")
    else:
        try:
            from dashboard.server import start_dashboard, register_intent_queue, register_engine
            # Let the dashboard's POST /api/intent enqueue goals for this agent —
            # this is what "run an agent from the frontend" rides on locally.
            register_intent_queue(remote_intents)
            register_engine(engine)
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

    # ── Durable run ledger: detect + resume any run orphaned by a crash ───────
    # Each run is durably checkpointed milestone by milestone (off the click path).
    # If the process died mid-run, the ledger is left "running"; on this boot we
    # detect that and re-dispatch the task so a crash never silently abandons work.
    try:
        from services import run_memory
        run_memory.install()   # cross-run semantic recall: index every completed run
    except Exception as e:
        print(f"[run_memory] install skipped (non-fatal): {e}")

    try:
        from services import agentspan_durable
        agentspan_durable.install()
        for led in agentspan_durable.resume_incomplete():
            goal = led.get("goal") or ""
            print(f"[durable] Interrupted run {led.get('run_id')} detected at "
                  f"milestone {led.get('done', 0)}/{led.get('total', 0)} — "
                  f"re-dispatching: {goal}")
            if goal and not listen:
                remote_intents.put(goal)
    except Exception as e:
        print(f"[durable] resume skipped (non-fatal): {e}")

    # ── Orchestrated mode: many agents at once (ENABLE_ORCHESTRATOR) ──────────
    # Instead of the serial single-agent loop below, hand every goal to the
    # Orchestrator, which spawns an agent worker per task and serializes their
    # actions through the ActionArbiter (the action queue). Local Agent S agents
    # share the one desktop; Browserbase agents run in parallel cloud windows.
    from orchestrator.config import ENABLE_ORCHESTRATOR, DEFAULT_SURFACE_KIND
    if ENABLE_ORCHESTRATOR:
        _run_orchestrated(coords, telemetry, remote_intents, listen, DEFAULT_SURFACE_KIND)
        return

    # ── Main loop ─────────────────────────────────────────────────────────────
    # Goals flow into ONE queue from any producer: the command line (stdin thread
    # below, unless --listen), the frontend (POST /api/intent), the coordinator,
    # and the backend poller. The loop just consumes the queue — so CLI and
    # frontend both drive the agent at the same time.
    if mode == "AUTONOMOUS":
        print("Mode AUTONOMOUS — planner drafts steps, then Agent S executes each one.")
    elif AUTONOMOUS_ON_UNMATCHED:
        print("Routines: 'fill form', 'open browser', 'demo' — unmatched intents go autonomous.")
    else:
        print("Routines: 'fill form', 'open browser', 'demo'.")

    if listen:
        print("[shepherd] --listen: goals come from the frontend/coordinator only "
              "(no command-line prompt). Ctrl-C to quit.\n")
    # Set while the agent is idle; cleared during a run so the CLI prompt holds.
    idle = threading.Event()
    idle.set()

    if not listen:
        print("Type a goal at the prompt, or send one from the frontend. Ctrl-C to quit.")
        threading.Thread(
            target=_stdin_producer, args=(engine, remote_intents, idle), daemon=True
        ).start()

    while True:
        try:
            raw = remote_intents.get()   # fed by CLI + frontend + coordinator + poller
            if not raw:
                continue

            # ── Resume from suspended task ────────────────────────────────
            if raw == "__RESUME__":
                ctx = engine._suspended_task
                if ctx is None:
                    continue  # stale resume signal, ignore
                engine._suspended_task = None
                idle.clear()
                print(f"[autonomous] resuming suspended task: {ctx.goal[:60]}...")
                result = engine._execute_autonomous_reactive(
                    ctx.goal, plan_hint=ctx.plan_hint, resume_ctx=ctx)
                _after_run(engine, telemetry, memory, result, confidence=1.0)
                if _should_end_session():
                    print("[shepherd] Task complete — ending session.\n")
                    break
                continue

            # ── New task — discard any suspended state ────────────────────
            engine._suspended_task = None
            idle.clear()   # a run is starting → pause the CLI prompt

            intent = Intent(raw_text=raw, timestamp=time.time())
            event_bus.emit("intent.received", {"raw_text": intent.raw_text, "source": intent.source})

            # Cross-run memory: recall the most similar successful prior run (by
            # MEANING, across differently-worded goals) so the operator sees the
            # agent reusing a proven path. Off the click path; best-effort.
            try:
                from services import run_memory
                recalled = run_memory.recall(intent.raw_text)
                if recalled:
                    print(f"[memory] Recalled a similar run (sim {recalled['similarity']:.2f}): "
                          f"{len(recalled['milestones'])} proven milestones from "
                          f"'{recalled['goal']}'")
                    event_bus.emit("memory.recall", {
                        "goal": recalled["goal"],
                        "similarity": recalled["similarity"],
                        "milestones": recalled["milestones"],
                    })
            except Exception as e:
                print(f"[memory] recall skipped (non-fatal): {e}")

            effective_mode = engine.effective_mode()

            # ── AUTONOMOUS mode — prefer a saved WORKFLOW, else free-form Agent S goal
            if effective_mode == "AUTONOMOUS":
                plan = router.resolve_plan(intent, mode="AUTONOMOUS")
                event_bus.emit("plan.resolved", {
                    "kind": plan.kind, "target": plan.target,
                    "confidence": plan.confidence, "source": plan.source,
                    "matched": plan.matched, "params": plan.params,
                })

                if plan.kind == "WORKFLOW":
                    workflow = router._workflows.get(plan.target)
                    if workflow is not None:
                        print(f"[router] → WORKFLOW {plan.target}  confidence={plan.confidence} ({plan.source})")
                        result = engine.execute_workflow(
                            workflow, goal=intent.raw_text, params=plan.params
                        )
                        telemetry.record(result, engine.last_step_records)
                        print(f"[shepherd] {result.status.upper()} — {result.steps_completed} milestones in {result.duration_ms}ms\n")
                        if _should_end_session():
                            print("[shepherd] Task complete — ending session.\n")
                            break
                        continue

                # No workflow matched — fall through to free-form autonomous
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
                event_bus.emit("intent.autonomous_fallback", {"raw_text": intent.raw_text})
                result = engine.execute_autonomous(raw)
                _after_run(engine, telemetry, memory, result, confidence=1.0)
                if _should_end_session():
                    print("[shepherd] Task complete — ending session.\n")
                    break
                continue

            plan = router.resolve_plan(intent, mode=effective_mode)
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
                if effective_mode != "LOCKED" and AUTONOMOUS_ON_UNMATCHED and engine._agent_s.available:
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

            # Arm the spoken-stop listener now — mic is free since intent was captured
            if FEATURES["deepgram"]:
                try:
                    from services.deepgram_input import listen_for_stop_command
                    listen_for_stop_command(halt_callback=engine.request_halt)
                except Exception:
                    pass

            # ── Execute (synchronous, blocking) ───────────────────────────────
            result = engine.execute(resolved, intent_text=intent.raw_text)
            _after_run(engine, telemetry, memory, result, confidence=resolved.confidence)
            if _should_end_session():
                print("[shepherd] Task complete — ending session.\n")
                break

        except KeyboardInterrupt:
            print("\n[shepherd] Bye.")
            break
        except Exception as e:
            print(f"[shepherd] Unhandled error: {e}")
            sentry_capture(e, tags={"scope": "main_loop"})
        finally:
            idle.set()   # run finished (or errored) → let the CLI prompt return


_listen_mode = False  # set True by --listen; keeps the agent serving across goals


def _run_orchestrated(coords, telemetry, remote_intents, listen, default_kind) -> None:
    """Multi-agent loop: every goal becomes its own agent worker, all serialized
    through the ActionArbiter. Goals can carry a surface prefix to pick the lane:
        'browser: find the cheapest flight to NYC'   → a Browserbase agent
        'local:  take my selfie in Photo Booth'       → a local Agent S agent
    No prefix → DEFAULT_SURFACE_KIND."""
    from orchestrator import Orchestrator, surfaces

    orch = Orchestrator(
        on_event=lambda t, d: event_bus.emit(t, d),
        telemetry=telemetry, coords=coords,
    )
    # Let the dashboard's fleet endpoints reach this orchestrator.
    try:
        from dashboard.server import register_orchestrator
        register_orchestrator(orch)
    except Exception as e:
        print(f"[orchestrator] dashboard not wired ({e}); REST fleet control off")

    print("\n=== ORCHESTRATED (multi-agent) ===")
    print("Each goal spawns its own agent. Prefix 'browser:' or 'local:' to pick "
          "a lane; otherwise default is "
          f"'{default_kind}'.  Ctrl-C to quit.\n")

    def _dispatch(raw: str) -> None:
        kind = default_kind
        text = raw.strip()
        for prefix, k in (("browser:", surfaces.KIND_BROWSERBASE),
                          ("local:", surfaces.KIND_LOCAL)):
            if text.lower().startswith(prefix):
                kind, text = k, text[len(prefix):].strip()
                break
        if text:
            agent_id = orch.dispatch(text, surface_kind=kind)
            print(f"[orchestrator] dispatched {agent_id} ({kind}): {text}")

    # Drain the shared intent queue (frontend / coordinator / poller) in the
    # background so those producers spawn agents too.
    def _drain() -> None:
        while True:
            raw = remote_intents.get()
            if raw:
                try:
                    _dispatch(raw)
                except Exception as e:
                    print(f"[orchestrator] dispatch failed: {e}")
    threading.Thread(target=_drain, daemon=True).start()

    if listen:
        print("[shepherd] --listen: goals come from the frontend/coordinator only.\n")
        while True:
            try:
                time.sleep(3600)
            except KeyboardInterrupt:
                print("\n[shepherd] Bye."); return

    while True:
        try:
            line = input("Goal → ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[shepherd] Bye."); return
        if line:
            _dispatch(line)


def _port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _should_end_session() -> bool:
    """
    End the program once a task finishes (EXIT_WHEN_DONE), unless we're a remote
    agent or in --listen mode — both keep the session alive to serve more goals.
    """
    return EXIT_WHEN_DONE and not FEATURES["remote"] and not _listen_mode


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

    # Failed runs that were swallowed by the engine (status set, no exception
    # raised) still surface in Sentry as a message with full run context.
    if result.status == "failed":
        sentry_capture_message(
            f"Run failed: {result.routine_id} — {result.error or 'unknown error'}",
            tags={
                "routine_id": result.routine_id,
                "status": result.status,
            },
            context={
                "run_id": result.run_id,
                "error": result.error,
                "steps_completed": result.steps_completed,
                "duration_ms": result.duration_ms,
                "variables": result.variables,
            },
            trace_id=engine.last_trace_id,
        )

    print(f"[shepherd] {result.status.upper()} — {result.steps_completed} steps in {result.duration_ms}ms\n")


def _band_start(resolved) -> None:
    try:
        from services import band_collab
        band_collab.publish_event(
            "run.start",
            f"Shepherd starting {resolved.routine_id} "
            f"(confidence {resolved.confidence:.2f}).",
        )
    except Exception as e:
        print(f"[band] start non-fatal: {e}")


def _band_complete(result) -> None:
    try:
        from services import band_collab
        band_collab.publish_event(
            "run.complete",
            f"Shepherd finished {result.routine_id}: {result.status} "
            f"({result.steps_completed} steps, {result.duration_ms}ms).",
        )
    except Exception as e:
        print(f"[band] complete non-fatal: {e}")


if __name__ == "__main__":
    main()
