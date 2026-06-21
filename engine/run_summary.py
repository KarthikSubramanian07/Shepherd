"""
Per-request response shown to the user on the frontend.

The response adapts to what the user asked for:
  • A QUESTION / information lookup  -> the actual ANSWER, drawn from what the
    agent observed (its reads/observations in the action trail).
  • An ACTION / task ("do X")        -> just "Completed." on success, or a
    one-line reason on failure.

Built at the run boundary (cold path, never the click loop). LLM-generated when
a key is configured (deterministic — temperature 0 via engine.llm), with a
templated fallback otherwise. Never raises.
"""
from __future__ import annotations

from engine import llm

# Exact string used for a successful action task (no LLM, or LLM echoes it).
DONE = "Completed."

_SYSTEM = (
    "You report back to the user after an agent attempted their request on a "
    "computer or browser. First decide what KIND of request it was:\n"
    "• A QUESTION or information lookup (the user wants to KNOW something): "
    "answer it directly and concisely, using ONLY facts present in the action "
    "trail (the agent's reads/observations). If the trail does not contain the "
    "answer, say you couldn't find it — do NOT guess.\n"
    "• An ACTION or task (the user wants something DONE): if it succeeded, reply "
    'with exactly "Completed." and nothing else. If it failed, give a one-'
    "sentence reason.\n"
    "Write in the first person. Be concise. Never invent facts not in the trail. "
    "No preamble, no markdown."
)


def summarize_run(goal: str, status: str, trail: list[str], *, error: str = "") -> str:
    """Return the answer (for a question) or a completion note (for a task)."""
    goal = (goal or "").strip()
    # Coerce defensively so a caller that passes records (not strings) can never
    # make this raise — the docstring promises it never does.
    steps = [str(s) for s in (trail or []) if s]
    if llm.available():
        try:
            trail_txt = "\n".join(f"- {s}" for s in steps[-25:]) or "(no actions recorded)"
            user = (
                f"User request: {goal or '(unspecified)'}\n"
                f"Outcome: {status}"
                + (f"\nError: {error}" if error else "")
                + f"\nAction trail (most recent last; 'read:' lines are observed content):\n{trail_txt}"
            )
            text = (llm.complete(_SYSTEM, [("user", user)], max_tokens=300, timeout=20.0) or "").strip()
            if text:
                return text[:800]
        except Exception as e:  # noqa: BLE001
            print(f"[run_summary] LLM summary failed ({e}); using template")
    return _template(goal, status, steps, error)


def _template(goal: str, status: str, steps: list[str], error: str) -> str:
    """Offline fallback. Without an LLM we can't read out an answer, so a
    completed run reports completion; a failed/aborted run reports why."""
    if status == "completed":
        return DONE
    if status == "aborted":
        return f"Stopped before finishing{': ' + goal if goal else ''}."
    reason = f" ({error})" if error else ""
    return f"Couldn't complete{': ' + goal if goal else ''}.{reason}"
