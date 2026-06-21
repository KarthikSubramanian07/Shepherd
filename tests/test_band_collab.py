"""
Band oversight collaboration — verdict parsing + graceful degradation.

Band is never load-bearing for safety: with no credentials it must be a no-op so
the oversight path falls back to the in-process verifier. And whatever phrasing
the verifier peer replies with, we must extract a valid verdict.
"""
from services import band_collab


def test_disabled_by_default_is_a_noop():
    # No BAND_* env set in the test environment → fully inert, never raises.
    assert band_collab.available() is False
    assert band_collab.request_verdict("credential field detected") is None
    band_collab.publish_event("run.start", "should not raise")  # no-op


def test_parse_verdict_extracts_halt():
    v = band_collab._parse_verdict("VERDICT: halt — credentials on screen")
    assert v["verdict"] == "halt"
    assert "credential" in v["explanation"].lower()
    assert v["model"] == "band:shepherd-verifier"


def test_parse_verdict_handles_ok_and_flag_and_prose():
    assert band_collab._parse_verdict("verdict: ok, false alarm")["verdict"] == "ok"
    assert band_collab._parse_verdict("My VERDICT: flag — unsure here")["verdict"] == "flag"


def test_parse_verdict_rejects_garbage():
    assert band_collab._parse_verdict("I cannot decide right now") is None
    assert band_collab._parse_verdict("") is None
