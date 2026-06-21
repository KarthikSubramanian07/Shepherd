"""
Fleet session title generation.

By default (TITLE_GEN_LLM=false), the title is simply the raw intent/goal text
truncated to display length — instant, zero-cost, and rate-limit-proof. When
TITLE_GEN_LLM is enabled, an LLM rewrites the goal into a short polished label
(e.g. "Applying to Acme SWE role") via a fire-and-forget async task.

Either way the title is generated at most ONCE per run, cached on AgentConn, and
never blocks event processing.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coordinator.server import AgentConn

log = logging.getLogger(__name__)

# Strong references to in-flight tasks so they aren't GC'd on Python 3.12+.
_background_tasks: set[asyncio.Task] = set()  # type: ignore[type-arg]

# Toggle: when false (default), title = raw intent text. When true, LLM rewrites it.
TITLE_GEN_LLM = os.environ.get("TITLE_GEN_LLM", "").lower() in ("1", "true", "yes")

_MAX_TITLE_LEN = 60

_SYSTEM_PROMPT = (
    "You generate extremely short titles (5-8 words max) summarizing what a "
    "desktop automation agent is working on. Output ONLY the title, no quotes, "
    "no explanation. Examples: 'Applying to Acme SWE role', 'Filing TPS report "
    "in SAP', 'Booking flight to NYC'."
)


def _truncate_goal(goal: str) -> str:
    """Produce a title by truncating the raw goal/intent string."""
    goal = goal.strip()
    if len(goal) <= _MAX_TITLE_LEN:
        return goal
    return goal[:_MAX_TITLE_LEN].rsplit(" ", 1)[0] + "…"


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
    """Set the run title on conn. Immediate if LLM is off, async otherwise."""
    if not TITLE_GEN_LLM:
        # Default path: use the raw intent/goal text directly (instant).
        conn.title = _truncate_goal(goal)
        return

    # LLM path: fire-and-forget async rewrite.
    loop = asyncio.get_running_loop()
    snapshot_run_id = conn.run_id

    async def _run() -> None:
        try:
            title = await loop.run_in_executor(None, _generate_title_sync, goal)
            if conn.run_id == snapshot_run_id:
                conn.title = title
        except Exception:
            if conn.run_id == snapshot_run_id:
                conn.title = _truncate_goal(goal)

    task = loop.create_task(_run())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
