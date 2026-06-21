"""
Orchestrator — dispatch, surface serialization, halt, snapshot. Uses injected
run_fns so no real engine / browser / model is needed.
"""
import threading
import time

from orchestrator import Orchestrator, surfaces
from orchestrator import config as orch_config


def test_dispatch_serializes_local_agents():
    """Many local agents dispatched at once never overlap on the desktop."""
    active = {"n": 0}
    overlaps = []
    lock = threading.Lock()

    def fn(worker):
        with worker.arbiter.hold(surfaces.LOCAL_DESKTOP, worker.agent_id):
            with lock:
                active["n"] += 1
                if active["n"] > 1:
                    overlaps.append(active["n"])
            time.sleep(0.01)
            with lock:
                active["n"] -= 1
        return {"status": "completed"}

    orch = Orchestrator(max_agents=8)
    for i in range(6):
        orch.dispatch(f"goal {i}", surface_kind="local", run_fn=fn)
    orch.join_all(timeout=5)

    assert overlaps == []
    snap = orch.snapshot()
    assert len(snap["agents"]) == 6
    assert all(a["status"] == "completed" for a in snap["agents"])


def test_halt_all_stops_running_agents():
    started = threading.Event()

    def fn(worker):
        started.set()
        while not worker.halted:
            time.sleep(0.01)
        return {"status": "halted"}

    orch = Orchestrator(max_agents=4)
    aid = orch.dispatch("long task", surface_kind="local", run_fn=fn)
    assert started.wait(timeout=2)

    n = orch.halt_all()
    orch.join_all(timeout=3)

    assert n >= 1
    w = orch.get(aid)
    assert w is not None and w.status == "halted"


def test_concurrency_cap_backlogs_extra_tasks():
    """Over the cap, tasks wait in the backlog and then drain."""
    gate = threading.Event()
    done = {"n": 0}
    lock = threading.Lock()

    def fn(worker):
        gate.wait(timeout=5)
        with lock:
            done["n"] += 1
        return {"status": "completed"}

    orch = Orchestrator(max_agents=2)
    for i in range(5):
        orch.dispatch(f"g{i}", surface_kind="local", run_fn=fn)

    # With a cap of 2 and 5 tasks, at most 2 run and 3 are backlogged.
    time.sleep(0.1)
    snap = orch.snapshot()
    running = [a for a in snap["agents"] if a["status"] == "running"]
    assert len(running) <= 2
    assert len(snap["backlog"]) >= 1

    gate.set()
    orch.join_all(timeout=5)
    assert done["n"] == 5


def test_shutdown_halts_and_waits_for_teardown():
    """shutdown() must halt every agent AND wait for each worker to run its
    teardown on its own thread — that's what closes browser windows cleanly."""
    torn_down = {"n": 0}
    lock = threading.Lock()

    def fn(worker):
        try:
            while not worker.halted:
                time.sleep(0.01)
        finally:
            with lock:                 # stand-in for session.close() on the worker thread
                torn_down["n"] += 1
        return {"status": "halted"}

    orch = Orchestrator(max_agents=4)
    for i in range(3):
        orch.dispatch(f"g{i}", surface_kind="local", run_fn=fn)
    time.sleep(0.1)

    orch.shutdown(timeout=3)

    assert torn_down["n"] == 3          # every worker finished its teardown
    snap = orch.snapshot()
    assert all(a["status"] == "halted" for a in snap["agents"])


def test_blocked_browserbase_task_does_not_stall_local(monkeypatch):
    """A backlogged Browserbase task that can never start (session cap reached)
    must not block runnable local tasks queued behind it (no head-of-line stall)."""
    monkeypatch.setattr(orch_config, "MAX_BROWSERBASE_SESSIONS", 0)
    done = {"local": 0}
    lock = threading.Lock()
    release = threading.Event()

    def a_fn(worker):
        release.wait(timeout=5)   # hold the single slot until we've backlogged the rest
        return {"status": "completed"}

    def local_fn(worker):
        with lock:
            done["local"] += 1
        return {"status": "completed"}

    orch = Orchestrator(max_agents=1)
    orch.dispatch("A holds the slot", surface_kind="local", run_fn=a_fn)
    # B can never start (bb cap 0) and sits at the head of the backlog; C is behind it.
    orch.dispatch("B browser (never startable)", surface_kind="browserbase", run_fn=local_fn)
    orch.dispatch("C local behind B", surface_kind="local", run_fn=local_fn)

    time.sleep(0.1)
    release.set()
    # C must run even though B is stuck ahead of it. Don't wait on B.
    deadline = time.time() + 3
    while done["local"] < 1 and time.time() < deadline:
        time.sleep(0.02)

    assert done["local"] == 1, "local task behind a blocked browserbase task never ran"
    snap = orch.snapshot()
    assert any(t["surface_kind"] == "browserbase" for t in snap["backlog"])
