"""
LLM milestone segmenter.

Collapses a low-level UI automation trace (the fine pyautogui steps Agent S
actually performs) into a SHORT list of high-level milestones — the discrete
steps a human would narrate when explaining the workflow. This is the layer
that lets a routine "crystallize": e.g. a new-tab digression to look something
up becomes a single `research` milestone instead of scattered navigate/scan
clicks.

Design constraints (see engine/engine.py docstring):
  • The click path is sacred — this NEVER runs inside the step actuation loop.
    The engine calls segment() at the RUN BOUNDARY (after the loop), exactly
    where the heuristic summarize() used to run.
  • Provider-agnostic: all model calls go through engine/llm.py (gemini/gemma by
    default, anthropic alt), so this module never hard-codes a provider.
  • Always degrades gracefully: with no configured LLM key, on a network error,
    or on a malformed response, it falls back to the heuristic summarize().

Output shape matches summarize()'s milestones so it is a drop-in:
    [{"kind", "label", "value", "fine", "fine_start", "fine_end"}, ...]
in execution order.
"""
from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from engine import llm
from engine.task_graph import summarize

if TYPE_CHECKING:
    from shepherd_types import InterventionEvent, TaskGraphNode

# Closed taxonomy — keeps milestone keys stable across runs and lets the UI
# style nodes by kind. Anything off-taxonomy is coerced to "interact".
TAXONOMY = {
    "open", "navigate", "search", "research",
    "scan", "fill", "submit", "verify", "interact",
}

_SYSTEM = """\
You segment a low-level UI automation trace into a SHORT list of high-level
milestones — the discrete steps a human would narrate when explaining the
workflow, not individual clicks.

BOUNDARY MARKERS — a new milestone usually begins at:
  - open_app (launching/opening an app)
  - navigation (a URL, or focusing the address bar)
  - tab/window switch: cmd+t (new), cmd+w (close), cmd+1..9 / cmd+tab (switch)
  - a search (typing a query then Enter)
  - a submit/commit: Enter / cmd+return / clicking Submit/Save
  - a long wait or page scan right after navigation
Mouse moves, single clicks, short waits, and field-to-field tabbing are
CONTINUATIONS — fold them into the milestone in progress.

RULES:
  - Granularity: produce what a human would narrate. Typically 3-8 milestones.
    NEVER one per click; NEVER one blob for the whole run.
  - BE SPECIFIC. `label` names the concrete action + its target (e.g. "Type
    recipient address", "Click Compose", "Navigate to Gmail inbox") — NOT a vague
    category like "Interact" or "Fill field". `detail` is a fuller one-line
    description of what concretely happened across this milestone's steps.
  - Group consecutive steps serving one goal (filling several fields = one "Fill...").
  - A new-tab/app digression is its own milestone (e.g. "Research..."); the
    return-and-continue afterwards is a separate milestone.
  - WRONG TURNS: a mis-click, a dead-end the run backs out of, a retry, or a
    correction does NOT advance the task. Emit it as its own element flagged
    "detour": true (covering its fine indices) so it is recorded as a mistake
    AGAINST the milestone it interrupted — never as a forward step. Forward
    milestones omit the flag (or set it false).
  - value = the salient payload (search term, URL host, record/field handled), else null.
  - Reuse a PRIOR milestone's exact label when this milestone is the same action
    as one already in the task graph (listed below) — this keeps the graph stable.
  - USER INTERVENTIONS: steps tagged <<< USER INTERVENED >>> are deliberate human
    steering, NOT wrong turns. They must be their own milestone (or part of the
    milestone they belong to) and NEVER flagged as detour. The user's instruction
    and reasoning explain WHY those steps happened — preserve that context in
    the milestone's detail field.

KIND must be one of: open | navigate | search | research | scan | fill | submit | verify | interact

OUTPUT: ONLY a JSON array, no prose. Each element:
  {"kind": "...", "label": "specific action, <=6 words, imperative",
   "detail": "<fuller one-line description of what happened>",
   "value": <string|null>, "detour": <bool — omit or false for forward steps>,
   "fine_start": <int>, "fine_end": <int>}
Cover every fine index in order with no gaps or overlaps
(next.fine_start = prev.fine_end + 1; first.fine_start = the first index shown)."""

