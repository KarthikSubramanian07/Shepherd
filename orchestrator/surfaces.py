"""
Surface model — a *serialization domain* for actions.

The whole multi-agent design turns on one distinction:

  - The LOCAL desktop is ONE shared surface. pyautogui drives a single global
    cursor/keyboard, so only one agent may actuate it at a time. All local
    Agent S agents share the single ``LOCAL_DESKTOP`` surface and are therefore
    serialized by the arbiter.
  - Each Browserbase cloud session is its OWN surface. Sessions are isolated
    (own page, own browser), so different sessions actuate in parallel — each
    ``BROWSERBASE:<session_id>`` is a distinct serialization domain.

A "surface" is just a string key the :class:`~orchestrator.arbiter.ActionArbiter`
locks on. Same key → serialized; different keys → parallel.
"""
from __future__ import annotations

LOCAL_DESKTOP = "LOCAL_DESKTOP"

_BROWSERBASE_PREFIX = "BROWSERBASE:"

# Surface *kinds* a task can request (the orchestrator resolves a kind to a
# concrete surface key — LOCAL maps to the one desktop; BROWSERBASE allocates a
# fresh session surface per agent).
KIND_LOCAL = "local"
KIND_BROWSERBASE = "browserbase"
VALID_KINDS = (KIND_LOCAL, KIND_BROWSERBASE)


def browserbase_surface(session_id: str) -> str:
    """The surface key for a specific Browserbase session."""
    return f"{_BROWSERBASE_PREFIX}{session_id}"


def is_browserbase(surface: str) -> bool:
    return surface.startswith(_BROWSERBASE_PREFIX)


def is_local(surface: str) -> bool:
    return surface == LOCAL_DESKTOP


def session_id_of(surface: str) -> str | None:
    """Extract the Browserbase session id from a surface key, or None."""
    if is_browserbase(surface):
        return surface[len(_BROWSERBASE_PREFIX):]
    return None


def kind_of(surface: str) -> str:
    return KIND_BROWSERBASE if is_browserbase(surface) else KIND_LOCAL
