"""
Dashboard WebSocket server — serves the Control Hub UI and streams live events.
Lane B owns the UI (dashboard/static/index.html).
This file: routing, WebSocket broadcast, replay API, static serving.
"""
import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

import config as _cfg
from config import DASHBOARD_PORT
from dashboard.events import event_bus
from dashboard.deepgram_routes import router as deepgram_router

_started_at = time.time()
_state: dict = {
    "status": "idle",
    "mode":   _cfg.EXECUTION_MODE,
    "routine_id": None,
    "run_id":     None,
    "step_index": None,
    "workflow_id": None,
    "node_key":   None,
    "awaiting":   False,
}

# ── Intervention tracking ──────────────────────────────────────────────────
_interventions: list[dict] = []
_intervention_counter: int = 0


def _create_intervention(data: dict) -> None:
    global _intervention_counter
    _intervention_counter += 1
    step_index = data.get("step_index", 0)
    now_iso = datetime.utcnow().isoformat() + "Z"
    reason = data.get("reason", "")
    verdict = data.get("verdict", "flag")

    def _trigger(r: str) -> str:
        r = r.lower()
        if any(k in r for k in ("credential", "password", "api key", "secret")):
            return "credential"
        if "captcha" in r:
            return "captcha"
        if any(k in r for k in ("phishing", "injection", "disregard")):
            return "phishing"
        if "stuck" in r:
            return "stuck"
        return "deviation"

    _interventions.append({
        "id": f"iv-{_intervention_counter}",
        "runId": _state.get("run_id") or "",
        "agentId": "shepherd-agent",
        "routineId": _state.get("routine_id") or "",
        "stepId": f"step-{step_index}",
        "detection": {
            "type": _trigger(reason),
            "verdict": verdict,
            "reason": reason,
            "stepId": f"step-{step_index}",
            "detectedAt": now_iso,
            "requiresHuman": True,
        },
        "status": "pending",
        "resolution": None,
        "resolvedBy": None,
        "resolvedAt": None,
        "note": None,
        "createdAt": now_iso,
    })


# ── TypeScript-compatible shape helpers ────────────────────────────────────

def _routine_to_ts(routine_id: str) -> dict:
    from engine.routines import get_routine
    r = get_routine(routine_id)
    now_iso = datetime.utcnow().isoformat() + "Z"
    steps_out = []
    si = getattr(r, "step_instructions", None) or {}
    for i, s in enumerate(r.steps):
        steps_out.append({
            "id": f"step-{i}",
            "index": i,
            "action": s.action,
            "title": getattr(s, "description", None) or s.action,
            "instruction": si.get(i),
            "target": getattr(s, "target", None),
            "text": getattr(s, "text", None),
            "highStakes": i in r.high_stakes_steps,
            "monitorTrigger": getattr(s, "monitor_trigger", None) or None,
        })
    edges_out = [
        {"id": f"e-{i}-{i + 1}", "source": f"step-{i}", "target": f"step-{i + 1}"}
        for i in range(len(r.steps) - 1)
    ]
    name = (r.description or routine_id).split(" — ")[0].split(" – ")[0].strip()
    running = _state.get("routine_id") == routine_id and _state.get("status") == "running"
    return {
        "id": r.routine_id,
        "name": name,
        "description": r.description or "",
        "mode": r.mode,
        "tags": [r.mode.lower(), "automation"],
        "version": 1,
        "stepCount": len(r.steps),
        "updatedAt": now_iso,
        "reliability": 1.0,
        "activeAgents": 1 if running else 0,
        "variables": r.variables,
        "steps": steps_out,
        "edges": edges_out,
        "createdAt": now_iso,
    }


