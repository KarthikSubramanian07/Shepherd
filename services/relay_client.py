"""
Relay client — the agent's outbound link to the central coordinator.

Runs in its own daemon thread (its own asyncio loop) and does three things, all
strictly OUTSIDE the click path:

  1. forwards every local `event_bus` event up to the coordinator,
  2. pushes downscaled screen frames at a low frame rate for the live view,
  3. receives commands (intent / approve / halt / override / mode) and applies
     them via the same primitives the local dashboard already uses
     (`engine.approvals`, `engine.request_halt`, the remote-intent queue).

If the coordinator is unreachable it retries with backoff and the agent keeps
running fully locally — the network is never on the critical path.
"""
from __future__ import annotations

import asyncio
import base64
import io
import queue
import threading
import time
from typing import Optional
from urllib.parse import urlencode

import config as _cfg
from config import (
    AGENT_HOST,
    AGENT_ID,
    AGENT_NAME,
    AGENT_PAIRING_CODE,
    COORDINATOR_TOKEN,
    COORDINATOR_URL,
    PROTOCOL_VERSION,
    RELAY_FPS,
    RELAY_FRAME_QUALITY,
    RELAY_FRAME_WIDTH,
)
from dashboard.events import event_bus


class RelayClient:
    def __init__(self, engine, remote_intents: "queue.Queue[str]") -> None:
        self._engine = engine
        self._remote_intents = remote_intents
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._event_q: Optional[asyncio.Queue] = None
        self._stop = threading.Event()

    # ── public ───────────────────────────────────────────────────────────────
    def start(self) -> None:
        threading.Thread(target=self._thread_main, daemon=True).start()

    # ── thread / loop bootstrap ───────────────────────────────────────────────
    def _thread_main(self) -> None:
        try:
            asyncio.run(self._run())
        except Exception as e:  # pragma: no cover - defensive
            print(f"[relay] fatal: {e}")

    async def _run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._event_q = asyncio.Queue(maxsize=1000)
        # Subscribe to the local event bus. The bus invokes this synchronously
        # from the dashboard loop thread, so we hop back onto our own loop.
        event_bus.subscribe(self._on_event)

        backoff = 1.0
        while not self._stop.is_set():
            try:
                await self._connect_once()
                backoff = 1.0
            except Exception as e:
                print(f"[relay] disconnected ({e}); retrying in {backoff:.0f}s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    # ── event bus bridge ──────────────────────────────────────────────────────
    def _on_event(self, message: dict) -> None:
        """Called from the dashboard loop thread; marshal onto the relay loop."""
        if self._loop is None or self._event_q is None:
            return
        try:
            self._loop.call_soon_threadsafe(self._enqueue, message)
        except RuntimeError:
            pass

    def _enqueue(self, message: dict) -> None:
        try:
            self._event_q.put_nowait(message)  # type: ignore[union-attr]
        except asyncio.QueueFull:
            pass

    # ── connection lifecycle ──────────────────────────────────────────────────
    async def _connect_once(self) -> None:
        import websockets

        params = {
            "agent_id": AGENT_ID,
            "name":     AGENT_NAME,
            "host":     AGENT_HOST,
            "code":     AGENT_PAIRING_CODE,
        }
        if COORDINATOR_TOKEN:
            params["token"] = COORDINATOR_TOKEN
        url = f"{_agent_ws_base()}/agent?{urlencode(params)}"

        async with websockets.connect(url, max_size=None, ping_interval=20) as ws:
            print(f"[relay] connected to coordinator as '{AGENT_ID}'")
            await ws.send(_json({"type": "hello", "name": AGENT_NAME,
                                 "host": AGENT_HOST, "mode": self._engine._mode,
                                 "protocol_version": PROTOCOL_VERSION}))
            await asyncio.gather(
                self._pump_events(ws),
                self._pump_frames(ws),
                self._recv_commands(ws),
            )

    async def _pump_events(self, ws) -> None:
        assert self._event_q is not None
        while True:
            message = await self._event_q.get()
            await ws.send(_json({"type": "event", "event": message}))

    async def _pump_frames(self, ws) -> None:
        interval = 1.0 / max(RELAY_FPS, 0.1)
        while True:
            frame = await self._loop.run_in_executor(None, _capture_frame)  # type: ignore[union-attr]
            if frame:
                await ws.send(_json({"type": "frame", "data": frame}))
            await asyncio.sleep(interval)

    async def _recv_commands(self, ws) -> None:
        async for raw in ws:
            try:
                msg = _loads(raw)
            except Exception:
                continue
            if msg.get("type") == "command":
                self._apply_command(msg.get("command"), msg.get("payload", {}) or {})

    # ── command dispatch (boundary-only primitives) ───────────────────────────
    def _apply_command(self, command: Optional[str], payload: dict) -> None:
        from engine.approvals import set_decision, set_override

        if command == "intent":
            text = (payload.get("text") or "").strip()
            if text:
                self._remote_intents.put(text)
                event_bus.emit("remote.intent", {"text": text, "source": "command-center"})
        elif command == "approve":
            set_decision("approve")
        elif command in ("halt", "stop"):
            # Resolve a pending approval gate AND arm the boundary halt flag, so
            # this works whether the agent is blocked at a monitor gate or mid-run.
            set_decision("halt")
            try:
                self._engine.request_halt()
            except Exception:
                pass
        elif command == "override":
            instruction = (payload.get("instruction") or "").strip()
            if instruction:
                set_override(instruction)
            else:
                set_decision("approve")
        elif command == "mode":
            mode = (payload.get("mode") or "").upper()
            if mode in ("LIVE", "LOCKED"):
                _cfg._runtime_mode = mode
                event_bus.emit("mode.changed", {"mode": mode})
        elif command in ("workflow.pause", "workflow.resume", "workflow.intervene"):
            self._apply_workflow_command(command, payload)

    def _apply_workflow_command(self, command: str, payload: dict) -> None:
        """Drive the milestone executor's control gate from the Command Center.

        Mirrors the local Control Hub's /api/workflow/* endpoints but over the
        relay command-down path, so a remote operator can pause / resume / steer
        a live traversal and crystallize a steer into the workflow ('remember')."""
        from engine import workflow_control

        if command == "workflow.pause":
            workflow_control.request_pause()
        elif command == "workflow.resume":
            workflow_control.clear_pause()
        elif command == "workflow.intervene":
            workflow_control.submit_intervention(
                instruction=(payload.get("instruction") or "").strip(),
                next_key=(payload.get("next_key") or payload.get("next") or "").strip(),
                scenario=(payload.get("scenario") or "").strip(),
                remember=bool(payload.get("remember")),
                decision=(payload.get("decision") or "override").strip() or "override",
                target_node=(payload.get("target_node") or "").strip(),
            )


# ── helpers ───────────────────────────────────────────────────────────────────


def _agent_ws_base() -> str:
    """Normalize COORDINATOR_URL to a ws(s):// base with no trailing slash."""
    url = COORDINATOR_URL.rstrip("/")
    if url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    return url


def _capture_frame() -> Optional[str]:
    """Grab the screen, downscale, JPEG-encode, return base64. Best-effort."""
    try:
        import pyautogui
        from PIL import Image

        img = pyautogui.screenshot()
        if RELAY_FRAME_WIDTH and img.width > RELAY_FRAME_WIDTH:
            ratio = RELAY_FRAME_WIDTH / img.width
            img = img.resize((RELAY_FRAME_WIDTH, int(img.height * ratio)), Image.BILINEAR)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=RELAY_FRAME_QUALITY)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:
        return None


def _json(obj: dict) -> str:
    import json
    return json.dumps(obj)


def _loads(raw) -> dict:
    import json
    return json.loads(raw)


def start_relay_client(engine, remote_intents: "queue.Queue[str]") -> Optional[RelayClient]:
    """Start the relay if a coordinator URL is configured; else no-op."""
    if not COORDINATOR_URL:
        return None
    client = RelayClient(engine, remote_intents)
    client.start()
    return client
