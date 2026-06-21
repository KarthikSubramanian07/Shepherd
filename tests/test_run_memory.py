"""
Cross-run memory — recall picks the most-similar SUCCESSFUL run above threshold,
and degrades to no-op without Redis/embedder. The VSIM math is Redis's; we test
the parsing/threshold/status glue with a canned similarity result.
"""
import json

from services import run_memory


class _FakeRedis:
    def __init__(self):
        self.kv = {}
        self.vsim = []   # list of (elem, score) the fake VSIM returns, best-first

    def execute_command(self, cmd, *args):
        if cmd == "VSIM":
            out = []
            for elem, score in self.vsim:
                out += [elem, score]
            return out
        if cmd == "VCARD":
            return len([k for k in self.kv if not k.endswith(":val") and ":val:" not in k])
        return None

    def get(self, k):
        return self.kv.get(k)

    def set(self, k, v):
        self.kv[k] = v

    def ping(self):
        return True


def _setup(monkeypatch, vsim, vals):
    fake = _FakeRedis()
    fake.vsim = vsim
    for elem, rec in vals.items():
        fake.kv[f"shepherd:runmem:val:{elem}"] = json.dumps(rec)
    monkeypatch.setattr(run_memory, "_redis", lambda: fake)
    monkeypatch.setattr(run_memory.embeddings, "available", lambda: True)
    monkeypatch.setattr(run_memory.embeddings, "embed_bytes", lambda t: b"\x00" * 4)
    return fake


def test_recall_returns_best_completed_above_threshold(monkeypatch):
    _setup(
        monkeypatch,
        vsim=[("e1", 0.91), ("e2", 0.70)],
        vals={
            "e1": {"run_id": "r1", "goal": "apply to acme", "milestones": ["a", "b", "c"], "status": "completed"},
            "e2": {"run_id": "r2", "goal": "other", "milestones": ["x"], "status": "completed"},
        },
    )
    rec = run_memory.recall("submit my acme application")
    assert rec and rec["run_id"] == "r1" and rec["similarity"] == 0.91
    assert rec["milestones"] == ["a", "b", "c"]


def test_recall_skips_unsuccessful_and_below_threshold(monkeypatch):
    _setup(
        monkeypatch,
        vsim=[("e1", 0.95), ("e2", 0.88)],
        vals={
            "e1": {"run_id": "r1", "goal": "g", "milestones": ["a"], "status": "halted"},      # not completed
            "e2": {"run_id": "r2", "goal": "g", "milestones": ["a", "b"], "status": "completed"},
        },
    )
    rec = run_memory.recall("g")
    assert rec and rec["run_id"] == "r2"   # skipped the halted higher-similarity one

    # Nothing above threshold -> None.
    _setup(monkeypatch, vsim=[("e1", 0.50)],
           vals={"e1": {"run_id": "r1", "goal": "g", "milestones": ["a"], "status": "completed"}})
    assert run_memory.recall("g") is None


def test_no_redis_is_noop(monkeypatch):
    monkeypatch.setattr(run_memory, "_redis", lambda: None)
    assert run_memory.available() is False
    assert run_memory.recall("anything") is None
    run_memory.index_run("g", ["a"], "completed", "r1")   # must not raise
