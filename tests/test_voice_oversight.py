"""
Voice oversight — spoken approve/stop classification.

Safety-first: a reply containing any stop word resolves to halt even if it also
contains an approve word ("no, don't approve" must halt). Ambiguous replies
return None so the on-screen gate decides instead.
"""
from services.deepgram_input import classify_decision


def test_approve_words():
    for t in ("approve", "yes go ahead", "looks good, proceed", "Confirm"):
        assert classify_decision(t) == "approve", t


def test_halt_words():
    for t in ("stop", "halt it", "cancel that", "no", "abort", "do not send"):
        assert classify_decision(t) == "halt", t


def test_halt_beats_approve():
    # Safety-first: any stop word wins over an approve word in the same reply.
    assert classify_decision("no, don't approve that") == "halt"
    assert classify_decision("yes but actually stop") == "halt"


def test_ambiguous_is_none():
    assert classify_decision("hmm not sure") is None
    assert classify_decision("") is None
