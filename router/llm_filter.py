"""
LLM-based precision filter for the router's candidate generation pipeline.

Vector search returns top-K candidates (recall); this module asks a fast LLM
which candidate, if any, genuinely satisfies the user's intent (precision).
Only invoked on the COLD routing path — never on the hot per-step loop.
"""
from __future__ import annotations

from typing import Optional

from engine import llm

# Candidate descriptor passed to the LLM prompt.
CandidateInfo = dict  # {"id": str, "name": str, "description": str}

_SYSTEM = (
    "You are a strict intent-matching filter. The user will give you their request "
    "and a list of candidate actions. Reply with ONLY the id of the candidate that "
    "genuinely satisfies the request, or the word NONE if no candidate is a good match. "
    "Do not explain. Output exactly one token: an id or NONE."
)

_USER_TEMPLATE = (
    "User request: {intent}\n\n"
    "Candidates:\n{candidates}\n\n"
    "Which candidate id (if any) actually satisfies the request? Reply with the id or NONE."
)


def select(intent_text: str, candidates: list[CandidateInfo]) -> Optional[str]:
    """Ask the LLM which candidate (if any) matches the intent.

    Returns the chosen candidate id, or None if the LLM says NONE or is unavailable.
    Graceful degradation: returns None (caller falls back) on any error.
    """
    if not candidates:
        return None
    if not llm.available():
        return None

    candidates_block = "\n".join(
        f"- id: {c['id']}, name: {c['name']}, description: {c['description']}"
        for c in candidates
    )
    user_msg = _USER_TEMPLATE.format(intent=intent_text, candidates=candidates_block)

    try:
        raw = llm.complete(
            system=_SYSTEM,
            messages=[("user", user_msg)],
            max_tokens=64,
            timeout=15.0,
        )
    except Exception as e:
        print(f"[llm_filter] LLM call failed (non-fatal): {e}")
        return None

    answer = raw.strip().strip('"').strip("'")
    if not answer or answer.upper() == "NONE":
        return None

    # Validate the answer is one of the candidate ids
    valid_ids = {c["id"] for c in candidates}
    if answer in valid_ids:
        return answer

    # Try to find the id in the answer (model may wrap it in quotes or extra text)
    for cid in valid_ids:
        if cid in answer:
            return cid

    return None
