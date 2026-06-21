"""
Operator-side catalog persistence.

Saves the catalog each connected agent pushes so it survives coordinator
restarts and is available immediately when the UI connects (before any agent
reconnects). Stored as a single JSON file keyed by agent_id.

Format:
  { "<agent_id>": { "catalog": {...}, "version": N, "updated_at": "..." }, ... }
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_STORE_PATH = Path(os.environ.get("CATALOG_STORE_PATH", "data/catalog_cache.json"))


def _read() -> dict:
    if not _STORE_PATH.exists():
        return {}
    try:
        return json.loads(_STORE_PATH.read_text())
    except Exception:
        return {}


def _write(data: dict) -> None:
    _STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(_STORE_PATH)


def save_catalog(agent_id: str, catalog: dict, version: int) -> None:
    """Persist a catalog push from an agent."""
    store = _read()
    store[agent_id] = {
        "catalog": catalog,
        "version": version,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _write(store)


def load_catalog(agent_id: str) -> Optional[dict]:
    """Load a previously persisted catalog for an agent (or None)."""
    store = _read()
    entry = store.get(agent_id)
    if not entry:
        return None
    return entry.get("catalog")


def load_catalog_version(agent_id: str) -> int:
    """Return the last persisted version number for an agent."""
    store = _read()
    entry = store.get(agent_id)
    if not entry:
        return 0
    return entry.get("version", 0)


def load_all() -> dict:
    """Load the entire catalog store (all agents)."""
    return _read()
