"""
Audit log under concurrency — many agents append at once, the single hash chain
stays valid and totally ordered, every entry attributed to its agent.
"""
import pytest

from telemetry import audit_log


@pytest.fixture(autouse=True)
def _restore_audit_state():
    """Keep the audit module's global path/head from leaking to other tests."""
    orig = (audit_log._LOG_PATH, audit_log._head_seq, audit_log._head_hash)
    yield
    audit_log._LOG_PATH, audit_log._head_seq, audit_log._head_hash = orig


def _reset(path):
    audit_log._LOG_PATH = path
    audit_log._head_seq = None
    audit_log._head_hash = "0" * 64


def test_concurrent_appends_keep_one_valid_chain(tmp_path):
    import threading

    _reset(tmp_path / "audit.jsonl")

    def append_many(aid):
        for i in range(25):
            audit_log.append(
                run_id=f"run-{aid}", step_index=i, action="agent_s",
                status="completed", duration_ms=1, ts=1.0, agent_id=aid,
            )

    agents = [f"agent-{i}" for i in range(8)]
    threads = [threading.Thread(target=append_many, args=(a,)) for a in agents]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    res = audit_log.verify_chain()
    assert res["valid"], res
    assert res["entries"] == 8 * 25

    entries = audit_log.read_all(limit=1000)
    # Every entry is attributed and the chain is a total order (seq 0..N-1).
    assert all("agent_id" in e for e in entries)
    assert {e["agent_id"] for e in entries} == set(agents)
    assert [e["seq"] for e in entries] == list(range(8 * 25))


def test_tamper_is_detected(tmp_path):
    import json

    path = tmp_path / "audit.jsonl"
    _reset(path)
    for i in range(5):
        audit_log.append(run_id="r", step_index=i, action="x", status="completed",
                         duration_ms=1, ts=1.0, agent_id="a")
    assert audit_log.verify_chain()["valid"]

    # Flip a byte in the middle entry → chain must break.
    lines = path.read_text().splitlines()
    e = json.loads(lines[2])
    e["action"] = "tampered"
    lines[2] = json.dumps(e)
    path.write_text("\n".join(lines) + "\n")

    res = audit_log.verify_chain()
    assert res["valid"] is False
    assert res["tampered_at"] == 2
