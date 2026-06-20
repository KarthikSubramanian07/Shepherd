"""
Dashboard WebSocket server — serves the Control Hub UI and streams live events.
Lane B owns the UI (dashboard/static/index.html).
This file: routing, WebSocket broadcast, replay API, static serving.
"""
import asyncio
import json
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from config import DASHBOARD_PORT
from dashboard.events import event_bus
from dashboard.deepgram_routes import router as deepgram_router

app = FastAPI(title="Shepherd Control Hub", docs_url=None, redoc_url=None)
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
    async with _ws_lock:
        _sockets.append(ws)
    # Replay event history so late-joining clients catch up
    for event in event_bus.get_history():
        try:
            await ws.send_text(json.dumps(event))
        except Exception:
            break
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
        return JSONResponse(ExecutionMemory().recent(20))
    except Exception:
        return JSONResponse([])


@app.get("/api/routines/{routine_id}")
async def get_routine_info(routine_id: str) -> JSONResponse:
    try:
        from engine.routines import get_routine
        r = get_routine(routine_id)
        return JSONResponse({
            "routine_id":       r.routine_id,
            "description":      r.description,
            "mode":             r.mode,
            "high_stakes_steps": r.high_stakes_steps,
            "steps": [
                {
                    "index":       i,
                    "action":      s.action,
                    "description": s.description or s.action,
                    "high_stakes": i in r.high_stakes_steps,
                }
                for i, s in enumerate(r.steps)
            ],
        })
    except KeyError:
        return JSONResponse({"error": "not found"}, status_code=404)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/api/control/{decision}")
async def control_decision(decision: str) -> JSONResponse:
    """
    Receive a human intervention decision from the dashboard.
    decision: "approve" | "halt" | "override"
    For override, pass {"instruction": "..."} in the JSON body.
    """
    from engine.approvals import set_decision
    if decision not in ("approve", "halt", "override"):
        return JSONResponse({"error": "invalid decision"}, status_code=400)
    set_decision(decision)
    return JSONResponse({"ok": True, "decision": decision})


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


@app.get("/api/runs/{run_id}")
async def get_run(run_id: str) -> JSONResponse:
    try:
        from telemetry.memory import ExecutionMemory
        run = ExecutionMemory().get_run(run_id)
        if run:
            return JSONResponse(run)
    except Exception:
        pass
    return JSONResponse({"error": "not found"}, status_code=404)


def start_dashboard() -> None:
    """Called from main.py as a daemon thread."""
    uvicorn.run(app, host="127.0.0.1", port=DASHBOARD_PORT, log_level="warning")
