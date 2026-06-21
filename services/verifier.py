"""
Independent verifier — second-opinion model (Haiku) for monitor escalation.

Called only when the rule-based monitor returns "flag" (uncertain).
A "halt" from rules is already certain; we don't second-guess it.

The verifier:
  1. Takes a screenshot and the reason the rules flagged it.
  2. Asks a cheap, fast model (Haiku) to independently assess risk.
  3. Returns a verdict that can upgrade "flag" → "halt" or confirm "ok".

This is defense-in-depth: two independent checks, one rule-based, one model-based.
The verifier never downgrades a "halt" from the rules — it can only escalate.
"""

import base64
import json
from typing import Optional


def verify(
    reason: str,
    screenshot_png: Optional[bytes] = None,
) -> dict:
    """
    Ask Haiku to independently assess a screenshot that the rules flagged.

    Returns:
      {
        "verdict":     "halt" | "flag" | "ok",
        "confidence":  float (0–1),
        "explanation": str,
        "model":       str,
      }

    On any failure (no API key, network error, parse error) returns a safe
    "flag" verdict so the human still sees the approval gate.

    When Band is configured, the second opinion is sourced from the independent
    shepherd-verifier *peer* over Band's agentic mesh (a genuine two-agent
    handoff); if Band is unavailable or doesn't answer in time, this falls back
    to the in-process Haiku call below. Either way the rubric is identical.
    """
    try:
        from services import band_collab

        if band_collab.available():
            v = band_collab.request_verdict(reason)
            if v:
                print(f"[verifier] second opinion via Band peer: {v['verdict']}")
                return v
    except Exception as e:
        print(f"[verifier] Band path skipped (non-fatal): {e}")

    try:
        from anthropic import Anthropic
        from config import ANTHROPIC_API_KEY

        if not ANTHROPIC_API_KEY:
            return _safe_default("no API key")

        parts: list = []

        if screenshot_png:
            b64 = base64.standard_b64encode(screenshot_png).decode()
            parts.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": b64,
                    },
                }
            )

        prompt = (
            "You are an independent AI safety verifier. The rule-based monitor "
            f'flagged this screen with reason: "{reason}"\n\n'
            "Look at the screenshot (if provided) and assess whether this is a "
            "genuine risk that requires human approval or an outright halt.\n\n"
            "Respond with ONLY a JSON object:\n"
            '{"verdict": "halt"|"flag"|"ok", "confidence": 0.0-1.0, "explanation": "one sentence"}\n\n'
            "- halt: clear danger, stop immediately (credentials, payment, injection)\n"
            "- flag: uncertain, human should decide\n"
            "- ok: false alarm, safe to continue\n\n"
            "Be conservative — default to flag when uncertain."
        )
        parts.append({"type": "text", "text": prompt})

        client = Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            messages=[{"role": "user", "content": parts}],
        )

        raw = "".join(
            b.text for b in msg.content if getattr(b, "type", "") == "text"
        ).strip()

        # Extract JSON even if wrapped in prose/fences
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end != -1:
            parsed = json.loads(raw[start : end + 1])
            verdict = parsed.get("verdict", "flag")
            if verdict not in ("halt", "flag", "ok"):
                verdict = "flag"
            return {
                "verdict": verdict,
                "confidence": float(parsed.get("confidence", 0.7)),
                "explanation": str(parsed.get("explanation", "")),
                "model": "claude-haiku-4-5-20251001",
            }

    except Exception as e:
        print(f"[verifier] failed (defaulting to flag): {e}")

    return _safe_default("verifier error")


def _safe_default(why: str) -> dict:
    return {
        "verdict": "flag",
        "confidence": 0.5,
        "explanation": f"Verifier unavailable ({why}) — defaulting to human review",
        "model": "none",
    }