def _make_agent() -> dict:
    status = _state.get("status", "idle")
    routine_id = _state.get("routine_id") or ""
    run_id = _state.get("run_id")
    step_index = _state.get("step_index")
    agent_status = {"idle": "idle", "running": "running", "halted": "blocked"}.get(status, "idle")
    progress = 0.0
    if routine_id and step_index is not None:
        try:
            from engine.routines import get_routine
            r = get_routine(routine_id)
            total = max(len(r.steps), 1)
            progress = round(min(step_index / total, 1.0), 3)
        except Exception:
            pass
    routine_name = routine_id or "—"
    if routine_id:
        try:
            from engine.routines import get_routine
            r = get_routine(routine_id)
            routine_name = (r.description or routine_id).split(" — ")[0].split(" – ")[0].strip()
        except Exception:
            pass
    now_iso = datetime.utcnow().isoformat() + "Z"
    agent: dict = {
        "id": "shepherd-agent",
        "name": "Shepherd",
        "routineId": routine_id,
        "routineName": routine_name,
        "runId": run_id,
        "status": agent_status,
        "currentStepId": f"step-{step_index}" if step_index is not None else None,
        "currentStepIndex": step_index,
        "progress": progress,
        "host": "localhost",
        "startedAt": None,
        "lastActivityAt": now_iso if status != "idle" else None,
    }
    if agent_status == "blocked":
        pending = [iv for iv in _interventions if iv["status"] == "pending"]
        if pending:
            agent["block"] = pending[-1]["detection"]
    return agent


def _ts_iso(v) -> str | None:
    if v is None:
        return None
    try:
        return datetime.utcfromtimestamp(float(v)).isoformat() + "Z"
    except Exception:
        return None


def _run_status(steps: list) -> str:
    for s in steps:
        if s.get("status") == "halted":
            return "aborted"
        if s.get("status") == "failed":
            return "failed"
    return "completed"


def _run_summary(r: dict) -> dict:
    steps = r.get("steps", [])
    routine_id = r.get("routine_id", "")
    routine_name = routine_id
    try:
        from engine.routines import get_routine
        rt = get_routine(routine_id)
        routine_name = (rt.description or routine_id).split(" — ")[0].split(" – ")[0].strip()
    except Exception:
        pass
    return {
        "id": r.get("run_id", ""),
        "routineId": routine_id,
        "routineName": routine_name,
        "agentId": "shepherd-agent",
        "agentName": "Shepherd",
        "status": _run_status(steps),
        "startedAt": _ts_iso(r.get("started_at")) or datetime.utcnow().isoformat() + "Z",
        "endedAt": _ts_iso(r.get("ended_at")),
        "confidence": r.get("confidence", 1.0),
    }


def _run_full(r: dict) -> dict:
    steps_out = []
    for s in r.get("steps", []):
        idx = s.get("index", 0)
        steps_out.append({
            "stepId": f"step-{idx}",
            "index": idx,
            "status": s.get("status", "completed"),
            "startedAt": _ts_iso(s.get("started_at")),
            "durationMs": s.get("duration_ms"),
            "error": s.get("error"),
            "monitorVerdict": s.get("monitor_verdict"),
            "deviation": s.get("deviation"),
        })
    return {**_run_summary(r), "variables": r.get("variables", {}), "steps": steps_out}

app = FastAPI(title="Shepherd Control Hub", docs_url=None, redoc_url=None)

