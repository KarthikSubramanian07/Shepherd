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
from pathlib import Path
from typing import Optional

_LOG_PATH = Path(os.getenv("AUDIT_LOG_PATH", "data/audit.jsonl"))


def _prev_hash() -> str:
    try:
        lines = _LOG_PATH.read_text().strip().splitlines()
        if lines:
            return json.loads(lines[-1])["hash"]
    except Exception:
        pass
    return "0" * 64  # genesis sentinel


def _seq() -> int:
    try:
        lines = _LOG_PATH.read_text().strip().splitlines()
        if lines:
            return json.loads(lines[-1]).get("seq", -1) + 1
    except Exception:
        pass
    return 0


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
) -> str:
    """Append one entry to the audit log. Returns its hash."""
    entry: dict = {
        "seq":        _seq(),
        "run_id":     run_id,
        "step_index": step_index,
        "action":     action,
        "target":     target,
        "status":     status,
        "duration_ms": duration_ms,
        "ts":         round(ts, 4),
        "prev_hash":  _prev_hash(),
    }
    if extra:
        entry.update(extra)
    entry["hash"] = _entry_hash(entry)
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
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
