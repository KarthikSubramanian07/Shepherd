"""
Orchestrator configuration — read straight from the environment so the package
stays self-contained (no edits to the central pydantic settings model).

All flags default OFF / conservative, so importing this never changes existing
single-agent behavior. The orchestrator only takes over main.py when
``ENABLE_ORCHESTRATOR`` is true.
"""
from __future__ import annotations

import os


def _bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip())
    except (ValueError, AttributeError):
        return default


# Master switch: when false, main.py runs the legacy single-agent serial loop.
ENABLE_ORCHESTRATOR = _bool("ENABLE_ORCHESTRATOR", False)

# How many agent workers may run concurrently in total.
MAX_CONCURRENT_AGENTS = _int("MAX_CONCURRENT_AGENTS", 4)

# Cap on simultaneous Browserbase cloud sessions (quota/cost guard).
MAX_BROWSERBASE_SESSIONS = _int("MAX_BROWSERBASE_SESSIONS", 3)

# Default surface kind for a dispatched task when none is specified.
DEFAULT_SURFACE_KIND = os.getenv("DEFAULT_SURFACE_KIND", "local").strip().lower()

# Containment rate limits: "per_agent" (each agent gets its own budget) or
# "global" (the policy limit is shared across all agents).
RATE_LIMIT_SCOPE = os.getenv("RATE_LIMIT_SCOPE", "per_agent").strip().lower()

# Max wall-clock a Browserbase session may sit idle before teardown (seconds).
BROWSERBASE_IDLE_TTL_S = _int("BROWSERBASE_IDLE_TTL_S", 180)
