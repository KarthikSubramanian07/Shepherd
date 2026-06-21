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
    WEBRTC_ENABLED,
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

            # Push the local catalog (routines, workflows, task-graphs) so the
            # remote Command Center can browse them without hitting :8765 directly.
            catalog = await self._loop.run_in_executor(None, _collect_catalog)
            if catalog:
                await ws.send(_json({"type": "catalog", "catalog": catalog}))

            # Start WebRTC P2P screen streaming if enabled.
            # Close any previous sender from a prior connection cycle.
            if getattr(self, '_webrtc', None):
                self._webrtc.close()
            self._webrtc = None
            if WEBRTC_ENABLED:
                try:
                    from services.webrtc_sender import WebRTCSender
                    sender = WebRTCSender(
                        ws, fps=RELAY_FPS, width=RELAY_FRAME_WIDTH, quality=RELAY_FRAME_QUALITY
                    )
                    if sender.available:
                        ok = await sender.start()
                        if ok:
                            self._webrtc = sender
                            print("[relay] WebRTC P2P mode active (JPEG relay continues as fallback)")
                except Exception as e:
                    print(f"[relay] WebRTC init failed (non-fatal): {e}")

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
            # Skip JPEG frames when WebRTC P2P is connected (video goes direct).
            if getattr(self, '_webrtc', None) and self._webrtc.is_connected:
                await asyncio.sleep(interval)
                continue
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
            mtype = msg.get("type")
            if mtype == "command":
                self._apply_command(msg.get("command"), msg.get("payload", {}) or {})
            elif mtype == "webrtc.answer" and self._webrtc:
                await self._webrtc.handle_answer(msg.get("data", {}))
            elif mtype == "webrtc.ice" and self._webrtc:
                await self._webrtc.handle_ice(msg.get("data", {}))

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
        elif command in ("workflow.pause", "workflow.resume", "workflow.intervene",
                         "workflow.finalize"):
            self._apply_workflow_command(command, payload)
        elif command == "promote":
            self._promote_graph(payload)

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
        elif command == "workflow.finalize":
            workflow_control.submit_finalize(
                decision=(payload.get("decision") or "persist").strip() or "persist",
                new_id=(payload.get("new_id") or "").strip(),
                name=(payload.get("name") or "").strip(),
            )

    def _promote_graph(self, payload: dict) -> None:
        """Auto-promote a crystallized task graph into a dispatchable workflow.

        Called by the Command Center's 'Bake out a new workflow' toggle after the
        coalescer saves the graph (task.graph.saved event). Delegates to the
        shared promote_graph() helper which derives name + intent_patterns from
        the graph's stored intents and fires async LLM description generation."""
        try:
            from engine.workflow_promote import promote_graph

            task_key = (payload.get("task_key") or "").strip()
            if not task_key:
                return

            promote_graph(task_key)
        except Exception as e:
            print(f"[relay] promote failed (non-fatal): {e}")


# ── helpers ───────────────────────────────────────────────────────────────────


def _agent_ws_base() -> str:
    """Normalize COORDINATOR_URL to a ws(s):// base with no trailing slash."""
    url = COORDINATOR_URL.rstrip("/")
    if url.startswith("http://"):
        url = "ws://" + url[len("http://"):]
    elif url.startswith("https://"):
        url = "wss://" + url[len("https://"):]
    return url


_cdp_pw = None    # cached Playwright server handle (prevents subprocess leak)
_cdp_page = None  # cached Playwright CDP page for frame capture fallback


