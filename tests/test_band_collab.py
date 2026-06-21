"""
Band oversight council — vote parsing, tally, and graceful degradation.

Band is never load-bearing for safety: with no credentials it must be a no-op so
the oversight path falls back to the in-process verifier. Whatever phrasing a
council member replies with, we must extract a valid vote, and the chair must
tally a panel of votes conservatively (any halt wins).
"""
from services import band_collab


def test_disabled_is_a_noop(monkeypatch):
    # Force the feature off regardless of the ambient .env so this never hits the
    # live API: inert, returns None, publish is a no-op.
    monkeypatch.setitem(band_collab.FEATURES, "band", False)
    assert band_collab.available() is False
    assert band_collab.request_verdict("credential field detected") is None
    band_collab.publish_event("run.start", "should not raise")  # no-op


def test_parse_vote_extracts_verdict_and_reason():
    v, r = band_collab._parse_vote("VOTE: halt — credentials on screen")
    assert v == "halt"
    assert "credential" in r.lower()
    # The single-verifier phrasing (VERDICT:) is accepted too.
    assert band_collab._parse_vote("VERDICT: flag — unsure")[0] == "flag"


def test_parse_vote_anchors_to_token():
    # The token right after VOTE:/VERDICT: wins, not the first of (halt,flag,ok)
    # found anywhere in the reason.
    assert band_collab._parse_vote("VOTE: ok — no halt needed")[0] == "ok"
    assert band_collab._parse_vote("VERDICT: halt — do not say ok")[0] == "halt"


def test_parse_vote_rejects_garbage():
    assert band_collab._parse_vote("I cannot decide right now") is None
    assert band_collab._parse_vote("") is None


def test_tally_any_halt_wins(monkeypatch):
    # Three specialists; one halt must carry the whole council.
    monkeypatch.setattr(
        band_collab, "_council_members",
        lambda: [("sec", "a1"), ("priv", "a2"), ("dest", "a3")],
    )
    out = band_collab._tally({
        "a1": ("ok", "looks fine"),
        "a2": ("halt", "leaks an email + token"),
        "a3": ("flag", "maybe irreversible"),
    })
    assert out["verdict"] == "halt"
    assert out["model"] == "band:council"
    assert len(out["votes"]) == 3
    assert "1 halt" in out["explanation"]


def test_tally_all_ok_is_ok(monkeypatch):
    monkeypatch.setattr(band_collab, "_council_members", lambda: [("sec", "a1"), ("priv", "a2")])
    out = band_collab._tally({"a1": ("ok", ""), "a2": ("ok", "")})
    assert out["verdict"] == "ok"


def test_council_members_parses_roster(monkeypatch):
    monkeypatch.setattr(band_collab, "BAND_COUNCIL",
                        "shepherd-security:u1, @shepherd-privacy:u2 ,bad-entry, :nohandle")
    members = band_collab._council_members()
    assert ("shepherd-security", "u1") in members
    assert ("shepherd-privacy", "u2") in members   # leading @ stripped
    assert all(h and a for h, a in members)         # malformed entries dropped