# ── Few-shot examples ────────────────────────────────────────────────────────
_EX1_IN = """\
PRIOR MILESTONES: none — first run
TRACE:
0 open_app Chrome localhost/demo-form — "Open browser"
1 wait — "Wait for browser"
2 wait — "Page settling"
3 wait — "Wait for form"
4 batch_fill [First name, Last name, Email, Phone] — "Fill applicant form fields"
5 hotkey tab — "Tab to password/API key - MONITOR TRIGGER"
6 hotkey cmd+return — "Submit form\""""
_EX1_OUT = """\
[
  {"kind": "open", "label": "Open application form", "detail": "opened Chrome to the demo application form", "value": "demo-form", "fine_start": 0, "fine_end": 3},
  {"kind": "fill", "label": "Fill applicant details", "detail": "filled first name, last name, email and phone", "value": null, "fine_start": 4, "fine_end": 4},
  {"kind": "verify", "label": "Reach credential field", "detail": "tabbed onto the API key field (monitor trigger)", "value": "API key", "fine_start": 5, "fine_end": 5},
  {"kind": "submit", "label": "Submit application", "detail": "submitted the form with cmd+return", "value": null, "fine_start": 6, "fine_end": 6}
]"""

_EX2_IN = """\
PRIOR MILESTONES: none — first run
TRACE:
0 open_app Chrome /grant-form — "Open grant application portal"
1 wait — "Page load"
2 type "Acme Robotics" — "Org name field"
3 type "2021" — "Year founded field"
4 hotkey cmd+t — "Open new tab to look up EIN"
5 type "acme robotics EIN" — "Search for EIN"
6 wait — "Results"
7 click — "Open IRS record"
8 wait — "Read EIN"
9 hotkey cmd+1 — "Switch back to application tab"
10 type "47-1234567" — "Enter EIN"
11 type "500000" — "Requested amount"
12 hotkey cmd+return — "Submit application\""""
_EX2_OUT = """\
[
  {"kind": "open", "label": "Open grant application", "detail": "opened the grant application portal", "value": "grant-form", "fine_start": 0, "fine_end": 1},
  {"kind": "fill", "label": "Fill organization details", "detail": "entered org name and year founded", "value": null, "fine_start": 2, "fine_end": 3},
  {"kind": "research", "label": "Look up EIN", "detail": "opened a new tab, searched for the EIN, read it off the IRS record", "value": "EIN", "fine_start": 4, "fine_end": 8},
  {"kind": "fill", "label": "Complete application", "detail": "switched back and entered the EIN and requested amount", "value": null, "fine_start": 9, "fine_end": 11},
  {"kind": "submit", "label": "Submit application", "detail": "submitted the completed application", "value": null, "fine_start": 12, "fine_end": 12}
]"""

_EX3_IN = """\
PRIOR MILESTONES: none — first run
TRACE:
0 open_app Chrome /portal — "Open portal"
1 type "user@x.com" — "Email"
2 type "hunter2" — "Password"
3 hotkey return — "Sign in"
4 wait — "scan: invalid password banner"
5 type "correcthorse" — "Re-enter password"
6 hotkey return — "Sign in"
7 wait — "scan: dashboard loaded\""""
_EX3_OUT = """\
[
  {"kind": "open", "label": "Open portal", "detail": "opened the login portal in Chrome", "value": "portal", "fine_start": 0, "fine_end": 0},
  {"kind": "fill", "label": "Enter credentials", "detail": "typed the email and password", "value": "user@x.com", "fine_start": 1, "fine_end": 2},
  {"kind": "submit", "label": "Sign in", "detail": "submitted the login form", "value": null, "fine_start": 3, "fine_end": 3},
  {"kind": "fill", "label": "Fix rejected password", "detail": "first password was rejected; re-entered the correct one and resubmitted", "value": "invalid password", "detour": true, "fine_start": 4, "fine_end": 6},
  {"kind": "scan", "label": "Dashboard loaded", "detail": "dashboard loaded after the retry", "value": null, "fine_start": 7, "fine_end": 7}
]"""

