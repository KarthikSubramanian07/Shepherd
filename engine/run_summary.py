"""
Medium natural-language response for a finished request.

At the run boundary (cold path, never the click loop) we turn the goal + outcome
+ action trail into a short paragraph the user reads on the frontend: what the
agent did and how it ended. LLM-generated when a key is configured (deterministic
— temperature 0 via engine.llm), with a templated fallback otherwise. Never
raises.
"""
from __future__ import annotations

from engine import llm

_SYSTEM = (
    "You are an assistant reporting back to the user after attempting a task on "
    "their computer or browser. In 2-4 plain sentences, say what you did and how "
    "it ended. Write in the first person ('I ...'). Be concrete but concise; do "
    "NOT invent results that aren't supported by the action trail. No preamble, "
    "no markdown, no bullet points."
)


def summarize_run(goal: str, status: str, trail: list[str], *, error: str = "") -> str:
    """Return a medium (2-4 sentence) response describing the run."""
    goal = (goal or "").strip()
    # Coerce defensively so a caller that passes records (not strings) can never
    # make this raise — the docstring promises it never does.
    steps = [str(s) for s in (trail or []) if s]
    if llm.available():
        try:
            trail_txt = "\n".join(f"- {s}" for s in steps[-20:]) or "(no actions recorded)"
            user = (
                f"Goal: {goal or '(unspecified)'}\n"
                f"Outcome: {status}"
                + (f"\nError: {error}" if error else "")
                + f"\nActions taken (most recent last):\n{trail_txt}"
            )
            text = (llm.complete(_SYSTEM, [("user", user)], max_tokens=300, timeout=20.0) or "").strip()
            if text:
                return text[:800]
        except Exception as e:  # noqa: BLE001
            print(f"[run_summary] LLM summary failed ({e}); using template")
    return _template(goal, status, steps, error)


def _template(goal: str, status: str, steps: list[str], error: str) -> str:
    verb = {
        "completed": "Completed",
        "failed": "Couldn't complete",
        "aborted": "Stopped",
    }.get(status, status.title() or "Finished")
    out = f"{verb} the task" + (f": {goal}." if goal else ".")
    if steps:
        out += f" Took {len(steps)} step(s): " + "; ".join(steps[-5:]) + "."
    if status != "completed" and error:
        out += f" ({error})"
    return out[:800]
