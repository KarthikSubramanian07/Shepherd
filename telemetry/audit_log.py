"""
Tamper-evident audit log — hash-chain backed JSONL.

Every action the engine executes is appended here. Each entry includes the
SHA-256 hash of itself (excluding the hash field) and the hash of the previous
entry, forming a chain. Any modification to any entry breaks the chain and is
detected by verify_chain().

Format (one JSON object per line):
  {
    "seq":        <int>,          # monotonic sequence number
    "run_id":     <str>,
    "step_index": <int>,
    "action":     <str>,
    "target":     <str|null>,
    "status":     <str>,          # completed|failed|halted|flagged
    "duration_ms":<int>,
    "ts":         <float>,        # Unix timestamp
    "prev_hash":  <str>,          # hash of previous entry ("0"*64 for genesis)
    "hash":       <str>           # SHA-256 of this entry (prev_hash included, hash excluded)
  }
"""
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Optional

_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", "data/audit.jsonl"))

# Multi-agent safety: many agent workers append concurrently. The hash chain is
# a single global ledger (one tamper-evidence proof for the whole fleet), so
# appends MUST be serialized — otherwise two agents read the same prev_hash/seq
# and fork the chain. One process-wide lock guards the in-memory chain head
# (cached seq + prev_hash) so we never re-read the file per append. The lock is
# off the click path (appends happen at step boundaries), so it costs nothing.
_lock = threading.Lock()
_head_seq: Optional[int] = None      # next seq to assign; None until primed
_head_hash: str = "0" * 64           # hash of the last entry (genesis sentinel)


def _prime_head_locked() -> None:
    """Initialize the in-memory chain head from the file once (caller holds lock)."""
    global _head_seq, _head_hash
    if _head_seq is not None:
        return
    try:
        lines = _LOG_PATH.read_text().strip().splitlines()
        if lines:
            last = json.loads(lines[-1])
            _head_seq = last.get("seq", -1) + 1
            _head_hash = last["hash"]
            return
    except Exception:
        pass
    _head_seq = 0
    _head_hash = "0" * 64


def _entry_hash(entry: dict) -> str:
    """SHA-256 of the entry with the 'hash' field excluded, keys sorted."""
    payload = {k: v for k, v in entry.items() if k != "hash"}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def append(
    run_id: str,
    step_index: int,
    action: str,
    status: str,
    duration_ms: int,
    ts: float,
    target: Optional[str] = None,
    extra: Optional[dict] = None,
    agent_id: Optional[str] = None,
) -> str:
    """Append one entry to the audit log. Returns its hash.

    Thread-safe: the whole read-head → build → write → advance-head sequence runs
    under one lock so concurrent agents produce a single, totally-ordered chain.
    Each entry is tagged with ``agent_id`` so a multi-agent ledger stays
    attributable while remaining one verifiable chain.
    """
    global _head_seq, _head_hash
    with _lock:
        _prime_head_locked()
        entry: dict = {
            "seq":        _head_seq,
            "run_id":     run_id,
            "agent_id":   agent_id,
            "step_index": step_index,
            "action":     action,
            "target":     target,
            "status":     status,
            "duration_ms": duration_ms,
            "ts":         round(ts, 4),
            "prev_hash":  _head_hash,
        }
        if extra:
            entry.update(extra)
        entry["hash"] = _entry_hash(entry)
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a") as f:
                f.write(json.dumps(entry) + "\n")
            # Advance the head only after a successful write so a failed append
            # never leaves a phantom link the next entry would chain onto.
            _head_seq += 1
            _head_hash = entry["hash"]
        except Exception as e:
            print(f"[audit] write failed (non-fatal): {e}")
        return entry["hash"]


def read_all(limit: int = 500) -> list:
    """Return up to `limit` most recent audit entries (newest last)."""
    try:
        lines = _LOG_PATH.read_text().strip().splitlines()
        return [json.loads(l) for l in lines[-limit:]]
    except Exception:
        return []


def verify_chain() -> dict:
    """
    Walk the entire log and verify:
      1. Each entry's hash matches its content.
      2. Each entry's prev_hash matches the previous entry's hash.

    Returns:
      {"valid": bool, "entries": int, "tampered_at": int|None, "reason": str}
    """
    try:
        lines = _LOG_PATH.read_text().strip().splitlines()
    except FileNotFoundError:
        return {"valid": True, "entries": 0, "tampered_at": None, "reason": "no log yet"}
    except Exception as e:
        return {"valid": False, "entries": 0, "tampered_at": None, "reason": str(e)}

    entries = []
    for i, line in enumerate(lines):
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError as e:
            return {"valid": False, "entries": i, "tampered_at": i,
                    "reason": f"JSON parse error at line {i}: {e}"}

    prev = "0" * 64  # genesis
    for i, e in enumerate(entries):
        expected = _entry_hash(e)
        if e.get("hash") != expected:
            return {"valid": False, "entries": len(entries),
                    "tampered_at": i,
                    "reason": f"Hash mismatch at seq {e.get('seq', i)}"}
        if e.get("prev_hash") != prev:
            return {"valid": False, "entries": len(entries),
                    "tampered_at": i,
                    "reason": f"Chain broken at seq {e.get('seq', i)}"}
        prev = e["hash"]

    return {"valid": True, "entries": len(entries), "tampered_at": None,
            "reason": "chain intact"}