# CORS — allow the Next.js dashboard (any localhost port in dev) plus any extra
# origins listed in CORS_ALLOW_ORIGINS (comma-separated, e.g. an ngrok URL).
_cors_extra = [o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_extra,
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(deepgram_router)

FRONTEND_DIR = Path(__file__).parent.parent / "frontend"

_sockets: list[WebSocket] = []
_ws_lock = asyncio.Lock()


@app.on_event("startup")
async def _startup() -> None:
    loop = asyncio.get_event_loop()
    event_bus.set_async_loop(loop)

    async def _broadcast(message: dict) -> None:
        payload = json.dumps(message)
        _track_state(message)
        async with _ws_lock:
            dead = []
            for ws in _sockets:
                try:
                    await ws.send_text(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _sockets.remove(ws)

    event_bus.subscribe(_broadcast)


def _track_state(message: dict) -> None:
    t = message.get("type", "")
    d = message.get("data", {})
    if t == "execution.start":
        _state.update(status="running", routine_id=d.get("routine_id"),
                      run_id=d.get("run_id"), mode=d.get("mode", _state["mode"]))
    elif t == "step.start":
        _state["step_index"] = d.get("index")
    elif t in ("execution.complete", "execution.halted"):
        _state.update(status="halted" if t == "execution.halted" else "idle",
                      step_index=None)
    elif t == "monitor.alert":
        _state["status"] = "halted"
        _create_intervention(d)
    elif t == "monitor.decision":
        if d.get("decision") != "halt":
            _state["status"] = "running"
        # Resolve the most recent pending intervention
        for iv in reversed(_interventions):
            if iv["status"] == "pending":
                iv["status"] = "resolved"
                iv["resolution"] = "approved" if d.get("decision") == "approve" else "rejected"
                iv["resolvedAt"] = datetime.utcnow().isoformat() + "Z"
                break
    elif t == "workflow.start":
        _state.update(status="running", workflow_id=d.get("workflow_id"),
                      node_key=d.get("start"), awaiting=False)
    elif t == "workflow.node.enter":
        _state.update(status="running", node_key=d.get("node_key"),
                      step_index=d.get("step_no"), awaiting=False)
    elif t == "workflow.awaiting":
        _state["awaiting"] = True
    elif t == "workflow.done":
        _state.update(status="idle", node_key=None, awaiting=False)


@app.get("/", response_class=HTMLResponse)
async def root() -> HTMLResponse:
    return (FRONTEND_DIR / "index.html").read_text()


@app.get("/api/screenshot")
async def get_screenshot():
    try:
        import io
        import pyautogui
        from fastapi.responses import Response
        buf = io.BytesIO()
        pyautogui.screenshot().save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")
    except Exception:
        return Response(status_code=204)


@app.get("/demo-form", response_class=HTMLResponse)
async def demo_form() -> HTMLResponse:
    f = Path(__file__).parent.parent / "data" / "demo_form.html"
    return f.read_text() if f.exists() else HTMLResponse("<h1>demo_form.html missing</h1>")


@app.get("/demo-mail", response_class=HTMLResponse)
async def demo_mail() -> HTMLResponse:
    f = Path(__file__).parent.parent / "data" / "demo_mail.html"
    return f.read_text() if f.exists() else HTMLResponse("<h1>demo_mail.html missing</h1>")


@app.get("/demo-web", response_class=HTMLResponse)
async def demo_web() -> HTMLResponse:
    return HTMLResponse("""
<html><body style="font:monospace;background:#0f1117;color:#cdd9e5;padding:2rem">
<h2 style="color:#2f81f7">Browserbase Local Fallback</h2>
<p>Network unavailable — showing local stub.</p>
<p>In live mode this would be a real remote browser session via Browserbase.</p>
</body></html>""")


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    # Replay history before joining broadcast list so live events don't interleave
    for event in event_bus.get_history():
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            return
    async with _ws_lock:
        _sockets.append(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        async with _ws_lock:
            if ws in _sockets:
                _sockets.remove(ws)


@app.get("/api/runs")
async def get_runs() -> JSONResponse:
    try:
        from telemetry.memory import ExecutionMemory
        raw = ExecutionMemory().recent(20)
        return JSONResponse([_run_summary(r) for r in raw])
    except Exception:
        return JSONResponse([])


@app.get("/api/routines")
async def list_routines() -> JSONResponse:
    try:
        from engine.routines import load_routines
        routines = load_routines()
        return JSONResponse([_routine_to_ts(rid) for rid in routines])
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/routines/{routine_id}")
async def get_routine_info(routine_id: str) -> JSONResponse:
    try:
        return JSONResponse(_routine_to_ts(routine_id))
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/control/{decision}")
async def control_decision(decision: str, request: Request) -> JSONResponse:
    """approve | halt | override — override body:
       {"instruction": "...", "flag": "one_off" | "save_as_rule"}

       flag is the teaching gate: save_as_rule bakes the resolution into the
       workflow as a conditional clause; one_off applies it for this run only.
    """
    from engine.approvals import set_decision, set_override
    if decision == "override":
        try:
            body = await request.json()
            instruction = (body.get("instruction") or "").strip()
            flag = (body.get("flag") or "one_off").strip()
        except Exception:
            instruction, flag = "", "one_off"
        if instruction:
            set_override(instruction, flag)
        else:
            set_decision("approve")
    elif decision in ("approve", "halt"):
        set_decision(decision)
    else:
        return JSONResponse({"error": "invalid decision"}, status_code=400)
    return JSONResponse({"ok": True, "decision": decision})


@app.post("/api/workflow/{action}")
async def workflow_control_action(action: str, request: Request) -> JSONResponse:
    """Control Hub hook into a live workflow traversal (the milestone executor).

      pause / resume   — block the next milestone awaiting a directive, or release.
      intervene        — steer a milestone in ONE message. Body:
        {"instruction": "...",        # NL action to take here (override)
         "next_key": "<node key>",    # force a branch / next milestone (optional)
         "scenario": "...",           # the `when` this applies under
         "remember": true|false,      # true → bake into the workflow (save_as_rule)
         "target_node": "<node key>", # apply only at this node ("" = next milestone)
         "decision": "override|halt|approve"}

    Mirrors /api/control but targets engine.workflow_control instead of the
    step-executor's approvals gate, so a remote operator can monitor (over /ws +
    /api/screenshot) and steer the traversal of the operated machine.
    """
    from engine import workflow_control
    if action == "pause":
        workflow_control.request_pause()
        return JSONResponse({"ok": True, "paused": True})
    if action == "resume":
        workflow_control.clear_pause()
        return JSONResponse({"ok": True, "paused": False})
    if action == "intervene":
        try:
            body = await request.json()
        except Exception:
            body = {}
        workflow_control.submit_intervention(
            instruction=(body.get("instruction") or "").strip(),
            next_key=(body.get("next_key") or "").strip(),
            scenario=(body.get("scenario") or "").strip(),
            remember=bool(body.get("remember")),
            decision=(body.get("decision") or "override").strip(),
            target_node=(body.get("target_node") or "").strip(),
        )
        return JSONResponse({"ok": True})
    if action == "finalize":
        # End-of-run persist gate: the operator's choice for the baked workflow.
        #   {"decision": "persist" | "save_as_new" | "discard",
        #    "new_id": "...", "name": "..."}   (new_id/name only for save_as_new)
        try:
            body = await request.json()
        except Exception:
            body = {}
        workflow_control.submit_finalize(
            decision=(body.get("decision") or "persist").strip(),
            new_id=(body.get("new_id") or "").strip(),
            name=(body.get("name") or "").strip(),
        )
        return JSONResponse({"ok": True})
    return JSONResponse({"error": "action must be pause|resume|intervene|finalize"},
                        status_code=400)


@app.get("/api/workflows")
async def list_workflows() -> JSONResponse:
    """Saved dispatchable workflows (id, name, version, intent patterns)."""
    try:
        from engine.workflow_store import WorkflowStore
        return JSONResponse([
            {"id": w.id, "name": w.name, "description": w.description,
             "version": w.version, "intent_patterns": w.intent_patterns,
             "params": w.params, "nodes": len(w.nodes),
             "updated_at": w.updated_at}
            for w in WorkflowStore().list()
        ])
    except Exception:
        return JSONResponse([])


@app.get("/api/workflows/{workflow_id}")
async def get_workflow(workflow_id: str) -> JSONResponse:
    """Full workflow (nodes + edges + taught layer) for rendering the graph."""
    try:
        from engine.workflow_store import WorkflowStore, _serialize
        wf = WorkflowStore().get(workflow_id)
        if wf is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return JSONResponse(_serialize(wf))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/status")
async def get_status() -> JSONResponse:
    return JSONResponse({
        **_state,
        "uptime_s": round(time.time() - _started_at),
        "dashboard_port": DASHBOARD_PORT,
    })


@app.get("/api/agentspan/status")
async def agentspan_status() -> JSONResponse:
    """The research digression runs as a real Agentspan (Orkes) agent on a durable
    self-hosted server. Surface its reachability + last execution so the Control
    Hub can show the agent engine working during the demo."""
    try:
        from services import agentspan_research
        return JSONResponse(agentspan_research.status())
    except Exception as e:
        return JSONResponse({"available": False, "error": str(e)})


# Cached, reused across polls — the dashboard hits /api/redis/stats every few
# seconds, so we keep one Redis client + one memory/cache handle alive instead of
# opening fresh connections each time.
_stats_redis_client = None
_stats_memory = None
_stats_semcache = None


def _stats_redis():
    global _stats_redis_client
    if _stats_redis_client is None:
        import redis as _redis
        from config import REDIS_URL
        _stats_redis_client = _redis.from_url(REDIS_URL, decode_responses=True)
    return _stats_redis_client


def _stats_memory_handle():
    global _stats_memory
    if _stats_memory is None or not _stats_memory.online:
        from telemetry.memory import ExecutionMemory
        _stats_memory = ExecutionMemory()
    return _stats_memory


def _stats_semcache_handle():
    global _stats_semcache
    if _stats_semcache is None or not _stats_semcache.connected:
        from services.semantic_cache import SemanticCache
        _stats_semcache = SemanticCache("milestones")
    return _stats_semcache


@app.get("/api/redis/stats")
async def redis_stats() -> JSONResponse:
    """Surface how Redis is used BEYOND caching: vector search for intent routing,
    agent memory (runs + learned variables), and the semantic LLM cache."""
    out: dict = {
        "available": False,
        "connection": "local",
        "version": None,
        "vector_routing": {"available": False},
        "agent_memory": {"available": False},
        "semantic_cache": {"available": False},
        "last_match": None,
    }
    try:
        from config import REDIS_URL
        r = _stats_redis()
        r.ping()
        out["available"] = True
        host = REDIS_URL.split("@")[-1]
        out["connection"] = "cloud" if (
            "redislabs" in host or "redis-cloud" in host or "rediss://" in REDIS_URL
        ) else "local"
        try:
            out["version"] = r.info("server").get("redis_version")
        except Exception:
            pass

        # Vector search — routines indexed in a Redis 8 vectorset
        try:
            from router.vector_router import VSET_KEY, SIMILARITY_THRESHOLD
            from services import embeddings
            count = int(r.execute_command("VCARD", VSET_KEY) or 0)
            out["vector_routing"] = {
                "available": count > 0,
                "indexed_routines": count,
                "dim": embeddings.EMBEDDING_DIM,
                "threshold": SIMILARITY_THRESHOLD,
                "model": "BAAI/bge-small-en-v1.5",
            }
        except Exception:
            pass

        # Agent memory + semantic cache — each module owns its own key scheme.
        out["agent_memory"] = _stats_memory_handle().stats()
        out["semantic_cache"] = _stats_semcache_handle().stats()

        # Last routing decision (from the live event history)
        for ev in reversed(event_bus.get_history()):
            if ev.get("type") == "routine.resolved":
                d = ev.get("data", {})
                out["last_match"] = {
                    "routine_id": d.get("routine_id"),
                    "similarity": d.get("confidence"),
                }
                break
    except Exception as e:
        out["error"] = str(e)
    return JSONResponse(out)


@app.post("/api/mode/{mode}")
async def set_mode(mode: str) -> JSONResponse:
    mode = mode.upper()
    if mode not in ("LIVE", "LOCKED", "AUTONOMOUS"):
        return JSONResponse(
            {"error": "mode must be LIVE, LOCKED, or AUTONOMOUS"}, status_code=400
        )
    _cfg._runtime_mode = mode
    _state["mode"] = mode
    event_bus.emit("mode.changed", {"mode": mode})
    return JSONResponse({"ok": True, "mode": mode})


@app.get("/api/routines/{routine_id}/stats")
async def get_routine_stats(routine_id: str) -> JSONResponse:
    """Return per-node evolution stats for a routine (confidence, deviation counts, etc.)."""
    try:
        from engine.routines import get_routine
        from telemetry.evolution import RoutineEvolution
        r = get_routine(routine_id)
        ev = RoutineEvolution()
        return JSONResponse(ev.all_stats(routine_id, len(r.steps)))
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/task-graphs")
async def list_task_graphs() -> JSONResponse:
    """Every stored task graph (read from data/task_graphs.json), newest first.
    Lets the frontend discover and browse graphs without knowing keys up front."""
    try:
        from engine.task_graph import TaskGraphStore
        raw = TaskGraphStore().all_graphs()
        out = [
            {
                "task_key":   key,
                "routine_id": g.get("routine_id"),
                "run_count":  g.get("run_count", 0),
                "node_count": len(g.get("nodes", [])),
                "edge_count": len(g.get("edges", [])),
                "updated_at": g.get("updated_at", 0),
                "intents":    g.get("intents", []),
                "labels":     [n.get("label") for n in g.get("nodes", [])],
            }
            for key, g in raw.items()
        ]
        out.sort(key=lambda x: x["updated_at"], reverse=True)
        return JSONResponse(out)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/task-graph/{routine_id}")
async def get_task_graph(routine_id: str) -> JSONResponse:
    """The accumulated graph for a task — milestones learned across all runs."""
    try:
        from engine.task_graph import TaskGraphStore, _serialize
        graph = TaskGraphStore().load(routine_id, {})
        if graph.run_count == 0 and not graph.nodes:
            return JSONResponse({"error": "no graph yet"}, status_code=404)
        return JSONResponse(_serialize(graph))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/task-graphs/{task_key}/promote")
async def promote_task_graph(task_key: str) -> JSONResponse:
    """Promote a crystallized task graph into a dispatchable workflow.

    Called by the Command Center's 'Bake out a new workflow' toggle after a
    first-time ad-hoc run completes and the coalescer saves the graph.
    Delegates to the shared promote_graph() helper which also fires async
    LLM description generation."""
    try:
        from engine.workflow_promote import promote_graph

        wf = promote_graph(task_key)
        if wf is None:
            return JSONResponse({"error": "graph not ready"}, status_code=404)
        return JSONResponse({
            "workflow_id": wf.id, "name": wf.name,
            "version": wf.version, "node_count": len(wf.nodes),
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    try:
        from telemetry.memory import ExecutionMemory
        run = ExecutionMemory().get_run(run_id)
        if run:
            return JSONResponse(_run_full(run))
    except Exception:
        pass
    return JSONResponse({"error": "not found"}, status_code=404)


@app.get("/api/audit")
async def get_audit() -> JSONResponse:
    """Return the most recent audit log entries (hash-chained JSONL)."""
    try:
        from telemetry.audit_log import read_all
        return JSONResponse(read_all(limit=200))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/audit/verify")
async def verify_audit() -> JSONResponse:
    """Verify tamper-evidence of the entire audit log hash chain."""
    try:
        from telemetry.audit_log import verify_chain
        return JSONResponse(verify_chain())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/policy")
async def get_policy() -> JSONResponse:
    """Return the current governance policy rules (from data/policy.yaml)."""
    try:
        import yaml
        from pathlib import Path
        raw = Path("data/policy.yaml").read_text()
        return JSONResponse(yaml.safe_load(raw))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/api/agents")
async def list_agents() -> JSONResponse:
    return JSONResponse([_make_agent()])


@app.get("/api/agents/{agent_id}")
async def get_agent(agent_id: str) -> JSONResponse:
    if agent_id != "shepherd-agent":
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(_make_agent())


@app.get("/api/interventions")
async def list_interventions() -> JSONResponse:
    return JSONResponse(list(reversed(_interventions[-50:])))


@app.post("/api/interventions/{intervention_id}")
async def resolve_intervention(intervention_id: str, request: Request) -> JSONResponse:
    iv = next((x for x in _interventions if x["id"] == intervention_id), None)
    if iv is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    try:
        body = await request.json()
        resolution = body.get("resolution", "approved")
    except Exception:
        resolution = "approved"

    from engine.approvals import set_decision
    if resolution == "rejected":
        set_decision("halt")
    else:
        set_decision("approve")

    iv["status"] = "resolved"
    iv["resolution"] = resolution
    iv["resolvedBy"] = "dashboard"
    iv["resolvedAt"] = datetime.utcnow().isoformat() + "Z"
    if isinstance(body, dict) and body.get("note"):
        iv["note"] = body["note"]
    return JSONResponse(iv)


@app.post("/api/ingest")
async def ingest_event(request: Request) -> JSONResponse:
    """
    Accept an event from a separate agent process and re-emit it on this backend's
    bus → broadcast to connected dashboards + recorded in history. Lets the backend
    run as its own persistent process while agents (which set BACKEND_URL) push to it.
    Accepts {"type": str, "data": {...}} or a list of such messages.
    """
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)
    msgs = body if isinstance(body, list) else [body]
    n = 0
    for m in msgs:
        if isinstance(m, dict) and m.get("type"):
            event_bus.emit(m["type"], m.get("data") or {})
            n += 1
    return JSONResponse({"ok": True, "ingested": n})


# ── Run a goal from the frontend ────────────────────────────────────────────
# Two ways an agent gets a goal submitted here:
#   • all-in-one: the in-process agent registers its queue (register_intent_queue),
#     so POST /api/intent hands it the goal directly.
#   • separate agent: a standalone backend buffers goals in _pending_intents, and
#     agents (BACKEND_URL set) poll GET /api/intent/next to pull them. This is what
#     lets the backend+frontend run independently of agents that come and go.
import queue as _queue

_intent_queue = None
_pending_intents: "_queue.Queue[str]" = _queue.Queue()
_engine_ref = None


def register_intent_queue(q) -> None:
    global _intent_queue
    _intent_queue = q


def register_engine(engine) -> None:
    """Give the dashboard a reference to the engine for steering/suspend queries."""
    global _engine_ref
    _engine_ref = engine


@app.post("/api/intent")
async def submit_intent(request: Request) -> JSONResponse:
    """Queue a goal: hand it to an in-process agent if attached, else buffer it for
    a separately-running agent to poll."""
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
    except Exception:
        text = ""
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    if _intent_queue is not None:
        _intent_queue.put(text)
    else:
        _pending_intents.put(text)
    event_bus.emit("remote.intent", {"text": text, "source": "dashboard"})
    return JSONResponse({"ok": True, "queued": text})


@app.get("/api/intent/next")
async def next_intent() -> JSONResponse:
    """A separately-running agent polls this for goals submitted via POST /api/intent."""
    try:
        return JSONResponse({"text": _pending_intents.get_nowait()})
    except _queue.Empty:
        return JSONResponse({"text": None})


@app.post("/api/steer")
async def steer_task(request: Request) -> JSONResponse:
    """Steer a running/suspended autonomous task (all-in-one mode)."""
    if _engine_ref is None:
        return JSONResponse({"error": "no engine registered"}, status_code=503)
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
        remember = body.get("remember", True)
    except Exception:
        text, remember = "", True
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    if _engine_ref.is_suspended():
        from shepherd_types import InterventionEvent
        import time as _time

        ctx = _engine_ref._suspended_task
        ctx.goal = f"{ctx.goal}\n\n[OPERATOR STEER]: {text}"
        ctx.chain_history.append(f">>> USER INTERVENED (IMPORTANT): {text}")
        flag = "save_as_rule" if remember else "one_off"
        ctx.interventions.append(InterventionEvent(
            step_index=ctx.step_index, trigger="steer", decision="override",
            instruction=text, flag=flag,
            node_key="", scenario="operator steer (on resume)", ts=_time.time(),
        ))
        if _intent_queue is not None:
            _intent_queue.put("__RESUME__")
        event_bus.emit("remote.steer", {"text": text, "source": "dashboard", "resumed": True})
        return JSONResponse({"ok": True, "action": "resumed", "text": text})
    else:
        _engine_ref.request_steer(text, remember)
        event_bus.emit("remote.steer", {"text": text, "source": "dashboard", "resumed": False})
        return JSONResponse({"ok": True, "action": "steered", "text": text})


@app.post("/api/new_task")
async def new_task(request: Request) -> JSONResponse:
    """Halt the current task and start a new one (compound halt + intent)."""
    try:
        body = await request.json()
        text = (body.get("text") or "").strip()
    except Exception:
        text = ""
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)

    # Halt any running task
    if _engine_ref is not None:
        try:
            _engine_ref.request_halt()
        except Exception:
            pass

    # Queue the new intent
    if _intent_queue is not None:
        _intent_queue.put(text)
    else:
        _pending_intents.put(text)
    event_bus.emit("remote.intent", {"text": text, "source": "dashboard"})
    return JSONResponse({"ok": True, "action": "new_task", "text": text})


def start_dashboard() -> None:
    """Run the FastAPI backend. Used both as a daemon thread (in-process, from
    main.py) and as a standalone persistent server (python -m dashboard.server)."""
    uvicorn.run(app, host="127.0.0.1", port=DASHBOARD_PORT, log_level="warning")


if __name__ == "__main__":
    # Standalone persistent backend — survives across agent runs, serves graphs,
    # runs, replay, policy, audit from disk, and live events ingested from agents.
    print(f"[backend] Shepherd backend → http://localhost:{DASHBOARD_PORT}  (Ctrl-C to stop)")
    start_dashboard()
