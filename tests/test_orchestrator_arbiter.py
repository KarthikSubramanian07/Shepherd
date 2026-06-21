"""
ActionArbiter — the multi-agent action queue. These tests prove the core
safety invariants without any desktop / model / network.
"""
import threading
import time

from orchestrator import surfaces
from orchestrator.arbiter import ActionArbiter, LeaseCancelled, PRIORITY_HALT

LOCAL = surfaces.LOCAL_DESKTOP


def test_local_surface_is_mutually_exclusive():
    """No two agents ever hold the LOCAL desktop at the same time."""
    arb = ActionArbiter(record_timeline=True)
    active = {"n": 0}
    overlaps = []
    lock = threading.Lock()

    def work(aid):
        with arb.hold(LOCAL, aid):
            with lock:
                active["n"] += 1
                if active["n"] > 1:
                    overlaps.append(active["n"])
            time.sleep(0.01)
            with lock:
                active["n"] -= 1

    threads = [threading.Thread(target=work, args=(f"a{i}",)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlaps == [], f"agents overlapped on the desktop: {overlaps}"
    # Recorded lease spans must not overlap in time either.
    spans = sorted(arb.timeline(), key=lambda s: s.acquired_at)
    assert len(spans) == 10
    for a, b in zip(spans, spans[1:]):
        assert a.released_at <= b.acquired_at + 1e-6


def test_distinct_surfaces_run_in_parallel():
    """Two Browserbase sessions can be held simultaneously (no cross-blocking)."""
    arb = ActionArbiter()
    s1 = surfaces.browserbase_surface("one")
    s2 = surfaces.browserbase_surface("two")
    l1 = arb.acquire(s1, "a", timeout=1)
    l2 = arb.acquire(s2, "b", timeout=1)  # must not block on l1
    assert l1 is not None and l2 is not None
    arb.release(l1)
    arb.release(l2)


def test_halt_priority_jumps_the_queue():
    """A PRIORITY_HALT waiter is served before earlier normal waiters."""
    arb = ActionArbiter()
    holder = arb.acquire(LOCAL, "holder")
    got = []

    def w(aid, prio):
        lease = arb.acquire(LOCAL, aid, priority=prio)
        if lease:
            got.append(aid)
            arb.release(lease)

    n = threading.Thread(target=w, args=("normal", 0))
    n.start()
    time.sleep(0.05)  # ensure 'normal' is queued first
    h = threading.Thread(target=w, args=("halt", PRIORITY_HALT))
    h.start()
    time.sleep(0.05)  # ensure 'halt' is queued
    arb.release(holder)
    n.join(timeout=2)
    h.join(timeout=2)

    assert got and got[0] == "halt", f"expected halt first, got {got}"


def test_cancel_wakes_a_blocked_acquire():
    """Halting an agent unblocks its pending acquire (returns None)."""
    arb = ActionArbiter()
    holder = arb.acquire(LOCAL, "holder")
    result = {}

    def waiter():
        result["lease"] = arb.acquire(LOCAL, "waiter")

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    n = arb.cancel("waiter")
    t.join(timeout=2)

    assert not t.is_alive()
    assert n == 1
    assert result["lease"] is None
    arb.release(holder)


def test_hold_raises_lease_cancelled():
    """The hold() context manager raises so the action is skipped, not run unguarded."""
    arb = ActionArbiter()
    holder = arb.acquire(LOCAL, "holder")
    flags = {}

    def waiter():
        try:
            with arb.hold(LOCAL, "w"):
                flags["ran"] = True
        except LeaseCancelled:
            flags["cancelled"] = True

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)
    arb.cancel("w")
    t.join(timeout=2)

    assert flags.get("cancelled") is True
    assert "ran" not in flags
    arb.release(holder)


def test_snapshot_reports_holder_and_waiters():
    arb = ActionArbiter()
    holder = arb.acquire(LOCAL, "holder")

    def waiter():
        lease = arb.acquire(LOCAL, "w1", timeout=1)
        if lease:
            arb.release(lease)

    t = threading.Thread(target=waiter)
    t.start()
    time.sleep(0.05)

    snap = {s["surface"]: s for s in arb.snapshot()}
    assert LOCAL in snap
    assert snap[LOCAL]["holder"] == "holder"
    assert any(w["agent_id"] == "w1" for w in snap[LOCAL]["waiters"])

    arb.release(holder)
    t.join(timeout=2)
