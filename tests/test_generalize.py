"""
Tests for goal generalization — a specific autonomous goal is converted into a
reusable WORKFLOW name so all runs of that kind crystallize into ONE graph.

    "write a gmail message about meteorology" -> "write a gmail message"

No network: the heuristic path is deterministic, and the LLM path is exercised
with a stubbed engine.llm. Run directly or under pytest.
"""
import functools
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import generalize as G
from engine import llm

_ORIG_AVAILABLE = llm.available
_ORIG_COMPLETE = llm.complete


def isolated(fn):
    """Reset generalize state + restore llm stubs after the test (no leakage)."""
    @functools.wraps(fn)
    def wrapper(*a, **kw):
        G._memo.clear()
        # Default: Redis/semantic cache off, so tests hit the live/heuristic path.
        G._cache = None
        G._cache_init = True
        try:
            return fn(*a, **kw)
        finally:
            llm.available = _ORIG_AVAILABLE
            llm.complete = _ORIG_COMPLETE
            G._memo.clear()
            G._cache = None
            G._cache_init = False
    return wrapper


# ── heuristic fallback (no LLM) ──────────────────────────────────────────────
@isolated
def test_heuristic_strips_about_topic():
    llm.available = lambda: False  # force offline path
    assert G.generalize_goal("write a gmail message about meteorology") == "write a gmail message"
    assert G.generalize_goal("Send Dana a slack message regarding the standup") \
        == "send dana a slack message"
    assert G.generalize_goal('write an email titled "Q3 numbers"') == "write an email"


@isolated
def test_heuristic_strips_quoted_payload():
    llm.available = lambda: False
    assert G.generalize_goal('compose a note "buy milk and eggs"') == "compose a note"


@isolated
def test_heuristic_passthrough_when_no_topic_clause():
    llm.available = lambda: False
    # Nothing to strip — already general. Lowercased + tidied, but preserved.
    assert G.generalize_goal("Open the calculator") == "open the calculator"


@isolated
def test_empty_goal_is_safe():
    llm.available = lambda: False
    assert G.generalize_goal("") == ""
    assert G.generalize_goal("   ") == ""


# ── LLM path (stubbed) ───────────────────────────────────────────────────────
@isolated
def test_llm_result_is_used_and_cleaned():
    llm.available = lambda: True
    llm.complete = lambda system, messages, **kw: '"Write A Gmail Message"\nextra prose'
    assert G.generalize_goal("write a gmail message about the weather") == "write a gmail message"


@isolated
def test_llm_junk_falls_back_to_heuristic():
    llm.available = lambda: True
    # Verbose run-on that didn't generalize -> rejected -> heuristic.
    llm.complete = lambda system, messages, **kw: "x" * 500
    assert G.generalize_goal("write a gmail message about meteorology") == "write a gmail message"


@isolated
def test_memo_avoids_second_call():
    llm.available = lambda: True
    calls = {"n": 0}

    def _complete(system, messages, **kw):
        calls["n"] += 1
        return "buy an item on amazon"

    llm.complete = _complete
    goal = "buy a stainless steel water bottle on amazon"
    assert G.generalize_goal(goal) == "buy an item on amazon"
    assert G.generalize_goal(goal) == "buy an item on amazon"
    assert calls["n"] == 1  # second resolution served from the in-process memo


# ── engine integration: the task key is keyed by the GENERAL goal ────────────
@isolated
def test_task_key_uses_generalized_goal():
    llm.available = lambda: False  # heuristic
    from engine.engine import ShepherdExecutionEngine
    k1 = ShepherdExecutionEngine._autonomous_task_key("write a gmail message about meteorology")
    k2 = ShepherdExecutionEngine._autonomous_task_key("write a gmail message about cooking")
    # Both specific goals collapse onto the same general workflow key.
    assert k1 == k2 == "AUTONOMOUS::write_a_gmail_message"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
