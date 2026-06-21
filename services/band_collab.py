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
_VERDICT_TIMEOUT_S = 20.0
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
            since = _latest_message_id(client)
            body = (
                f"@{BAND_VERIFIER_HANDLE} The rule-based monitor flagged a "
                f"high-stakes desktop action and is uncertain.\n\n"
                f"Reason: {reason}\n"
                + (f"Context: {context}\n" if context else "")
                + "\nReply with one line: VERDICT: halt|flag|ok — <one-sentence reason>."
            )
            client.post(
                f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/messages",
                json={
                    "content": body,
                    "mentions": [{"agent_id": BAND_VERIFIER_AGENT_ID}],
                },
            ).raise_for_status()

            deadline = time.monotonic() + _VERDICT_TIMEOUT_S
            while time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL_S)
                reply = _verifier_reply_after(client, since)
                if reply:
                    parsed = _parse_verdict(reply)
                    if parsed:
                        return parsed
            print("[band] verifier peer did not answer in time — falling back")
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
        httpx.post(
            f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/events",
            headers=_headers(),
            json={"type": kind, "content": text},
            timeout=5.0,
        )
    except Exception as e:
        print(f"[band] publish_event non-fatal: {e}")


# ── internals ──────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"Bearer {BAND_ENGINE_API_KEY}",
        "Content-Type": "application/json",
    }


def _latest_message_id(client) -> Optional[str]:
    """Newest message id right now, so we only consider replies that come after."""
    try:
        msgs = _list_messages(client)
        return msgs[-1].get("id") if msgs else None
    except Exception:
        return None


def _list_messages(client) -> list[dict]:
    r = client.get(f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/messages")
    r.raise_for_status()
    data = r.json()
    # Agent API returns either a bare list or {"data": [...]}; handle both.
    return data.get("data", data) if isinstance(data, dict) else data


def _verifier_reply_after(client, since_id: Optional[str]) -> Optional[str]:
    """Text of the first message from the verifier agent posted after `since_id`."""
    msgs = _list_messages(client)
    seen_marker = since_id is None
    for m in msgs:
        if not seen_marker:
            if m.get("id") == since_id:
                seen_marker = True
            continue
        sender = m.get("agent_id") or m.get("sender_id") or (m.get("sender") or {}).get("id")
        if sender == BAND_VERIFIER_AGENT_ID:
            return m.get("content") or m.get("text") or ""
    return None


def _parse_verdict(text: str) -> Optional[dict]:
    """Pull 'VERDICT: <halt|flag|ok> — <reason>' out of the verifier's reply."""
    low = text.lower()
    idx = low.find("verdict")
    scan = low[idx:] if idx != -1 else low
    verdict = next((v for v in _VALID if v in scan), None)
    if not verdict:
        return None
    # Reason = everything after the verdict word on its line.
    after = scan.split(verdict, 1)[1].lstrip(" -—:").strip()
    explanation = (after.splitlines()[0] if after else "").strip() or "Band verifier peer verdict"
    return {
        "verdict":     verdict,
        "confidence":  0.85,
        "explanation": explanation[:240],
        "model":       "band:shepherd-verifier",
    }
