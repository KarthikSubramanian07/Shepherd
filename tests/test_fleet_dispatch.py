"""Fleet REST: batch deploy + guards. Uses a fake orchestrator so no real
agent workers spawn."""
import asyncio

from httpx import ASGITransport, AsyncClient

from dashboard import server


class _FakeOrch:
    def __init__(self):
        self.calls = []

    def dispatch(self, goal, surface_kind="", name=""):
        if surface_kind == "bad":
            raise ValueError("unknown surface kind: 'bad'")
        self.calls.append((goal, surface_kind, name))
        return f"agent-{len(self.calls):03d}"

    def snapshot(self):
        return {"agents": [], "backlog": [], "queue": []}


def _post(path, json):
    async def go():
        transport = ASGITransport(app=server.app)
        async with AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.post(path, json=json)
            return r.status_code, r.json()
    return asyncio.run(go())


def test_batch_deploy_dispatches_all_tasks():
    orch = _FakeOrch()
    server.register_orchestrator(orch)
    try:
        status, body = _post("/api/fleet/dispatch_batch", {"tasks": [
            {"goal": "weather in Tokyo", "surface_kind": "browserbase"},
            {"goal": "open TextEdit", "surface_kind": "local"},
            {"goal": "search arxiv", "surface_kind": "browserbase"},
        ]})
        assert status == 200
        assert body["ok"] is True
        assert len(body["agent_ids"]) == 3
        assert orch.calls[0] == ("weather in Tokyo", "browserbase", "")
        assert orch.calls[1][1] == "local"
    finally:
        server.register_orchestrator(None)


def test_batch_reports_per_task_errors_without_failing_others():
    orch = _FakeOrch()
    server.register_orchestrator(orch)
    try:
        status, body = _post("/api/fleet/dispatch_batch", {"tasks": [
            {"goal": "good one", "surface_kind": "browserbase"},
            {"goal": "", "surface_kind": "local"},          # missing goal
            {"goal": "bad kind", "surface_kind": "bad"},     # raises ValueError
        ]})
        assert status == 200
        assert body["ok"] is False
        assert len(body["agent_ids"]) == 1           # only the good one
        assert len(body["errors"]) == 2
    finally:
        server.register_orchestrator(None)


def test_batch_requires_nonempty_list_and_running_orchestrator():
    server.register_orchestrator(_FakeOrch())
    try:
        status, _ = _post("/api/fleet/dispatch_batch", {"tasks": []})
        assert status == 400
    finally:
        server.register_orchestrator(None)

    # No orchestrator → 409.
    status, _ = _post("/api/fleet/dispatch_batch", {"tasks": [{"goal": "x"}]})
    assert status == 409
