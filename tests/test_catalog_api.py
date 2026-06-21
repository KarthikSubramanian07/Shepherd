"""Integration tests for the catalog relay API.

Validates:
- Agent pushes catalog → coordinator stores it
- REST endpoints return correct data
- Token auth enforcement on catalog endpoints
- Version increments on re-push
- 404 for unknown agents
- Persistence (catalog_store save/load)
"""
import asyncio
import json
import os
import tempfile
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from coordinator.server import AgentConn, Hub, app

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def hub():
    """Fresh Hub instance (no real websockets)."""
    return Hub()


@pytest.fixture
def agent_conn():
    """An AgentConn with no real websocket."""
    return AgentConn(agent_id="test-agent-1", name="TestAgent", host="localhost", ws=None)


@pytest.fixture
def sample_catalog():
    return {
        "routines": [
            {"id": "r1", "name": "Login routine", "description": "Logs into the app",
             "mode": "LIVE", "stepCount": 5, "version": 1},
            {"id": "r2", "name": "Submit form", "description": "Fills and submits",
             "mode": "LOCKED", "stepCount": 3, "version": 1},
        ],
        "workflows": [
            {"id": "w1", "name": "Apply to job", "description": "Full application flow",
             "version": 2, "intent_patterns": ["apply", "job"],
             "params": ["company", "role"], "nodes": 4, "updated_at": 1700000000.0},
        ],
        "task_graphs": [
            {"task_key": "tg1", "routine_id": "r1", "run_count": 3,
             "node_count": 6, "edge_count": 5, "updated_at": 1700000000,
             "intents": ["login"], "labels": ["Open", "Type", "Click"]},
        ],
    }


# ── Helper ────────────────────────────────────────────────────────────────────


def _run(coro):
    """Run an async function synchronously."""
    return asyncio.run(coro)


async def _get(path: str, agents: dict, token: str = "", headers: dict | None = None):
    """Make a GET request against the FastAPI app with mocked hub + token."""
    with patch("coordinator.server.hub") as mock_hub, \
         patch("coordinator.server.COORDINATOR_TOKEN", token):
        mock_hub.agents = agents
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.get(path, headers=headers or {})


# ── Unit tests: Hub + AgentConn catalog storage ──────────────────────────────


def test_catalog_stored_on_conn(agent_conn, sample_catalog):
    """Setting catalog on AgentConn stores the data and increments version."""
    assert agent_conn.catalog is None
    assert agent_conn.catalog_version == 0

    agent_conn.catalog = sample_catalog
    agent_conn.catalog_version += 1

    assert agent_conn.catalog == sample_catalog
    assert agent_conn.catalog_version == 1


def test_catalog_version_increments(agent_conn, sample_catalog):
    """Each catalog push increments the version."""
    agent_conn.catalog = sample_catalog
    agent_conn.catalog_version += 1
    assert agent_conn.catalog_version == 1

    agent_conn.catalog = sample_catalog
    agent_conn.catalog_version += 1
    assert agent_conn.catalog_version == 2


def test_catalog_in_snapshot(agent_conn, sample_catalog):
    """Snapshot doesn't expose catalog (it's served via separate endpoints)."""
    agent_conn.catalog = sample_catalog
    agent_conn.catalog_version = 1
    snap = agent_conn.snapshot()
    # catalog is NOT in the roster snapshot (it's too large to broadcast)
    assert "catalog" not in snap


# ── HTTP endpoint tests ──────────────────────────────────────────────────────


@pytest.fixture
def registered_agent(hub, agent_conn, sample_catalog):
    """Register an agent with a catalog in the hub."""
    agent_conn.catalog = sample_catalog
    agent_conn.catalog_version = 1
    hub.register_agent(agent_conn)
    return agent_conn


def test_catalog_endpoint_returns_full_catalog(registered_agent):
    """GET /api/agents/{id}/catalog returns the full catalog + version."""
    resp = _run(_get("/api/agents/test-agent-1/catalog", {"test-agent-1": registered_agent}))

    assert resp.status_code == 200
    data = resp.json()
    assert data["version"] == 1
    assert len(data["routines"]) == 2
    assert len(data["workflows"]) == 1
    assert len(data["task_graphs"]) == 1
    assert data["routines"][0]["id"] == "r1"
    assert data["workflows"][0]["name"] == "Apply to job"
    assert data["task_graphs"][0]["task_key"] == "tg1"


def test_routines_endpoint(registered_agent):
    """GET /api/agents/{id}/routines returns only routines."""
    resp = _run(_get("/api/agents/test-agent-1/routines", {"test-agent-1": registered_agent}))

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == "r1"
    assert data[1]["id"] == "r2"


def test_workflows_endpoint(registered_agent):
    """GET /api/agents/{id}/workflows returns only workflows."""
    resp = _run(_get("/api/agents/test-agent-1/workflows", {"test-agent-1": registered_agent}))

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "w1"
    assert data[0]["nodes"] == 4


def test_task_graphs_endpoint(registered_agent):
    """GET /api/agents/{id}/task-graphs returns only task graphs."""
    resp = _run(_get("/api/agents/test-agent-1/task-graphs", {"test-agent-1": registered_agent}))

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["task_key"] == "tg1"
    assert data[0]["run_count"] == 3


def test_catalog_404_for_unknown_agent():
    """GET /api/agents/{id}/catalog returns 404 for unknown agent."""
    resp = _run(_get("/api/agents/nonexistent/catalog", {}))

    assert resp.status_code == 404
    assert resp.json()["error"] == "agent not found"


