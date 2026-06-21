"""
Band (band.ai / Thenvoi) — multi-agent oversight collaboration. BOUNDARY ONLY.

This is the engine side of a genuine two-agent collaboration over Band's agentic
mesh. When the rule-based monitor is *uncertain* about a high-stakes screen, the
Shepherd engine (acting as the "shepherd-monitor" Band agent) posts the flagged
action into a shared Band room, @mentioning the independent "shepherd-verifier"
agent, and reads back the verifier's verdict. The verifier runs as its own Band
agent in `services/band_verifier.py` (Claude via the Band AnthropicAdapter).

Two agents, coordinating over Band, exactly as Band's own Drafter/Reviewer
example does — mapped onto Shepherd's Monitor/Verifier oversight handoff.

Everything here is off the click path (the engine only calls this at a flagged
boundary, never mid-action) and degrades gracefully: any failure, timeout, or
missing credential returns None, and the caller falls back to the in-process
verifier. Band is never load-bearing for safety.

Uses the free Agent API (REST) directly over httpx, so the engine side needs no
SDK install — only the verifier agent process does. Endpoints and auth follow
docs.band.ai/api (Agent API: /api/v1/agent).
"""
import re
import time
from typing import Optional

from config import (
    FEATURES,
    BAND_API_BASE,
    BAND_ROOM_ID,
    BAND_ENGINE_API_KEY,
    BAND_VERIFIER_AGENT_ID,
    BAND_VERIFIER_HANDLE,
)

# How long to wait for the verifier peer to answer before falling back (seconds).
# Off the click path (a boundary check), but still a synchronous wait on the run
# thread, so keep it tight: the live round-trip is ~5s.
_VERDICT_TIMEOUT_S = 12.0
_POLL_INTERVAL_S = 1.5
_VALID = ("halt", "flag", "ok")


def available() -> bool:
    """True only when Band is fully configured AND the engine key authenticates.

    Hitting GET /me validates the connection (per the Band guide). If auth is
    wrong or the platform is unreachable, we report unavailable so the oversight
    path silently uses the in-process verifier instead of stalling.
    """
    if not FEATURES["band"]:
        return False
    try:
        import httpx
        r = httpx.get(f"{BAND_API_BASE}/me", headers=_headers(), timeout=5.0)
        return r.status_code == 200
    except Exception as e:
        print(f"[band] unavailable (non-fatal): {e}")
        return False


def request_verdict(reason: str, context: str = "") -> Optional[dict]:
    """Ask the verifier peer over Band whether a flagged screen is a real risk.

    Returns a verdict dict matching services.verifier.verify():
      {"verdict": "halt"|"flag"|"ok", "confidence": float, "explanation": str, "model": str}
    or None on any failure/timeout so the caller can fall back.
    """
    if not FEATURES["band"]:
        return None
    try:
        import httpx
        with httpx.Client(headers=_headers(), timeout=8.0) as client:
            # Snapshot existing message ids so we only accept a NEW verifier reply
            # (order-independent and pagination-tolerant, unlike a "since" cursor).
            before = _message_ids(client)
            body = (
                f"@{BAND_VERIFIER_HANDLE} The rule-based monitor flagged a "
                f"high-stakes desktop action and is uncertain.\n\n"
                f"Reason: {reason}\n"
                + (f"Context: {context}\n" if context else "")
                + "\nReply with one line: VERDICT: halt|flag|ok — <one-sentence reason>."
            )
            client.post(
                f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/messages",
                json={"message": {
                    "content": body,
                    "mentions": [{
                        "id":     BAND_VERIFIER_AGENT_ID,
                        "handle": BAND_VERIFIER_HANDLE,
                        "kind":   "mention",
                    }],
                }},
            ).raise_for_status()

            deadline = time.monotonic() + _VERDICT_TIMEOUT_S
            while time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL_S)
                reply = _new_verifier_reply(client, before)
                if reply:
                    parsed = _parse_verdict(reply)
                    if parsed:
                        return parsed
            print("[band] verifier peer did not answer in time, falling back")
    except Exception as e:
        print(f"[band] request_verdict non-fatal: {e}")
    return None


def publish_event(kind: str, text: str) -> None:
    """Post an informational run-lifecycle event into the oversight room.

    Events (not messages) are Band's channel for thoughts / progress / tool calls
    — they don't require @mentions and give the room a replayable record of every
    Shepherd run alongside the verdict exchanges. Fire-and-forget.
    """
    if not FEATURES["band"]:
        return
    try:
        import httpx
        # Events carry a fixed set of message_type values; run lifecycle maps to
        # an informational "thought". The human-readable kind is prefixed in text.
        httpx.post(
            f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/events",
            headers=_headers(),
            json={"event": {"content": f"[{kind}] {text}", "message_type": "thought"}},
            timeout=5.0,
        )
    except Exception as e:
        print(f"[band] publish_event non-fatal: {e}")


# ── internals ──────────────────────────────────────────────────────────────

def _headers() -> dict:
    # Band's Agent API authenticates with the X-API-Key header (verified against
    # the live /me endpoint — Bearer and api_key query are both rejected).
    return {
        "X-API-Key": BAND_ENGINE_API_KEY,
        "Content-Type": "application/json",
    }


def _message_ids(client) -> set:
    """Set of message ids currently in the room (the 'before' snapshot)."""
    try:
        return {m.get("id") for m in _list_messages(client) if m.get("id")}
    except Exception:
        return set()


def _list_messages(client) -> list[dict]:
    r = client.get(f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/messages")
    r.raise_for_status()
    data = r.json()
    # Agent API returns either a bare list or {"data": [...]}; handle both.
    return data.get("data", data) if isinstance(data, dict) else data


def _sender_id(m: dict) -> Optional[str]:
    # The Band Agent API carries the author in `sender_id`; tolerate alternates.
    return m.get("sender_id") or m.get("agent_id") or (m.get("sender") or {}).get("id")


def _new_verifier_reply(client, before_ids: set) -> Optional[str]:
    """Text of a NEW message (id not in the pre-post snapshot) authored by the
    verifier agent that actually parses to a verdict. Order-independent, so it
    does not depend on how the API paginates or sorts the message list."""
    best = None
    for m in _list_messages(client):
        if m.get("id") in before_ids:
            continue
        if _sender_id(m) != BAND_VERIFIER_AGENT_ID:
            continue
        content = m.get("content") or m.get("text") or ""
        if _parse_verdict(content):       # prefer a message that carries a verdict
            best = content
    return best


_VERDICT_RE = re.compile(r"verdict\s*[:\-]*\s*(halt|flag|ok)\b", re.IGNORECASE)


def _parse_verdict(text: str) -> Optional[dict]:
    """Pull the verdict token that immediately follows 'VERDICT:' out of the
    reply. Anchored (not a substring-anywhere scan) so 'VERDICT: ok, no halt
    needed' reads as ok, and so on-screen text cannot smuggle a verdict in."""
    if not text:
        return None
    m = _VERDICT_RE.search(text)
    if not m:
        return None
    verdict = m.group(1).lower()
    after = text[m.end():].lstrip(" -:—").strip()
    explanation = (after.splitlines()[0] if after else "").strip() or "Band verifier peer verdict"
    return {
        "verdict":     verdict,
        "confidence":  0.85,
        "explanation": explanation[:240],
        "model":       "band:shepherd-verifier",
    }
