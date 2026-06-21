"""
Goal generalizer — turn a specific autonomous goal into a reusable WORKFLOW name.

A task graph is keyed by its goal. Left specific, every run of a slightly
different goal spawns its OWN graph ("write a gmail message about meteorology",
"write a gmail message about cooking", …) and nothing ever crystallizes. By
generalizing the goal to the repeatable procedure it instantiates —

    "write a gmail message about meteorology"  ->  "write a gmail message"

— all such runs reinforce and branch ONE general workflow graph. The
topic/subject/recipient become per-run payload (kept in node values + the
graph's `intents` provenance list), not part of the key.

Design (mirrors engine/milestones.py):
  • Provider-agnostic LLM call via engine/llm.py; never the click path — this
    runs once at the run boundary's start (task-key resolution).
  • Semantic-cached: a near-identical goal returns the SAME canonical name, so
    the generalization is stable AND cheap on repeats. Redis-optional.
  • Degrades gracefully: no LLM key / network error / junk output → a
    deterministic heuristic that strips the trailing topic clause.
  • generalize_goal() NEVER raises; on total failure it returns the goal as-is.
"""
from __future__ import annotations

import re

from engine import llm

# Prepositional heads that introduce an instance-specific topic/payload. Used by
# the heuristic fallback to lop off "… about meteorology" / "… titled X".
_TOPIC_HEADS = (
    " about ", " regarding ", " concerning ", " on the topic of ",
    " on the subject of ", " titled ", " entitled ", " called ",
    " saying ", " that says ", " with the subject ", " re ",
)

_SYSTEM = """\
You turn a specific user task goal into a GENERAL, reusable workflow name.

Remove instance-specific payload — topics, subjects, recipients, names, values,
dates, search terms, quoted text. KEEP the action verb and the app/target
(gmail, amazon, the form, etc.). The result names the REPEATABLE procedure, so
future runs of the same KIND of task reuse it.

Rules:
  - Short, lowercase, imperative; no trailing punctuation.
  - Keep the concrete app/target when the goal names one (gmail, amazon, slack).
  - Do NOT invent detail the goal didn't state. Do NOT keep the specific topic.
  - Output ONLY the generalized goal text on a single line — no quotes, no prose.

Examples:
  write a gmail message about meteorology        -> write a gmail message
  send Dana a slack message about the standup    -> send a slack message
  apply to the senior backend role at Stripe     -> apply to a job
  buy a stainless steel water bottle on amazon   -> buy an item on amazon
  search google for the population of Tokyo       -> search google
  book a flight from SFO to JFK on July 3         -> book a flight
  fill out the Acme grant application form        -> fill out a form"""

# In-process memo so the two task-key resolutions per run (planned + reactive),
# and repeat goals within a session, never re-pay — even with no Redis.
_memo: dict[str, str] = {}

# Semantic cache: a reworded-but-equivalent goal returns the same canonical name.
_CACHE_MIN_SIM = 0.93
_cache = None
_cache_init = False


def _semantic_cache():
    global _cache, _cache_init
    if not _cache_init:
        _cache_init = True
        try:
            from services.semantic_cache import SemanticCache
            _cache = SemanticCache("generalize")
        except Exception as e:
            print(f"[generalize] semantic cache unavailable (non-fatal): {e}")
            _cache = None
    return _cache


def _clean(text: str) -> str:
    """Normalize a candidate workflow name: first line, unquoted, lowercase, tidy."""
    line = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    line = line.strip().strip('"').strip("'").strip()
    line = re.sub(r"\s+", " ", line).rstrip(" .!").lower()
    return line


def _heuristic(goal: str) -> str:
    """Deterministic fallback: drop the trailing instance-topic clause."""
    g = _clean(goal)
    low = g
    cut = len(g)
    for head in _TOPIC_HEADS:
        i = low.find(head)
        if i != -1:
            cut = min(cut, i)
    g = g[:cut].strip()
    # Strip a trailing quoted payload, e.g. write an email "Q3 numbers".
    g = re.sub(r'\s*["“].*$', "", g).strip()
    return g or _clean(goal)


def _llm_generalize(goal: str) -> str:
    text = llm.complete(_SYSTEM, [("user", _clean(goal))])
    out = _clean(text)
    # Guard against junk: empty, or a verbose run-on that didn't actually
    # generalize (kept everything, or longer than the input).
    if not out or len(out) > max(60, len(goal)):
        raise ValueError(f"implausible generalization: {out!r}")
    return out


def generalize_goal(goal: str) -> str:
    """Return a general, reusable workflow name for `goal`.

    'write a gmail message about meteorology' -> 'write a gmail message'.
    LLM-first (semantic-cached); heuristic fallback; never raises.
    """
    g = (goal or "").strip()
    if not g:
        return g
    if g in _memo:
        return _memo[g]

    result: str | None = None
    if llm.available():
        cache = _semantic_cache()
        if cache and cache.available:
            hit = cache.get(g, min_sim=_CACHE_MIN_SIM)
            if hit:
                cached, sim = hit
                name = (cached or {}).get("general")
                if name:
                    print(f"[generalize] semantic cache HIT (sim={sim}) — skipped LLM")
                    result = name
        if result is None:
            try:
                result = _llm_generalize(g)
                if cache and cache.available:
                    cache.put(g, {"general": result})
            except Exception as e:
                print(f"[generalize] LLM failed (using heuristic): {e}")

    if result is None:
        result = _heuristic(g)

    _memo[g] = result
    if result != _clean(g):
        print(f"[generalize] '{g}' -> '{result}'")
    return result
