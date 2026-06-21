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
    BAND_COUNCIL,
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
    """Ask the oversight council over Band whether a flagged screen is a real risk.

    Posts the flagged action into the room @mentioning every council member, then
    tallies their votes. With no council configured this is a council of one (the
    shepherd-verifier) — identical to the original single-verifier handoff.

    Returns a verdict dict matching services.verifier.verify(), plus a `votes`
    breakdown, or None on any failure/timeout so the caller can fall back.
    """
    if not FEATURES["band"]:
        return None
    members = _council_members()
    try:
        import httpx

        with httpx.Client(headers=_headers(), timeout=8.0) as client:
            # Snapshot existing message ids so we only accept NEW replies
            # (order-independent and pagination-tolerant, unlike a "since" cursor).
            before = _message_ids(client)
            mentions = " ".join(f"@{h}" for h, _ in members)
            single = len(members) == 1
            body = (
                f"{mentions} The rule-based monitor flagged a high-stakes desktop "
                f"action and is uncertain.\n\n"
                f"Reason: {reason}\n"
                + (f"Context: {context}\n" if context else "")
                + (
                    "\nReply with one line: VERDICT: halt|flag|ok — <one-sentence reason>."
                    if single
                    else "\nEach specialist: reply with one line: VOTE: halt|flag|ok — "
                    "<one-sentence reason from your specialty>."
                )
            )
            client.post(
                f"{BAND_API_BASE}/chats/{BAND_ROOM_ID}/messages",
                json={
                    "message": {
                        "content": body,
                        "mentions": [
                            {"id": aid, "handle": h, "kind": "mention"}
                            for h, aid in members
                        ],
                    }
                },
            ).raise_for_status()

            by_id = {aid: h for h, aid in members}
            deadline = time.monotonic() + _VERDICT_TIMEOUT_S
            while time.monotonic() < deadline:
                time.sleep(_POLL_INTERVAL_S)
                votes = _collect_votes(client, before, by_id)
                # Resolve as soon as everyone has voted; otherwise wait out the
                # window and tally whoever answered (graceful with a slow member).
                if len(votes) >= len(members):
                    return _tally(votes)
            votes = _collect_votes(client, before, by_id)
            if votes:
                return _tally(votes)
            print("[band] council did not answer in time, falling back")
    except Exception as e:
        print(f"[band] request_verdict non-fatal: {e}")
    return None


# `request_council_verdict` is the descriptive name; keep `request_verdict` as the
# stable entry point that services.verifier already calls.
request_council_verdict = request_verdict


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


def _council_members() -> list[tuple[str, str]]:
    """The roster as [(handle, agent_id), ...]. BAND_COUNCIL is a comma-separated
    list of `handle:agent_id`; empty falls back to the single shepherd-verifier
    (a council of one), so an unconfigured deployment behaves exactly as before."""
    roster: list[tuple[str, str]] = []
    for entry in (BAND_COUNCIL or "").split(","):
        entry = entry.strip()
        if not entry or ":" not in entry:
            continue
        handle, aid = entry.split(":", 1)
        handle, aid = handle.strip().lstrip("@"), aid.strip()
        if handle and aid:
            roster.append((handle, aid))
    if not roster and BAND_VERIFIER_AGENT_ID:
        roster.append((BAND_VERIFIER_HANDLE, BAND_VERIFIER_AGENT_ID))
    return roster


def _collect_votes(client, before_ids: set, by_id: dict) -> dict:
    """{agent_id: (verdict, reason)} for council members who have posted a NEW
    vote since the snapshot. Latest parseable vote per member wins. Order- and
    pagination-independent."""
    votes: dict = {}
    for m in _list_messages(client):
        if m.get("id") in before_ids:
            continue
        aid = _sender_id(m)
        if aid not in by_id:
            continue
        parsed = _parse_vote(m.get("content") or m.get("text") or "")
        if parsed:
            votes[aid] = parsed
    return votes


_VOTE_RE = re.compile(r"(?:verdict|vote)\s*[:\-]*\s*(halt|flag|ok)\b", re.IGNORECASE)


def _parse_vote(text: str) -> Optional[tuple[str, str]]:
    """Pull the (verdict, reason) that immediately follows 'VOTE:'/'VERDICT:'.
    Anchored (not a substring-anywhere scan) so 'VOTE: ok, no halt needed' reads
    as ok, and on-screen text cannot smuggle a verdict in."""
    if not text:
        return None
    m = _VOTE_RE.search(text)
    if not m:
        return None
    after = text[m.end() :].lstrip(" -:—").strip()
    reason = (after.splitlines()[0] if after else "").strip()[:240]
    return (m.group(1).lower(), reason)


def _tally(votes: dict) -> dict:
    """Combine per-member votes into a single conservative verdict + a breakdown.
    Any halt -> halt; otherwise any flag -> flag; only all-ok -> ok."""
    by_id = {aid: h for h, aid in _council_members()}
    breakdown = [
        {"handle": by_id.get(aid, aid), "verdict": v, "reason": r}
        for aid, (v, r) in votes.items()
    ]
    verdicts = [v for v, _ in votes.values()]
    n = {x: verdicts.count(x) for x in _VALID}
    if n["halt"]:
        verdict = "halt"
    elif n["flag"]:
        verdict = "flag"
    else:
        verdict = "ok"

    # Pick a representative reason: the strongest dissent if any, else any reason.
    lead = next((b for b in breakdown if b["verdict"] == verdict and b["reason"]), None)
    council = len(breakdown) > 1
    summary = (
        f"Council: {n['halt']} halt / {n['flag']} flag / {n['ok']} ok"
        if council
        else (lead["reason"] if lead else "Band verifier peer verdict")
    )
    if council and lead and lead["reason"]:
        summary += f" — {lead['reason']}"
    return {
        "verdict": verdict,
        "confidence": 0.85,
        "explanation": summary[:240],
        "model": "band:council" if council else "band:shepherd-verifier",
        "votes": breakdown,
    }