# ── Few-shot example 4: repeat run with prior milestones + user intervention ──
_EX4_IN = """\
PRIOR MILESTONES:
  Open application form
  Fill applicant details  [taught: if projects field is empty → research their GitHub]
  Submit application
TRACE:
0 open_app Chrome localhost/demo-form — "Open browser"
1 wait — "Wait for form"
2 batch_fill [First name, Last name, Email] — "Fill basic fields"
3 hotkey cmd+t — "Open new tab"  <<< USER INTERVENED: research their GitHub projects first (reason: projects field was empty) >>>
4 type "applicant github projects" — "Search GitHub"
5 wait — "Results page"
6 click — "Open profile"
7 wait — "Read projects"
8 hotkey cmd+1 — "Switch back to form"
9 type "ML pipeline, data viz" — "Fill projects field"
10 hotkey cmd+return — "Submit form\""""
_EX4_OUT = """\
[
  {"kind": "open", "label": "Open application form", "detail": "opened Chrome to the demo application form", "value": "demo-form", "fine_start": 0, "fine_end": 1},
  {"kind": "fill", "label": "Fill applicant details", "detail": "filled basic fields (name, email); projects field was empty", "value": null, "fine_start": 2, "fine_end": 2},
  {"kind": "research", "label": "Research GitHub projects", "detail": "user instructed to research GitHub projects because projects field was empty; searched and read profile", "value": "GitHub", "fine_start": 3, "fine_end": 7},
  {"kind": "fill", "label": "Complete application", "detail": "switched back and filled projects field with findings from research", "value": null, "fine_start": 8, "fine_end": 9},
  {"kind": "submit", "label": "Submit application", "detail": "submitted the completed application", "value": null, "fine_start": 10, "fine_end": 10}
]"""

# ── Few-shot example 5: intervention mid-fill (steer within a milestone) ──────
_EX5_IN = """\
PRIOR MILESTONES:
  Open email client
  Compose new message
  Send message
TRACE:
0 open_app Chrome mail.google.com — "Open Gmail"
1 wait — "Inbox loaded"
2 click — "Click Compose"
3 type "team@example.com" — "To field"
4 type "Weekly update" — "Subject"  <<< USER INTERVENED: also CC manager@example.com and mark as high priority (reason: this is the weekly status report) >>>
5 click — "Open CC field"
6 type "manager@example.com" — "CC field"
7 click — "Click priority indicator"
8 type "Here are this week's highlights..." — "Body text"
9 hotkey cmd+return — "Send\""""
_EX5_OUT = """\
[
  {"kind": "open", "label": "Open email client", "detail": "opened Gmail in Chrome", "value": "mail.google.com", "fine_start": 0, "fine_end": 1},
  {"kind": "fill", "label": "Compose new message", "detail": "clicked compose, filled recipient and subject; user instructed to also CC manager and mark high priority for weekly status report", "value": null, "fine_start": 2, "fine_end": 8},
  {"kind": "submit", "label": "Send message", "detail": "sent the email with cmd+return", "value": null, "fine_start": 9, "fine_end": 9}
]"""

_FEWSHOT = [
    ("user", _EX1_IN), ("assistant", _EX1_OUT),
    ("user", _EX2_IN), ("assistant", _EX2_OUT),
    ("user", _EX3_IN), ("assistant", _EX3_OUT),
    ("user", _EX4_IN), ("assistant", _EX4_OUT),
    ("user", _EX5_IN), ("assistant", _EX5_OUT),
]


# Semantic cache for segmentation: a repeated run produces a near-identical trace,
# so we return the prior segmentation by MEANING instead of paying for the LLM
# again. Lazily built; Redis-optional.
_CACHE_MIN_SIM = 0.95
_cache = None
_cache_init = False


def _semantic_cache():
    global _cache, _cache_init
    if not _cache_init:
        _cache_init = True
        try:
            from services.semantic_cache import SemanticCache
            _cache = SemanticCache("milestones")
        except Exception as e:
            print(f"[milestones] semantic cache unavailable (non-fatal): {e}")
            _cache = None
    return _cache


def llm_available() -> bool:
    return llm.available()


def _truncate(text: str, n: int = 40) -> str:
    t = (text or "").strip().replace("\n", " ")
    return t[:n] + "…" if len(t) > n else t


