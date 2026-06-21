"""Integration contract for the remote workflow flow:

  • the coordinator builds a live milestone graph from the workflow.* event stream
    (status, current node, awaiting block, per-node frame timestamps), and
  • the relay routes workflow.pause/resume/intervene commands down to the
    milestone executor's control gate (engine.workflow_control).
"""
import queue
import threading
import time

from coordinator.server import AgentConn, Hub
from engine import workflow_control
from engine.workflow_executor import WorkerTurn
from services.relay_client import RelayClient
from shepherd_types import TaskGraphNode, Workflow


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


# ── end-of-run persist gate (Phase 2) ─────────────────────────────────────────
def test_coordinator_tracks_finalize_gate():
    hub = Hub()
    conn = _conn()
    hub.apply_event(conn, _ev("workflow.start", workflow_id="WF", name="WF", start="fill"))
    hub.apply_event(conn, _ev("workflow.done", status="completed", steps=3))

    hub.apply_event(conn, _ev(
        "workflow.finalize", workflow_id="WF", name="WF",
        current_version=1, proposed_version=2,
        ops=[{"op": "add_conditional", "node": "fill", "when": "empty", "do": "research"}],
    ))
    fin = conn.snapshot()["workflow"]["finalize"]
    assert fin is not None
    assert fin["proposed_version"] == 2
    assert len(fin["ops"]) == 1

    hub.apply_event(conn, _ev("workflow.finalized", action="persisted",
                              workflow_id="WF", version=2))
    snap = conn.snapshot()["workflow"]
    assert snap["finalize"] is None
    assert snap["finalized"] == {"action": "persisted", "workflow_id": "WF", "version": 2}


def test_coordinator_tracks_dispatch_routing():
    """An ad-hoc dispatch's routing decision is surfaced for the operator."""
    hub = Hub()
    conn = _conn()

    hub.apply_event(conn, _ev("intent.received", raw_text="apply to the job", source="command-center"))
    assert conn.snapshot()["routing"]["state"] == "routing"

    hub.apply_event(conn, _ev(
        "plan.resolved", kind="WORKFLOW", target="WF_LIVE_JOB_APPLICATION",
        confidence=0.83, source="vector", matched=["apply", "job"],
    ))
    r = conn.snapshot()["routing"]
    assert r["state"] == "matched"
    assert r["kind"] == "WORKFLOW" and r["target"] == "WF_LIVE_JOB_APPLICATION"
    assert r["confidence"] == 0.83

    # a later unmatched intent falls back to autonomous
    hub.apply_event(conn, _ev("intent.autonomous_fallback", raw_text="do something new"))
    assert conn.snapshot()["routing"]["state"] == "autonomous"

    # the routing banner is per-run: it clears when the run finishes so it
    # doesn't linger as stale until the next dispatch re-sets it.
    hub.apply_event(conn, _ev("workflow.done", status="completed", steps=3))
    assert conn.snapshot()["routing"] is None


def test_relay_finalize_command_resolves_gate():
    """The Command Center's workflow.finalize command unblocks await_finalize."""
    workflow_control.reset()
    relay = RelayClient(engine=None, remote_intents=queue.Queue())
    wf = Workflow(id="WF", name="WF", version=1)

    out: dict = {}

    def runner():
        out["decision"] = workflow_control.await_finalize(wf, [{"op": "x"}])

    th = threading.Thread(target=runner)
    th.start()
    time.sleep(0.1)  # let the gate start waiting
    relay._apply_command("workflow.finalize", {"decision": "discard"})
    th.join(timeout=5)

    assert out["decision"]["decision"] == "discard"
    workflow_control.reset()


def test_persist_baked_applies_decision(monkeypatch):
    saved: list[Workflow] = []
    monkeypatch.setattr(
        "engine.workflow_store.WorkflowStore.save", lambda self, w: saved.append(w)
    )

    # persist → reference workflow version bumps + saved
    wf = Workflow(id="WF", name="WF", version=1)
    out = workflow_control.persist_baked(wf, [{"op": "x"}], {"decision": "persist"})
    assert out["action"] == "persisted" and wf.version == 2
    assert saved and saved[-1].id == "WF"

    # save_as_new → reference untouched, a fresh v1 clone saved under new id
    saved.clear()
    wf2 = Workflow(id="WF", name="WF", version=2)
    out = workflow_control.persist_baked(
        wf2, [], {"decision": "save_as_new", "new_id": "WF_COPY", "name": "Copy"}
    )
    assert out["action"] == "saved_as_new" and out["workflow_id"] == "WF_COPY"
    assert wf2.version == 2  # reference not bumped
    assert saved and saved[-1].id == "WF_COPY" and saved[-1].version == 1

    # discard → nothing saved, version unchanged
    saved.clear()
    wf3 = Workflow(id="WF", name="WF", version=5)
    out = workflow_control.persist_baked(wf3, [], {"decision": "discard"})
    assert out["action"] == "discarded" and wf3.version == 5
    assert saved == []