def test_catalog_empty_when_no_push():
    """GET /api/agents/{id}/catalog returns empty lists when agent has no catalog."""
    conn = AgentConn(agent_id="empty-agent", name="Empty", host="box", ws=None)
    resp = _run(_get("/api/agents/empty-agent/catalog", {"empty-agent": conn}))

    assert resp.status_code == 200
    data = resp.json()
    assert data["routines"] == []
    assert data["workflows"] == []
    assert data["task_graphs"] == []
    assert data["version"] == 0


def test_catalog_token_auth_enforced(registered_agent):
    """Catalog endpoints return 401 when COORDINATOR_TOKEN is set and no token provided."""
    agents = {"test-agent-1": registered_agent}

    # No token → 401
    resp = _run(_get("/api/agents/test-agent-1/catalog", agents, token="secret123"))
    assert resp.status_code == 401

    # Wrong token → 401
    resp = _run(_get("/api/agents/test-agent-1/catalog?token=wrong", agents, token="secret123"))
    assert resp.status_code == 401

    # Correct token via query param → 200
    resp = _run(_get("/api/agents/test-agent-1/catalog?token=secret123", agents, token="secret123"))
    assert resp.status_code == 200

    # Correct token via Bearer header → 200
    async def _bearer_test():
        return await _get(
            "/api/agents/test-agent-1/catalog", agents,
            token="secret123",
            headers={"Authorization": "Bearer secret123"},
        )
    resp = _run(_bearer_test())
    assert resp.status_code == 200


def test_token_auth_on_sub_endpoints(registered_agent):
    """Token auth is enforced on /routines, /workflows, /task-graphs too."""
    agents = {"test-agent-1": registered_agent}

    for path in ["/routines", "/workflows", "/task-graphs"]:
        # No token → 401
        resp = _run(_get(f"/api/agents/test-agent-1{path}", agents, token="tok"))
        assert resp.status_code == 401, f"{path} should require auth"

        # With token → 200
        resp = _run(_get(f"/api/agents/test-agent-1{path}?token=tok", agents, token="tok"))
        assert resp.status_code == 200, f"{path} should allow with token"


def test_catalog_version_in_response(registered_agent, sample_catalog):
    """Version number in response reflects the push count."""
    registered_agent.catalog_version = 5
    resp = _run(_get("/api/agents/test-agent-1/catalog", {"test-agent-1": registered_agent}))
    data = resp.json()
    assert data["version"] == 5


# ── Persistence tests (catalog_store) ────────────────────────────────────────


def test_catalog_store_save_and_load(sample_catalog):
    """save_catalog persists to disk, load_catalog retrieves it."""
    from pathlib import Path

    from coordinator.catalog_store import load_catalog, load_catalog_version, save_catalog

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        with patch("coordinator.catalog_store._STORE_PATH", Path(tmp_path)):
            save_catalog("agent-x", sample_catalog, 3)

            loaded = load_catalog("agent-x")
            assert loaded == sample_catalog

            version = load_catalog_version("agent-x")
            assert version == 3
    finally:
        os.unlink(tmp_path)


def test_catalog_store_unknown_agent():
    """load_catalog returns None for unknown agent."""
    from pathlib import Path

    from coordinator.catalog_store import load_catalog, load_catalog_version

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        with patch("coordinator.catalog_store._STORE_PATH", Path(tmp_path)):
            assert load_catalog("no-such-agent") is None
            assert load_catalog_version("no-such-agent") == 0
    finally:
        os.unlink(tmp_path)


def test_catalog_store_overwrites_on_update(sample_catalog):
    """A second save for the same agent overwrites the previous entry."""
    from pathlib import Path

    from coordinator.catalog_store import load_catalog, load_catalog_version, save_catalog

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        with patch("coordinator.catalog_store._STORE_PATH", Path(tmp_path)):
            save_catalog("agent-x", sample_catalog, 1)
            updated = {**sample_catalog, "routines": []}
            save_catalog("agent-x", updated, 2)

            loaded = load_catalog("agent-x")
            assert loaded["routines"] == []
            assert load_catalog_version("agent-x") == 2
    finally:
        os.unlink(tmp_path)


def test_catalog_store_multiple_agents(sample_catalog):
    """Multiple agents can store catalogs independently."""
    from pathlib import Path

    from coordinator.catalog_store import load_catalog, save_catalog

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        with patch("coordinator.catalog_store._STORE_PATH", Path(tmp_path)):
            save_catalog("agent-a", sample_catalog, 1)
            save_catalog("agent-b", {"routines": [], "workflows": [], "task_graphs": []}, 1)

            a = load_catalog("agent-a")
            b = load_catalog("agent-b")
            assert len(a["routines"]) == 2
            assert len(b["routines"]) == 0
    finally:
        os.unlink(tmp_path)


def test_catalog_restored_on_register(sample_catalog):
    """Hub.register_agent restores persisted catalog if agent has none."""
    from pathlib import Path

    from coordinator.catalog_store import save_catalog

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        tmp_path = f.name

    try:
        with patch("coordinator.catalog_store._STORE_PATH", Path(tmp_path)):
            # Pre-persist a catalog
            save_catalog("agent-z", sample_catalog, 7)

        # Now register an agent with that ID — it should load the cached catalog
        conn = AgentConn(agent_id="agent-z", name="Z", host="box", ws=None)
        assert conn.catalog is None

        with patch("coordinator.catalog_store._STORE_PATH", Path(tmp_path)):
            hub = Hub()
            hub.register_agent(conn)

        assert conn.catalog == sample_catalog
        assert conn.catalog_version == 7
    finally:
        os.unlink(tmp_path)
