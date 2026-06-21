"""
Durable run ledger — crash-resume semantics + graceful degradation.

A run that completes cleanly must NOT be resumed; a run orphaned mid-flight (the
process died while "running") MUST be detected and offered for re-dispatch.
"""
from services import agentspan_durable as dur


class _FakeRedis:
    """Minimal in-memory stand-in: get/set/scan_iter/ping."""
    def __init__(self):
        self.store = {}

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def scan_iter(self, match="*"):
        prefix = match.rstrip("*")
        return [k for k in list(self.store) if k.startswith(prefix)]

    def ping(self):
        return True


def _patch(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(dur, "_redis", lambda: fake)
    return fake


def test_no_redis_is_a_noop(monkeypatch):
    monkeypatch.setattr(dur, "_redis", lambda: None)
    assert dur.available() is False
    dur._begin("r1", "goal", 5)          # must not raise
    dur._checkpoint("r1", 2)
    dur._finish("r1", "completed")
    assert dur.resume_incomplete() == []


def test_completed_run_is_not_resumed(monkeypatch):
    _patch(monkeypatch)
    dur._begin("r1", "apply to job", total=4)
    dur._checkpoint("r1", 0)
    dur._checkpoint("r1", 3)             # all milestones done
    dur._finish("r1", "completed")
    assert dur.resume_incomplete() == []


def test_orphaned_run_is_detected_and_resumable(monkeypatch):
    _patch(monkeypatch)
    dur._begin("r2", "send the email", total=7)
    dur._checkpoint("r2", 0)
    dur._checkpoint("r2", 3)             # crashed here — never finished
    out = dur.resume_incomplete()
    assert len(out) == 1
    led = out[0]
    assert led["run_id"] == "r2"
    assert led["goal"] == "send the email"
    assert led["done"] == 4 and led["total"] == 7
    # Re-scanning must not return it twice (status flipped to "resuming").
    assert dur.resume_incomplete() == []


def test_checkpoint_is_monotonic(monkeypatch):
    fake = _patch(monkeypatch)
    dur._begin("r3", "g", total=5)
    dur._checkpoint("r3", 4)
    dur._checkpoint("r3", 1)             # out-of-order/late event must not regress
    import json
    assert json.loads(fake.get("shepherd:durable:r3"))["done"] == 5
