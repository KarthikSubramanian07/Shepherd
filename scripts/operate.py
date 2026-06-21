#!/usr/bin/env python3
"""
Operated-machine launcher — one command to join a coordinator session and run.

This is the generalized, turnkey version of `remote_e2e.py`. It:
  1. Dials the coordinator relay (outbound only — no inbound port needed).
  2. Opens a target app URL in Chrome (via Playwright CDP).
  3. Runs the workflow executor, streaming events + frames to the coordinator.

The remote operator drives the Command Center to watch and steer the agent.

Configuration (env vars or CLI args):
  COORDINATOR_URL     ws(s)://host:port of the coordinator (required)
  COORDINATOR_TOKEN   shared auth secret (optional if coordinator has none)
  AGENT_PAIRING_CODE  session/pairing code (default: auto-generated)
  AGENT_ID            stable machine identifier (default: hostname)
  AGENT_NAME          human-readable label (default: hostname)
  TARGET_URL          URL to open in Chrome on startup (optional)
  WORKFLOW_ID         workflow ID to auto-run (optional; agent waits for intents if unset)
  RELAY_FPS           frames/sec pushed to coordinator (default: 3.0)
  RELAY_FRAME_WIDTH   downscale width in px (default: 1024)
  RELAY_FRAME_QUALITY JPEG quality 1-95 (default: 55)

Usage:
  # Minimal: join coordinator, open a URL, stream screen + await commands
  COORDINATOR_URL=ws://100.64.0.1:8770 AGENT_PAIRING_CODE=DEMO \
    TARGET_URL=https://example.com \
    python scripts/operate.py

  # With CLI overrides
  python scripts/operate.py \\
    --coordinator ws://coordinator.example.com \\
    --token my-secret \\
    --code DEMO \\
    --target-url file://path/to/app.html \\
    --workflow WF_LIVE_JOB_APPLICATION
"""
from __future__ import annotations

import argparse
import asyncio
import os
import queue
import sys
import threading
import time
from types import SimpleNamespace

# Ensure repo root is on the path before anything else.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Operated-machine launcher: dial coordinator, open app, run workflows.")
    p.add_argument("--coordinator", "-c", default=None,
                   help="Coordinator WebSocket URL (overrides COORDINATOR_URL env)")
    p.add_argument("--token", "-t", default=None,
                   help="Auth token (overrides COORDINATOR_TOKEN env)")
    p.add_argument("--code", default=None,
                   help="Session/pairing code (overrides AGENT_PAIRING_CODE env)")
    p.add_argument("--agent-id", default=None,
                   help="Agent ID (overrides AGENT_ID env)")
    p.add_argument("--agent-name", default=None,
                   help="Agent display name (overrides AGENT_NAME env)")
    p.add_argument("--target-url", default=None,
                   help="URL to open in Chrome on startup (overrides TARGET_URL env)")
    p.add_argument("--workflow", default=None,
                   help="Workflow ID to auto-run (overrides WORKFLOW_ID env)")
    p.add_argument("--fps", type=float, default=None,
                   help="Frame rate for screen streaming (overrides RELAY_FPS env)")
    p.add_argument("--frame-width", type=int, default=None,
                   help="Downscale width in px (overrides RELAY_FRAME_WIDTH env)")
    p.add_argument("--frame-quality", type=int, default=None,
                   help="JPEG quality 1-95 (overrides RELAY_FRAME_QUALITY env)")
    return p.parse_args()


def _apply_env(args: argparse.Namespace) -> None:
    """Push CLI args into env so config.py picks them up at import time."""
    mappings = [
        ("coordinator", "COORDINATOR_URL"),
        ("token", "COORDINATOR_TOKEN"),
        ("code", "AGENT_PAIRING_CODE"),
        ("agent_id", "AGENT_ID"),
        ("agent_name", "AGENT_NAME"),
        ("target_url", "TARGET_URL"),
        ("workflow", "WORKFLOW_ID"),
        ("fps", "RELAY_FPS"),
        ("frame_width", "RELAY_FRAME_WIDTH"),
        ("frame_quality", "RELAY_FRAME_QUALITY"),
    ]
    for attr, env_key in mappings:
        val = getattr(args, attr, None)
        if val is not None:
            os.environ[env_key] = str(val)

    # Ensure DISPLAY is set for screenshot capture on Linux.
    os.environ.setdefault("DISPLAY", ":0")


def _start_bus_loop():
    """Give the event bus an async loop so relay subscribers fire."""
    from dashboard.events import event_bus
    loop = asyncio.new_event_loop()
    threading.Thread(target=loop.run_forever, daemon=True).start()
    event_bus.set_async_loop(loop)
    return loop