def render_step(i: int, step) -> str:
    """One compact line per fine step — the model's per-step input row."""
    parts = [str(i), step.action]
    if step.action == "hotkey" and step.keys:
        parts.append("+".join(step.keys))
    elif step.target:
        parts.append(step.target)
    if step.action == "batch_fill" and step.fields:
        labels = [f.description for f in step.fields if f.description]
        if labels:
            parts.append("[" + ", ".join(labels) + "]")
    txt = _truncate(step.text or "")
    if txt:
        parts.append(f'"{txt}"')
    line = " ".join(parts)
    if step.description:
        line += f' — "{step.description}"'
    return line


def _render_trace(
    steps,
    prior_labels: list[str],
    interventions: "Optional[list[InterventionEvent]]" = None,
    prior_nodes: "Optional[list[TaskGraphNode]]" = None,
) -> str:
    # Richer prior context when full node objects are available.
    if prior_nodes:
        prior_lines: list[str] = []
        for n in prior_nodes:
            line = f"  {n.label}"
            if n.conditionals:
                conds = "; ".join(f"if {c.when} → {c.do}" for c in n.conditionals)
                line += f"  [taught: {conds}]"
            if n.source == "taught":
                line += "  (user-taught)"
            prior_lines.append(line)
        prior_section = "PRIOR MILESTONES:\n" + "\n".join(prior_lines)
    elif prior_labels:
        prior_section = "PRIOR MILESTONES: " + "; ".join(dict.fromkeys(prior_labels))
    else:
        prior_section = "PRIOR MILESTONES: none — first run"

    # Build an index of interventions by step_index for inline annotation.
    # Multiple steers can share the same step_index (batch drain), so collect
    # all of them per step.
    iv_map: dict[int, list["InterventionEvent"]] = {}
    for iv in (interventions or []):
        iv_map.setdefault(iv.step_index, []).append(iv)

    trace_lines: list[str] = []
    for i, s in enumerate(steps):
        line = render_step(i, s)
        if i in iv_map:
            annotations: list[str] = []
            for iv in iv_map[i]:
                reason = (iv.scenario or iv.trigger or "").strip()
                instr = (iv.instruction or "").strip()
                parts = []
                if instr:
                    parts.append(instr)
                if reason:
                    parts.append(f"reason: {reason}")
                annotations.append("; ".join(parts) if parts else "user override")
            line += f"  <<< USER INTERVENED: {' | '.join(annotations)} >>>"
        trace_lines.append(line)

    return f"{prior_section}\nTRACE:\n" + "\n".join(trace_lines)


def _coerce_kind(kind: Optional[str]) -> str:
    k = (kind or "").strip().lower()
    return k if k in TAXONOMY else "interact"


_STOP_WORDS = frozenset({
    "a", "an", "the", "to", "in", "on", "at", "of", "for", "and", "or",
    "is", "it", "its", "this", "that", "with", "from", "by",
})


def _token_set(text: str) -> set[str]:
    """Lowercase word tokens, stripping punctuation and stop words."""
    return {w for w in text.lower().split() if w.isalpha() and w not in _STOP_WORDS}


def _snap_label(kind: str, label: str, prior_labels: list[str]) -> str:
    """Reuse a prior milestone's label when it's the same action — fuzzy match.

    Matching tiers (first hit wins):
      1. Exact case-insensitive match.
      2. Token-set Jaccard similarity >= 0.5 — picks the highest-scoring prior.
    This prevents key divergence from minor LLM wording variations
    (e.g. "Fill applicant info" vs "Fill applicant details")."""
    low = label.strip().lower()
    for p in prior_labels:
        if p.strip().lower() == low:
            return p
    # Fuzzy tier: token-set Jaccard.
    label_tokens = _token_set(label)
    if not label_tokens:
        return label.strip()
    best_score, best_label = 0.0, ""
    for p in prior_labels:
        p_tokens = _token_set(p)
        if not p_tokens:
            continue
        jaccard = len(label_tokens & p_tokens) / len(label_tokens | p_tokens)
        if jaccard > best_score:
            best_score = jaccard
            best_label = p
    if best_score >= 0.5:
        return best_label
    return label.strip()