def _get_cdp_page():
    """Lazily connect to Chrome via CDP and cache the page reference."""
    global _cdp_pw, _cdp_page
    if _cdp_page is not None:
        try:
            _cdp_page.url  # verify connection is still alive
            return _cdp_page
        except Exception:
            _cdp_page = None
    # Stop previous Playwright server if it exists (prevents subprocess leak).
    if _cdp_pw is not None:
        try:
            _cdp_pw.stop()
        except Exception:
            pass
        _cdp_pw = None
    try:
        from playwright.sync_api import sync_playwright
        _cdp_pw = sync_playwright().start()
        browser = _cdp_pw.chromium.connect_over_cdp("http://localhost:29229")
        ctx = browser.contexts[0] if browser.contexts else None
        _cdp_page = ctx.pages[0] if ctx and ctx.pages else None
        return _cdp_page
    except Exception:
        return None


def _capture_frame() -> Optional[str]:
    """Grab the screen, downscale, JPEG-encode, return base64. Best-effort.

    Tries pyautogui first (full desktop), falls back to Playwright CDP screenshot
    (browser viewport only) if pyautogui fails (e.g. no DISPLAY auth on headless VMs).
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    img = None
    # Primary: pyautogui full-screen capture.
    try:
        import pyautogui
        img = pyautogui.screenshot()
    except Exception:
        pass

    # Fallback: Playwright CDP screenshot (captures active browser tab).
    if img is None:
        try:
            page = _get_cdp_page()
            if page:
                raw = page.screenshot(type="jpeg", quality=RELAY_FRAME_QUALITY)
                # Skip PIL decode if no resize needed — avoids double JPEG encoding.
                if not RELAY_FRAME_WIDTH:
                    return base64.b64encode(raw).decode("ascii")
                img = Image.open(io.BytesIO(raw))
                if img.width <= RELAY_FRAME_WIDTH:
                    return base64.b64encode(raw).decode("ascii")
        except Exception:
            pass

    if img is None:
        return None

    if RELAY_FRAME_WIDTH and img.width > RELAY_FRAME_WIDTH:
        ratio = RELAY_FRAME_WIDTH / img.width
        img = img.resize((RELAY_FRAME_WIDTH, int(img.height * ratio)), Image.BILINEAR)
    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=RELAY_FRAME_QUALITY)
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _collect_catalog() -> Optional[dict]:
    """Gather the agent's local catalog for the coordinator to cache.

    Returns a dict with routines, workflows, and task_graphs — or None if
    collection fails (non-fatal; the agent still works without catalog push).
    """
    catalog: dict = {}
    try:
        from engine.routines import load_routines, get_routine
        routine_ids = load_routines()
        routines_out = []
        for rid in routine_ids:
            try:
                r = get_routine(rid)
                name = (r.description or rid).split(" — ")[0].split(" – ")[0].strip()
                routines_out.append({
                    "id": r.routine_id, "name": name,
                    "description": r.description or "",
                    "mode": r.mode, "stepCount": len(r.steps),
                    "version": 1,
                })
            except Exception:
                pass
        catalog["routines"] = routines_out
    except Exception:
        catalog["routines"] = []

    try:
        from engine.workflow_store import WorkflowStore
        catalog["workflows"] = [
            {"id": w.id, "name": w.name, "description": getattr(w, "description", None),
             "version": w.version, "intent_patterns": w.intent_patterns,
             "params": w.params, "nodes": len(w.nodes),
             "updated_at": w.updated_at}
            for w in WorkflowStore().list()
        ]
    except Exception:
        catalog["workflows"] = []

    try:
        from engine.task_graph import TaskGraphStore
        raw = TaskGraphStore().all_graphs()
        catalog["task_graphs"] = [
            {"task_key": key, "routine_id": g.get("routine_id"),
             "run_count": g.get("run_count", 0),
             "node_count": len(g.get("nodes", [])),
             "edge_count": len(g.get("edges", [])),
             "updated_at": g.get("updated_at", 0),
             "intents": g.get("intents", []),
             "labels": [n.get("label") for n in g.get("nodes", [])]}
            for key, g in raw.items()
        ]
    except Exception:
        catalog["task_graphs"] = []

    if not catalog["routines"] and not catalog["workflows"] and not catalog["task_graphs"]:
        return None
    return catalog


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
