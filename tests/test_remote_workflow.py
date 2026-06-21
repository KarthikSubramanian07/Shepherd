"""Integration contract for the remote workflow flow:

  • the coordinator builds a live milestone graph from the workflow.* event stream
    (status, current node, awaiting block, per-node frame timestamps), and
  • the relay routes workflow.pause/resume/intervene commands down to the
    milestone executor's control gate (engine.workflow_control).
"""
import queue

from coordinator.server import AgentConn, Hub
from engine import workflow_control
from engine.workflow_executor import WorkerTurn
from services.relay_client import RelayClient
from shepherd_types import TaskGraphNode


def _conn() -> AgentConn:
    return AgentConn(agent_id="a1", name="A1", host="box", ws=None)


def _ev(t, **data):
    return {"type": t, "data": data}


def test_coordinator_builds_workflow_graph_from_stream():
    hub = Hub()
    conn = _conn()

    hub.apply_event(conn, _ev("workflow.start", workflow_id="WF", name="Job App", start="open"))
    assert conn.status == "running"
    assert conn.workflow["id"] == "WF"

    hub.apply_event(conn, _ev(
        "workflow.node.enter", workflow_id="WF", step_no=0, node_key="open",
        label="Open form", kind="open",
        options=[{"key": "fill", "label": "Fill details", "via": "edge", "when": None}],
    ))
    assert conn.workflow["current"] == "open"
    assert conn.step_index == 0

    # a frame arrives, then the milestone completes → its frame ts is pinned
    conn.last_frame, conn.last_frame_ts = "JPEGDATA", 123.0
    hub.apply_event(conn, _ev(
        "workflow.step", workflow_id="WF", step_no=0, node_key="open",
        label="Open form", status="done", did="opened", next="fill",
        options=[{"key": "fill", "label": "Fill details", "via": "edge", "when": None}],
    ))
    snap = conn.snapshot()
    nodes = {n["key"]: n for n in snap["workflow"]["nodes"]}
    assert nodes["open"]["status"] == "done"
    assert nodes["open"]["frameTs"] == 123.0
    # the chosen next edge is recorded so the graph can draw open -> fill
    assert {"from": "open", "to": "fill", "when": None} in snap["workflow"]["edges"]


def test_coordinator_awaiting_sets_block():
    hub = Hub()
    conn = _conn()
    hub.apply_event(conn, _ev("workflow.start", workflow_id="WF", name="WF", start="fill"))
    hub.apply_event(conn, _ev(
        "workflow.awaiting", step_no=1, node_key="fill", label="Fill projects",
        options=[{"key": "research", "label": "Research", "via": "cond", "when": "empty"}],
    ))
    assert conn.status == "blocked"
    assert conn.block["workflow"] is True
    assert conn.block["nodeKey"] == "fill"
    assert conn.snapshot()["workflow"]["awaiting"] is True

    hub.apply_event(conn, _ev("workflow.done", status="completed", steps=3))
    assert conn.status == "completed"
    assert conn.block is None


def test_relay_workflow_commands_drive_control_gate():
    workflow_control.reset()
    relay = RelayClient(engine=None, remote_intents=queue.Queue())

    relay._apply_command("workflow.pause", {})
    assert workflow_control.is_paused()

    relay._apply_command("workflow.resume", {})
    assert not workflow_control.is_paused()

    relay._apply_command("workflow.intervene", {
        "instruction": "research the projects page",
        "next_key": "research", "scenario": "the projects field is empty",
        "remember": True, "target_node": "fill",
    })
    # the queued directive is consumed by the executor gate when it reaches `fill`
    turn = WorkerTurn(
        goal="g", step_no=1,
        node=TaskGraphNode(key="fill", kind="fill", label="Fill projects"),
        resolved={}, missing=[], options=[], profile={},
    )
    iv = workflow_control.review(turn)
    assert iv is not None
    assert iv.instruction == "research the projects page"
    assert iv.next == "research"
    assert iv.remember is True
    workflow_control.reset()