def _make_emitter():
    """Create an event emitter that forwards to the local event bus."""
    from dashboard.events import event_bus

    def emit(event_type: str, data: dict) -> None:
        event_bus.emit(event_type, data)
        # Local trace for the operator running this script.
        if event_type == "workflow.node.enter":
            label = data.get("label", "?")
            step = data.get("step_no", "?")
            print(f"  [{step}] ENTER: {label}")
        elif event_type == "workflow.step":
            did = data.get("did", "")
            nxt = (data.get("next") or "END").split("::")[-1]
            print(f"       did: {did} → next={nxt}")
        elif event_type == "workflow.done":
            print(f"  DONE: status={data.get('status')} steps={data.get('steps')}")
        elif event_type == "workflow.awaiting":
            label = data.get("label", "?")
            print(f"  AWAITING operator directive at: {label}")

    return emit


def main() -> None:
    args = _parse_args()
    _apply_env(args)

    # Validate required config.
    coordinator_url = os.environ.get("COORDINATOR_URL", "")
    if not coordinator_url:
        print("ERROR: COORDINATOR_URL is required (env or --coordinator flag).")
        print("  Example: COORDINATOR_URL=ws://100.64.0.1:8770 python scripts/operate.py")
        sys.exit(1)

    # Now import the app modules (they read config at import time).
    import compat  # noqa: F401,E402
    from config import AGENT_ID, AGENT_PAIRING_CODE
    from dashboard.events import event_bus  # noqa: F401
    from services.relay_client import start_relay_client

    _start_bus_loop()

    # Start the relay client — connects to the coordinator.
    remote_intents: queue.Queue[str] = queue.Queue()
    engine_stub = SimpleNamespace(_mode="LIVE", request_halt=lambda: None)
    relay = start_relay_client(engine_stub, remote_intents)

    print(f"[operate] Connecting to coordinator: {coordinator_url}")
    print(f"[operate] Agent ID: {AGENT_ID} | Pairing code: {AGENT_PAIRING_CODE}")
    time.sleep(2.0)  # Let relay establish connection.

    # Open target URL in Chrome if specified.
    target_url = os.environ.get("TARGET_URL", "")
    page = None
    if target_url:
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start()
            browser = pw.chromium.connect_over_cdp("http://localhost:29229")
            ctx = browser.contexts[0]
            page = next(
                (pg for pg in ctx.pages if pg.url.startswith(("http://localhost:3000", "file://"))),
                ctx.pages[-1] if ctx.pages else ctx.new_page()
            )
            page.bring_to_front()
            page.goto(target_url)
            print(f"[operate] Opened target URL: {target_url}")
            time.sleep(1.0)
        except Exception as e:
            print(f"[operate] Warning: could not open target URL ({e}). Continuing without browser.")
            page = None

    # If a workflow was specified, run it.
    workflow_id = os.environ.get("WORKFLOW_ID", "")
    if workflow_id:
        try:
            from engine import workflow_control
            from engine.workflow_executor import WorkflowExecutor
            from engine.workflow_store import WorkflowStore

            store = WorkflowStore()
            workflow = store.get(workflow_id)
            if workflow:
                print(f"[operate] Running workflow: {workflow.name} ({workflow_id})")
                workflow_control.reset()

                def gate(turn):
                    workflow_control.request_pause()
                    return workflow_control.review(turn)

                # Use a no-op worker if no page (pure event streaming mode).
                worker = _build_worker(page)
                ex = WorkflowExecutor(worker, event_emit=_make_emitter(), gate=gate)
                run = ex.run(workflow, goal=f"Execute {workflow.name}", params={}, profile={})
                print(f"[operate] Workflow finished: status={run.status}, "
                      f"path={[s.label for s in run.path]}")
            else:
                print(f"[operate] Warning: workflow '{workflow_id}' not found in store.")
        except Exception as e:
            print(f"[operate] Workflow execution error: {e}")

    # Keep alive: stream screen + handle remote intents until Ctrl-C.
    # NOTE: operate.py is a lightweight launcher for quick demos. For
    # full intent dispatch (router → workflows/routines/autonomous), run
    # `main.py --listen` instead — it uses the same relay_client sidecar
    # for video streaming but routes intents through the full engine.
    print(f"\n[operate] Agent is live on the coordinator (code={AGENT_PAIRING_CODE}).")
    print("[operate] The operator can now connect to the Command Center and drive this machine.")
    print("[operate] For full intent dispatch, use: python main.py --listen")
    print("[operate] Press Ctrl-C to disconnect.\n")
    try:
        while True:
            try:
                intent = remote_intents.get(timeout=1.0)
                print(f"[operate] Remote intent received: {intent}")
                from dashboard.events import event_bus
                event_bus.emit("remote.intent.received", {"text": intent})
            except queue.Empty:
                pass
    except KeyboardInterrupt:
        print("\n[operate] Shutting down.")


def _build_worker(page):
    """Build a workflow worker appropriate for the available environment."""
    if page is None:
        return _NoOpWorker()
    try:
        from scripts.live_job_app import LiveJobAppWorker
        return LiveJobAppWorker(agent_s=None, page=page, ctx=page.context, vision=False)
    except Exception:
        return _NoOpWorker()


class _NoOpWorker:
    """Minimal worker stub that does nothing — used when no browser page is available."""

    def execute(self, turn):
        from engine.workflow_executor import WorkerResult
        return WorkerResult(did=f"(no-op) {turn.node.label}", extracted={})


if __name__ == "__main__":
    main()
