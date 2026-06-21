"""
Shepherd Coordinator — the central relay for remote orchestration / governance /
observability.

Topology (star, not p2p): every operated machine runs Shepherd plus an *outbound*
relay client (`services/relay_client.py`) that dials INTO this coordinator. The
remote Command Center (the Next.js app) also connects here. The coordinator is
therefore the only component that needs a public URL — agents never expose an
inbound port.

Two socket roles:
  /agent  — an operated machine. Streams events + screen frames up; receives
            commands (intent / approve / halt / override / mode) down.
  /ui     — a Command Center browser. Receives the live agent roster, every
            agent's event stream, and the watched agent's screen frames;
            sends commands targeted at a specific agent.

Everything here is observability + control plumbing. It holds no automation
logic and actuates nothing itself.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from config import COORDINATOR_PORT, COORDINATOR_TOKEN

# Reuse the agent's Deepgram transcription surface so the Command Center can turn
# a spoken command into an intent without a backend of its own.
try:
    from dashboard.deepgram_routes import router as deepgram_router
except Exception:  # pragma: no cover - optional
    deepgram_router = None

_AGENT_EVENT_HISTORY = 200


# ── Agent state ───────────────────────────────────────────────────────────────


@dataclass
class AgentConn:
    agent_id: str
    name: str
    host: str
    ws: WebSocket
    code: str = ""               # session / pairing code this agent belongs to
    online: bool = True
    status: str = "idle"          # idle | running | blocked | completed | failed
    mode: str = "LIVE"
    routine_id: Optional[str] = None
    run_id: Optional[str] = None
    step_index: Optional[int] = None
    total_steps: Optional[int] = None
    block: Optional[dict] = None   # populated while status == "blocked"
    last_activity: float = field(default_factory=time.time)
    last_frame: Optional[str] = None        # base64 JPEG
    last_frame_ts: float = 0.0
    history: deque = field(default_factory=lambda: deque(maxlen=_AGENT_EVENT_HISTORY))

    def snapshot(self) -> dict:
        return {
            "id":               self.agent_id,
            "name":             self.name,
            "host":             self.host,
            "code":             self.code,
            "online":           self.online,
            "status":           self.status,
            "mode":             self.mode,
            "routineId":        self.routine_id,
            "runId":            self.run_id,
            "currentStepIndex": self.step_index,
            "totalSteps":       self.total_steps,
            "progress":         self._progress(),
            "block":            self.block,
            "lastActivityAt":   _iso(self.last_activity),
            "hasFrame":         self.last_frame is not None,
        }

    def _progress(self) -> float:
        if self.status == "completed":
            return 1.0
        if not self.total_steps:
            return 0.0
        idx = (self.step_index or 0) + 1
        return max(0.0, min(1.0, idx / self.total_steps))


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + "Z"


class Hub:
    """In-memory registry + fan-out. Single event loop, so no locks needed."""

    def __init__(self) -> None:
        self.agents: dict[str, AgentConn] = {}
        self.uis: set[WebSocket] = set()
        self.ui_watch: dict[WebSocket, Optional[str]] = {}
        # The session code each UI is scoped to. None = unscoped (sees every
        # session — handy for a dev/fleet overview).
        self.ui_code: dict[WebSocket, Optional[str]] = {}

    # ── agent lifecycle ──────────────────────────────────────────────────────
    def register_agent(self, conn: AgentConn) -> None:
        self.agents[conn.agent_id] = conn

    def drop_agent(self, conn: AgentConn) -> None:
        # Only retire the connection if it is still the registered one. A
        # reconnect with the same agent_id replaces it in `self.agents`, so the
        # old socket's cleanup must not mark the new, live connection offline.
        current = self.agents.get(conn.agent_id)
        if current is conn:
            current.online = False

    # ── UI fan-out (scoped by session code) ──────────────────────────────────
    def _can_see(self, ws: WebSocket, agent: AgentConn) -> bool:
        """A UI sees an agent if it is unscoped or its code matches."""
        code = self.ui_code.get(ws)
        return code is None or code == agent.code

    async def broadcast_session(
        self, agent: AgentConn, message: dict, *, only_watching: bool = False
    ) -> None:
        payload = json.dumps(message)
        dead = []
        for ws in list(self.uis):
            if not self._can_see(ws, agent):
                continue
            if only_watching and self.ui_watch.get(ws) != agent.agent_id:
                continue
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._drop_ui(ws)

    async def push_roster(self) -> None:
        """Send each UI the roster scoped to its own session code."""
        dead = []
        for ws in list(self.uis):
            try:
                await ws.send_text(json.dumps(
                    {"type": "agents", "agents": self.roster_for(ws)}))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._drop_ui(ws)

    def roster_for(self, ws: WebSocket) -> list[dict]:
        return [a.snapshot() for a in self.agents.values() if self._can_see(ws, a)]

    def _drop_ui(self, ws: WebSocket) -> None:
        self.uis.discard(ws)
        self.ui_watch.pop(ws, None)
        self.ui_code.pop(ws, None)

    # ── event ingestion (derive agent status from the event stream) ──────────
    def apply_event(self, conn: AgentConn, event: dict) -> None:
        t = event.get("type", "")
        d = event.get("data", {}) or {}
        conn.last_activity = time.time()
        conn.history.append(event)

        if t == "execution.start":
            conn.status = "running"
            conn.routine_id = d.get("routine_id")
            conn.run_id = d.get("run_id")
            conn.mode = d.get("mode", conn.mode)
            conn.total_steps = d.get("total_steps")
            conn.step_index = None
            conn.block = None
        elif t == "step.start":
            conn.status = "running"
            conn.step_index = d.get("index")
            conn.total_steps = d.get("total", conn.total_steps)
        elif t == "monitor.alert":
            conn.status = "blocked"
            conn.block = {
                "stepIndex":   d.get("step_index"),
                "verdict":     d.get("verdict"),
                "trigger":     d.get("trigger"),
                "reason":      d.get("reason"),
                "suggestions": d.get("suggestions", []),
            }
        elif t == "monitor.decision":
            if d.get("decision") != "halt":
                conn.status = "running"
            conn.block = None
        elif t == "execution.complete":
            st = d.get("status", "completed")
            conn.status = {"completed": "completed", "failed": "failed"}.get(st, "idle")
            conn.block = None
        elif t == "execution.halted":
            conn.status = "idle"
            conn.block = None
        elif t == "mode.changed":
            conn.mode = d.get("mode", conn.mode)


hub = Hub()

app = FastAPI(title="Shepherd Coordinator", docs_url=None, redoc_url=None)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
if deepgram_router is not None:
    app.include_router(deepgram_router)


def _authorized(ws: WebSocket) -> bool:
    if not COORDINATOR_TOKEN:
        return True
    return ws.query_params.get("token") == COORDINATOR_TOKEN


# ── HTTP ──────────────────────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return HTMLResponse(
        "<h1>Shepherd Coordinator</h1>"
        "<p>Relay is up. Agents connect on <code>/agent</code>, "
        "the Command Center on <code>/ui</code>.</p>"
        f"<p>{len(hub.agents)} agent(s) registered.</p>"
    )


@app.get("/api/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "agents": len(hub.agents)})


@app.get("/api/agents")
async def list_agents(code: Optional[str] = None) -> JSONResponse:
    agents = [
        a.snapshot() for a in hub.agents.values()
        if code is None or a.code == code
    ]
    return JSONResponse(agents)


@app.get("/api/agents/{agent_id}/screen")
async def agent_screen(agent_id: str) -> JSONResponse:
    conn = hub.agents.get(agent_id)
    if not conn or not conn.last_frame:
        return JSONResponse({"error": "no frame"}, status_code=404)
    return JSONResponse({"data": conn.last_frame, "ts": conn.last_frame_ts})


# ── Agent socket ──────────────────────────────────────────────────────────────


@app.websocket("/agent")
async def agent_ws(ws: WebSocket) -> None:
    await ws.accept()
    if not _authorized(ws):
        await ws.close(code=4401)
        return

    qp = ws.query_params
    agent_id = qp.get("agent_id") or f"agent-{int(time.time())}"
    conn = AgentConn(
        agent_id=agent_id,
        name=qp.get("name") or agent_id,
        host=qp.get("host") or agent_id,
        code=qp.get("code") or agent_id,
        ws=ws,
    )
    hub.register_agent(conn)
    await hub.push_roster()

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")

            if mtype == "event":
                event = msg.get("event", {})
                hub.apply_event(conn, event)
                await hub.broadcast_session(
                    conn, {"type": "event", "agent_id": agent_id, "event": event}
                )
                await hub.push_roster()
            elif mtype == "frame":
                conn.last_frame = msg.get("data")
                conn.last_frame_ts = time.time()
                await hub.broadcast_session(
                    conn,
                    {"type": "frame", "agent_id": agent_id,
                     "data": conn.last_frame, "ts": conn.last_frame_ts},
                    only_watching=True,
                )
            elif mtype == "hello":
                conn.name = msg.get("name", conn.name)
                conn.host = msg.get("host", conn.host)
                conn.mode = msg.get("mode", conn.mode)
                await hub.push_roster()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.drop_agent(conn)
        await hub.push_roster()


# ── UI socket ─────────────────────────────────────────────────────────────────


@app.websocket("/ui")
async def ui_ws(ws: WebSocket) -> None:
    await ws.accept()
    if not _authorized(ws):
        await ws.close(code=4401)
        return

    hub.uis.add(ws)
    hub.ui_watch[ws] = None
    hub.ui_code[ws] = ws.query_params.get("code") or None  # "" => unscoped
    await ws.send_text(json.dumps({"type": "agents", "agents": hub.roster_for(ws)}))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            mtype = msg.get("type")

            if mtype == "watch":
                agent_id = msg.get("agent_id")
                hub.ui_watch[ws] = agent_id
                conn = hub.agents.get(agent_id) if agent_id else None
                if conn and hub._can_see(ws, conn):
                    # Replay this agent's recent events + last frame so the
                    # panel is populated immediately on selection.
                    for event in list(conn.history):
                        await ws.send_text(json.dumps(
                            {"type": "event", "agent_id": agent_id, "event": event}))
                    if conn.last_frame:
                        await ws.send_text(json.dumps(
                            {"type": "frame", "agent_id": agent_id,
                             "data": conn.last_frame, "ts": conn.last_frame_ts}))
            elif mtype == "unwatch":
                hub.ui_watch[ws] = None
            elif mtype == "command":
                await _relay_command(ws, msg)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub._drop_ui(ws)


async def _relay_command(ws: WebSocket, msg: dict) -> None:
    agent_id = msg.get("agent_id")
    conn = hub.agents.get(agent_id) if agent_id else None
    if not conn or not conn.online:
        return
    if not hub._can_see(ws, conn):   # can't command a session you're not in
        return
    out = {
        "type":    "command",
        "command": msg.get("command"),
        "payload": msg.get("payload", {}),
    }
    try:
        await conn.ws.send_text(json.dumps(out))
    except Exception:
        hub.drop_agent(conn)
        await hub.push_roster()


def start_coordinator() -> None:
    uvicorn.run(app, host="0.0.0.0", port=COORDINATOR_PORT, log_level="info")


if __name__ == "__main__":
    start_coordinator()
