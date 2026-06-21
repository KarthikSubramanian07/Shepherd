"""
Tests for the per-request medium text response (engine.run_summary).
Network-free: the LLM is stubbed on/off; the template path is deterministic.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine import run_summary as RS
from engine import llm

_ORIG_AVAIL = llm.available
_ORIG_COMPLETE = llm.complete


def teardown_function(_):
    llm.available = _ORIG_AVAIL
    llm.complete = _ORIG_COMPLETE


def test_template_fallback_completed():
    llm.available = lambda: False
    out = RS.summarize_run("draft an email about water", "completed",
                           ["Open Mail", "Type subject", "Send"])
    assert out.startswith("Completed the task")
    assert "draft an email about water" in out
    assert "3 step" in out


def test_template_fallback_failed_includes_error():
    llm.available = lambda: False
    out = RS.summarize_run("book a flight", "failed", ["Open site"], error="field not found")
    assert out.startswith("Couldn't complete")
    assert "field not found" in out


def test_uses_llm_when_available():
    llm.available = lambda: True
    llm.complete = lambda system, messages, **kw: "  I opened Mail and sent the email.  "
    out = RS.summarize_run("send an email", "completed", ["Open Mail", "Send"])
    assert out == "I opened Mail and sent the email."


def test_never_raises_on_empty():
    llm.available = lambda: False
    assert RS.summarize_run("", "aborted", []) != ""


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            teardown_function(None)
            print(f"ok  {name}")
    print("all passed")
