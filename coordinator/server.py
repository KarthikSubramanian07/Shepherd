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

from config import COORDINATOR_PORT, COORDINATOR_TOKEN, PROTOCOL_VERSION

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
    # Live workflow traversal state, built on the fly from workflow.* events so the
    # Command Center can render the milestone graph for this agent.
    workflow: Optional[dict] = None
    # Most recent ad-hoc dispatch routing decision (intent → workflow / autonomous),
    # surfaced so the operator can see what the vector router matched.
    routing: Optional[dict] = None
    # Live execution-trace graph, built on the fly from step.* events for runs that
    # are NOT following a saved workflow (autonomous goals / routines). This is the
    # granular "what is the agent actually doing right now" view; for a brand-new
    # task it is the trace being crystallized into a future workflow.
    trace: Optional[dict] = None

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
            "workflow":         self._workflow_view(),
            "routing":          self.routing,
            "trace":            self._trace_view(),
        }

    def _workflow_view(self) -> Optional[dict]:
        """Roster-safe view of the live workflow graph (no frames; the UI captures
        per-node screenshots client-side from the frame stream)."""
        if not self.workflow:
            return None
        wf = self.workflow
        return {
            "id":        wf.get("id"),
            "name":      wf.get("name"),
            "current":   wf.get("current"),
            "awaiting":  wf.get("awaiting", False),
            "nodes":     [wf["nodes"][k] for k in wf.get("order", []) if k in wf["nodes"]],
            "edges":     wf.get("edges", []),
            "status":    wf.get("status"),
            "baked":     wf.get("baked"),
            "finalize":  wf.get("finalize"),
            "finalized": wf.get("finalized"),
        }

    def _trace_view(self) -> Optional[dict]:
        """Roster-safe view of the live execution trace (granular step graph)."""
        if not self.trace:
            return None
        tr = self.trace
        return {
            "runId":     tr.get("run_id"),
            "routineId": tr.get("routine_id"),
            "kind":      tr.get("kind"),
            # known=False means there was no prior task graph → a brand-new task
            # whose trace is being crystallized into a future workflow.
            "known":     tr.get("known", False),
            "status":    tr.get("status", "running"),
            "current":   tr.get("current"),
            "nodes":     tr.get("nodes", []),
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

        if t == "intent.received":
            # An ad-hoc task was dispatched; routing is about to be decided.
            conn.routing = {"state": "routing", "text": d.get("raw_text"),
                            "source": d.get("source")}
        elif t == "plan.resolved":
            # The vector/keyword router resolved the intent to a target.
            kind = d.get("kind")
            if kind in ("WORKFLOW", "ROUTINE"):
                state = "matched"
            elif kind == "AUTONOMOUS":
                # A plan that resolves straight to autonomous is the same
                # outcome as the intent.autonomous_fallback path.
                state = "autonomous"
            else:
                state = "unmatched"
            conn.routing = {
                "state": state,
                "kind": kind, "target": d.get("target"),
                "confidence": d.get("confidence"), "source": d.get("source"),
                "matched": d.get("matched", []),
            }
        elif t in ("intent.unmatched", "intent.autonomous_fallback"):
            conn.routing = {
                "state": "autonomous" if t == "intent.autonomous_fallback" else "unmatched",
                "kind": "AUTONOMOUS" if t == "intent.autonomous_fallback" else None,
                "text": d.get("raw_text"),
            }
        elif t == "execution.start":
            conn.status = "running"
            conn.routine_id = d.get("routine_id")
            conn.run_id = d.get("run_id")
            conn.mode = d.get("mode", conn.mode)
            conn.total_steps = d.get("total_steps")
            conn.step_index = None
            conn.block = None
            # A run is starting: drop any stale workflow graph and open a fresh
            # trace. If this run is actually following a saved workflow, a
            # subsequent workflow.start re-establishes the workflow and clears
            # this trace (the two are mutually exclusive per run).
            conn.workflow = None
            conn.trace = _new_trace(d.get("run_id"), d.get("routine_id"), d.get("mode"))
        elif t == "task.graph.loaded":
            self._apply_trace_meta(conn, d)
        elif t in ("step.start", "step.agent_s_thinking", "step.complete",
                   "step.error", "step.deviation", "step.fallback"):
            if t == "step.start":
                conn.status = "running"
                conn.step_index = d.get("index")
                conn.total_steps = d.get("total", conn.total_steps)
            self._apply_trace_event(conn, t, d)
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
            conn.routing = None
            if conn.trace:
                conn.trace["status"] = st
                conn.trace["current"] = None
        elif t == "execution.halted":
            conn.status = "idle"
            conn.block = None
            conn.routing = None
            if conn.trace:
                conn.trace["status"] = "halted"
                conn.trace["current"] = None
        elif t == "mode.changed":
            conn.mode = d.get("mode", conn.mode)
        elif t.startswith("workflow."):
            self._apply_workflow_event(conn, t, d)

    # ── workflow traversal: build the live milestone graph from the stream ──────
    def _apply_workflow_event(self, conn: AgentConn, t: str, d: dict) -> None:
        if t == "workflow.start":
            conn.status = "running"
            conn.routine_id = d.get("workflow_id")
            conn.run_id = None
            conn.step_index = None
            conn.block = None
            # Following a saved workflow now → the granular step trace doesn't apply.
            conn.trace = None
            conn.workflow = {
                "id": d.get("workflow_id"), "name": d.get("name"),
                "current": d.get("start"), "awaiting": False,
                "nodes": {}, "order": [], "edges": [],
            }
            return

        wf = conn.workflow
        if wf is None:
            wf = conn.workflow = {"id": d.get("workflow_id"), "name": None,
                                  "current": None, "awaiting": False,
                                  "nodes": {}, "order": [], "edges": []}

        def _node(key: str) -> dict:
            node = wf["nodes"].get(key)
            if node is None:
                node = {"key": key, "status": "pending"}
                wf["nodes"][key] = node
                if key not in wf["order"]:
                    wf["order"].append(key)
            return node

        def _edge(frm: str, to: str, when: Optional[str]) -> None:
            if not frm or not to:
                return
            for e in wf["edges"]:
                if e["from"] == frm and e["to"] == to:
                    if when:
                        e["when"] = when
                    return
            wf["edges"].append({"from": frm, "to": to, "when": when})

        if t == "workflow.node.enter":
            conn.status = "running"
            key = d.get("node_key")
            node = _node(key)
            node.update({
                "key": key, "label": d.get("label"), "kind": d.get("kind"),
                "instruction": d.get("instruction"), "missing": d.get("missing", []),
                "conditionals": d.get("conditionals", []),
                "options": d.get("options", []), "status": "running",
            })
            for o in d.get("options", []):
                _edge(key, o.get("key"), o.get("when"))
            wf["current"] = key
            wf["awaiting"] = False
            conn.step_index = d.get("step_no")
            conn.block = None
        elif t == "workflow.intervention":
            key = d.get("node_key")
            node = _node(key)
            node["intervention"] = {
                "decision": d.get("decision"), "instruction": d.get("instruction"),
                "scenario": d.get("scenario"), "flag": d.get("flag"),
            }
            wf["awaiting"] = False
            conn.block = None
        elif t == "workflow.step":
            key = d.get("node_key")
            node = _node(key)
            st = d.get("status")
            node.update({
                "label": d.get("label", node.get("label")),
                "kind": d.get("kind", node.get("kind")),
                "status": "blocked" if st == "blocked" else "done",
                "did": d.get("did"), "branch": d.get("branch"),
                "next": d.get("next"), "extracted": d.get("extracted", []),
                # frame timestamp lets the UI pin the screenshot it captured for
                # this milestone from the live frame stream.
                "frameTs": conn.last_frame_ts,
            })
            for o in d.get("options", []):
                _edge(key, o.get("key"), o.get("when"))
            if d.get("next") and d.get("next") != "END":
                _edge(key, d.get("next"), d.get("branch"))
        elif t == "workflow.awaiting":
            conn.status = "blocked"
            key = d.get("node_key")
            _node(key)["status"] = "awaiting"
            wf["awaiting"] = True
            wf["current"] = key
            conn.step_index = d.get("step_no")
            conn.block = {
                "workflow": True, "stepIndex": d.get("step_no"), "nodeKey": key,
                "label": d.get("label"), "trigger": "workflow.awaiting",
                "reason": "Awaiting operator directive at this milestone",
                "options": d.get("options", []), "suggestions": [],
            }
        elif t == "workflow.baked":
            wf["baked"] = d.get("ops", d.get("applied", []))
        elif t == "workflow.finalize":
            # Run baked judgment calls; awaiting the operator's persist choice.
            conn.status = "blocked"
            wf["finalize"] = {
                "workflow_id": d.get("workflow_id"), "name": d.get("name"),
                "current_version": d.get("current_version"),
                "proposed_version": d.get("proposed_version"),
                "ops": d.get("ops", []),
            }
        elif t == "workflow.finalized":
            wf["finalize"] = None
            wf["finalized"] = {
                "action": d.get("action"), "workflow_id": d.get("workflow_id"),
                "version": d.get("version"),
            }
        elif t == "workflow.done":
            st = d.get("status")
            conn.status = {"completed": "completed", "blocked": "blocked",
                           "aborted": "failed"}.get(st, "idle")
            wf["awaiting"] = False
            wf["status"] = st
            conn.block = None
            # The dispatch routing decision is per-run; drop it so the banner
            # doesn't linger as stale after the run ends (re-set on next dispatch).
            conn.routing = None

    # ── execution trace: build the live granular step graph from step.* events ──
    def _apply_trace_meta(self, conn: AgentConn, d: dict) -> None:
        """task.graph.loaded carries whether a crystallized graph already existed."""
        if conn.trace is None:
            conn.trace = _new_trace(d.get("run_id"), d.get("routine_id"), None)
        tr = conn.trace
        if d.get("run_id"):
            tr["run_id"] = d.get("run_id")
        if d.get("routine_id"):
            tr["routine_id"] = d.get("routine_id")
        tr["known"] = bool(d.get("known"))

    def _apply_trace_event(self, conn: AgentConn, t: str, d: dict) -> None:
        # A workflow run streams workflow.* (not step.*); never shadow an active
        # workflow with a step trace.
        if conn.workflow is not None:
            return
        if conn.trace is None:
            conn.trace = _new_trace(d.get("run_id"), None, None)
        tr = conn.trace
        # step.deviation uses `step_index`; every other step.* uses `index`.
        idx = d.get("index")
        if idx is None:
            idx = d.get("step_index")
        if idx is None:
            return
        if len(tr["by_index"]) >= _MAX_TRACE_NODES and idx not in tr["by_index"]:
            return
        by = tr["by_index"]
        node = by.get(idx)
        if node is None:
            node = {"index": idx, "status": "pending"}
            by[idx] = node
            tr["nodes"].append(node)

        if t == "step.start":
            node.update({"action": d.get("action"),
                         "description": d.get("description"), "status": "running"})
            tr["current"] = idx
        elif t == "step.agent_s_thinking":
            node["thinking"] = d.get("description") or node.get("thinking")
        elif t == "step.complete":
            node.update({"status": d.get("status", "completed"),
                         "durationMs": d.get("duration_ms"),
                         # pin the screenshot the UI captured from the frame stream.
                         "frameTs": conn.last_frame_ts})
        elif t == "step.error":
            node.update({"status": "error", "error": d.get("error")})
        elif t in ("step.deviation", "step.fallback"):
            node["note"] = d.get("reason") or d.get("description") or t


# Cap trace growth so a misbehaving/compromised agent can't exhaust coordinator
# memory or UI fan-out bandwidth by emitting unbounded unique step indexes.
_MAX_TRACE_NODES = 2000


def _new_trace(run_id: Optional[str], routine_id: Optional[str],
               kind: Optional[str]) -> dict:
    return {
        "run_id": run_id, "routine_id": routine_id, "kind": kind,
        "known": None, "status": "running", "current": None,
        "nodes": [], "by_index": {},
    }


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
    return JSONResponse({
        "ok": True,
        "agents": len(hub.agents),
        "protocol_version": PROTOCOL_VERSION,
    })


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
                first_frame = conn.last_frame is None
                conn.last_frame = msg.get("data")
                conn.last_frame_ts = time.time()
                await hub.broadcast_session(
                    conn,
                    {"type": "frame", "agent_id": agent_id,
                     "data": conn.last_frame, "ts": conn.last_frame_ts},
                    only_watching=True,
                )
                # The first frame flips snapshot()["hasFrame"]; refresh the
                # roster so the UI learns this agent has a live screen.
                if first_frame:
                    await hub.push_roster()
            elif mtype == "hello":
                conn.name = msg.get("name", conn.name)
                conn.host = msg.get("host", conn.host)
                conn.mode = msg.get("mode", conn.mode)
                client_version = msg.get("protocol_version")
                if isinstance(client_version, int) and client_version > PROTOCOL_VERSION:
                    print(f"[coordinator] warning: agent '{agent_id}' speaks "
                          f"protocol v{client_version}, we only support v{PROTOCOL_VERSION}")
                await hub.push_roster()
            elif mtype in ("webrtc.offer", "webrtc.answer", "webrtc.ice"):
                # WebRTC signaling: relay to the watching UI(s) for this agent.
                await hub.broadcast_session(
                    conn,
                    {"type": mtype, "agent_id": agent_id,
                     "data": msg.get("data")},
                    only_watching=True,
                )
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
            elif mtype in ("webrtc.answer", "webrtc.ice"):
                # WebRTC signaling from UI → agent.
                agent_id = msg.get("agent_id")
                conn = hub.agents.get(agent_id) if agent_id else None
                if conn and conn.online and hub._can_see(ws, conn):
                    try:
                        await conn.ws.send_text(json.dumps(
                            {"type": mtype, "data": msg.get("data")}))
                    except Exception:
                        hub.drop_agent(conn)
                        await hub.push_roster()
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