def _parse(data: list, n_steps: int, prior_labels: list[str]) -> list[dict]:
    """Validate the model's parsed JSON array into milestone dicts."""
    if not isinstance(data, list) or not data:
        raise ValueError("not a non-empty JSON array")

    milestones: list[dict] = []
    for item in data:
        fs = int(item["fine_start"])
        fe = int(item["fine_end"])
        if fe < fs:
            fs, fe = fe, fs
        kind = _coerce_kind(item.get("kind"))
        label = _snap_label(kind, str(item.get("label") or "Step")[:60], prior_labels)
        milestones.append({
            "kind": kind,
            "label": label,
            "value": item.get("value") or None,
            "detail": str(item.get("detail") or "")[:200],
            "detour": bool(item.get("detour")),
            "fine": max(1, fe - fs + 1),
            "fine_start": fs,
            "fine_end": fe,
        })

    # Coverage sanity check — reject obviously broken segmentations so we fall
    # back to the heuristic rather than persisting garbage.
    covered = sum(m["fine"] for m in milestones)
    if not (0.5 * n_steps <= covered <= 1.5 * n_steps):
        raise ValueError(f"coverage {covered} far from {n_steps} steps")
    return milestones


def _llm_segment(
    steps,
    variables: dict,
    prior_labels: list[str],
    interventions: "Optional[list[InterventionEvent]]" = None,
    prior_nodes: "Optional[list[TaskGraphNode]]" = None,
) -> list[dict]:
    # Provider-agnostic — engine/llm.py picks gemini/gemma or anthropic.
    messages: list[llm.Message] = list(_FEWSHOT)
    messages.append(("user", _render_trace(
        steps, prior_labels, interventions=interventions, prior_nodes=prior_nodes,
    )))

    text = llm.complete(_SYSTEM, messages, prefill="[")
    return _parse(llm.parse_json_array(text), len(steps), prior_labels)


def _heuristic_segment(steps, variables: dict) -> list[dict]:
    """Heuristic fallback — wraps summarize() and adds contiguous index ranges."""
    heur, _ = summarize(steps, variables)
    out, idx = [], 0
    for m in heur:
        out.append({
            "kind": m["kind"], "label": m["label"], "value": m["value"],
            "detail": "", "detour": False,
            "fine": m["fine"], "fine_start": idx, "fine_end": idx + m["fine"] - 1,
        })
        idx += m["fine"]
    return out


def segment(
    steps,
    variables: dict,
    prior_labels: Optional[list[str]] = None,
    interventions: "Optional[list[InterventionEvent]]" = None,
    prior_nodes: "Optional[list[TaskGraphNode]]" = None,
) -> list[dict]:
    """
    Segment a fine step sequence into ordered high-level milestones.

    Tries the LLM first (when a key is configured); on any failure falls back to
    the deterministic heuristic. Always returns a non-empty list when steps is
    non-empty, in execution order.

    When *interventions* or *prior_nodes* are provided, intervention markers are
    rendered inline at the step indices where they occurred, and prior milestones
    include taught conditionals / procedures so the segmenter respects prior
    teaching and treats user-directed steps as forward milestones (not detours).
    """
    if not steps:
        return []
    prior_labels = prior_labels or []
    if llm_available():
        # Semantic cache lookup BEFORE the LLM call. The step count guards against
        # a near-match whose indices would not line up (a hit must be same length).
        cache = _semantic_cache()
        cache_key = _render_trace(
            steps, prior_labels, interventions=interventions, prior_nodes=prior_nodes,
        )
        if cache and cache.available:
            hit = cache.get(cache_key, min_sim=_CACHE_MIN_SIM)
            if hit:
                cached, sim = hit
                if cached.get("n_steps") == len(steps) and cached.get("milestones"):
                    print(f"[milestones] semantic cache HIT (sim={sim}) — skipped LLM call")
                    return cached["milestones"]
        try:
            ms = _llm_segment(
                steps, variables, prior_labels,
                interventions=interventions, prior_nodes=prior_nodes,
            )
            if ms:
                if cache and cache.available:
                    cache.put(cache_key, {"n_steps": len(steps), "milestones": ms})
                return ms
        except Exception as e:
            print(f"[milestones] LLM segmentation failed (using heuristic): {e}")
    return _heuristic_segment(steps, variables)
