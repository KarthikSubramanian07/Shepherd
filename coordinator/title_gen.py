"""
Best-effort async title generation for fleet session summaries.

Generates a short human-readable label (e.g. "Applying to Acme SWE role") from
the run's goal or first step description. Called at most ONCE per run; the result
is cached on the AgentConn. Uses the existing engine/llm layer and never blocks
event processing (runs in a thread executor). Falls back to a truncated goal
string if the LLM call fails or is rate-limited.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coordinator.server import AgentConn

log = logging.getLogger(__name__)

_MAX_TITLE_LEN = 60
_FALLBACK_TRUNC = 50

_SYSTEM_PROMPT = (
    "You generate extremely short titles (5-8 words max) summarizing what a "
    "desktop automation agent is working on. Output ONLY the title, no quotes, "
    "no explanation. Examples: 'Applying to Acme SWE role', 'Filing TPS report "
    "in SAP', 'Booking flight to NYC'."
)


def _truncate_goal(goal: str) -> str:
    """Produce a fallback title by truncating the raw goal string."""
    goal = goal.strip()
    if len(goal) <= _FALLBACK_TRUNC:
        return goal
    return goal[:_FALLBACK_TRUNC].rsplit(" ", 1)[0] + "…"


def _generate_title_sync(goal: str) -> str:
    """Synchronous LLM call for title generation. Runs in a thread."""
    try:
        from engine import llm
        if not llm.available():
            return _truncate_goal(goal)
        result = llm.complete(
            system=_SYSTEM_PROMPT,
            messages=[("user", f"Agent goal: {goal}")],
            max_tokens=30,
            timeout=15.0,
        )
        title = result.strip().strip('"').strip("'")
        if not title:
            return _truncate_goal(goal)
        if len(title) > _MAX_TITLE_LEN:
            title = title[:_MAX_TITLE_LEN].rsplit(" ", 1)[0] + "…"
        return title
    except Exception as exc:
        log.debug("title generation failed (best-effort): %s", exc)
        return _truncate_goal(goal)


def generate_title_async(conn: "AgentConn", goal: str) -> None:
    """Fire-and-forget title generation. Writes result to conn.title."""
    loop = asyncio.get_event_loop()

    async def _run() -> None:
        try:
            title = await loop.run_in_executor(None, _generate_title_sync, goal)
            conn.title = title
        except Exception:
            conn.title = _truncate_goal(goal)

    asyncio.ensure_future(_run())
