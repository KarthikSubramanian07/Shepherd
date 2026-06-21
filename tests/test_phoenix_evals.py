"""
Phoenix eval feedback — promotion threshold + graceful degradation.

The judge runs an LLM call (not unit-tested here); we test the deterministic
glue: a step the judge has net-called a real risk enough times is promoted into
the monitored set, and everything no-ops without a key/Redis.
"""
from services import phoenix_evals


class _FakeRedis:
    def __init__(self, store=None):
        self.store = store or {}

    def scan_iter(self, match="*"):
        pre = match.rstrip("*")
        return [k for k in self.store if k.startswith(pre)]

    def get(self, k):
        return self.store.get(k)

    def incrby(self, k, n):
        self.store[k] = str(int(self.store.get(k, 0)) + n)

    def ping(self):
        return True


def test_promoted_steps_threshold(monkeypatch):
    fake = _FakeRedis({
        "shepherd:eval:ROUTINE_X:2": "3",   # >= 2 -> promoted
        "shepherd:eval:ROUTINE_X:5": "1",   # below threshold
        "shepherd:eval:ROUTINE_X:7": "-2",  # judged false-alarm -> not promoted
    })
    monkeypatch.setattr(phoenix_evals, "_redis", lambda: fake)
    promoted = phoenix_evals.promoted_steps("ROUTINE_X")
    assert promoted == {2}


def test_record_signal_accumulates(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(phoenix_evals, "_redis", lambda: fake)
    phoenix_evals._record_signal("R", 1, +1)
    phoenix_evals._record_signal("R", 1, +1)
    assert phoenix_evals.promoted_steps("R") == {1}   # two real-risk judgements


def test_no_redis_is_empty(monkeypatch):
    monkeypatch.setattr(phoenix_evals, "_redis", lambda: None)
    assert phoenix_evals.promoted_steps("R") == set()


def test_score_verdict_noop_without_key(monkeypatch):
    monkeypatch.setattr(phoenix_evals, "available", lambda: False)
    assert phoenix_evals.score_verdict("credentials", "halt") is None


def test_score_plan_noop_without_key(monkeypatch):
    monkeypatch.setattr(phoenix_evals, "available", lambda: False)
    assert phoenix_evals.score_plan("apply to a job", [{"action": "open"}]) is None


def test_score_plan_uses_judge(monkeypatch):
    monkeypatch.setattr(phoenix_evals, "available", lambda: True)
    monkeypatch.setattr(phoenix_evals, "_judge",
                        lambda p: {"sound": True, "score": 0.9, "explanation": "fine"})
    out = phoenix_evals.score_plan("g", [{"action": "open"}, {"action": "type"}])
    assert out["sound"] is True and out["score"] == 0.9
