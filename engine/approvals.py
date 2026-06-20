"""
Human approval gate — blocks the engine thread at FLAG/HALT boundaries until
a human decision arrives from the dashboard, or the timeout expires.

Usage (engine side):
    from engine.approvals import request_approval, suggestions_for
    decision = request_approval(step_index, reason, timeout=30.0, default="halt")

Usage (server side):
    from engine.approvals import set_decision
    set_decision("approve")  # or "halt" or "override:<instruction>"
"""
import threading

_pending = threading.Event()
_decision: str = "approve"

# Context-specific suggestions surfaced in the dashboard intervention panel.
# Each entry: (label shown on the chip, decision sent to engine).
_SUGGESTIONS: dict[str, list[tuple[str, str]]] = {
    "credential": [
        ("Use test credentials", "approve"),
        ("Skip this step",       "approve"),
    ],
    "captcha": [
        ("Solve manually → approve", "approve"),
        ("Skip captcha step",        "approve"),
    ],
    "phishing": [
        ("Trust and continue", "approve"),
        ("Halt — safety risk", "halt"),
    ],
    "stuck": [
        ("Retry this step",     "approve"),
        ("Skip and continue",   "approve"),
    ],
}


def suggestions_for(trigger: str) -> list[dict]:
    """Return [{label, action}] chips for this trigger type."""
    return [
        {"label": lbl, "action": act}
        for lbl, act in _SUGGESTIONS.get(trigger or "", [])
    ]


def request_approval(
    step_index: int,
    reason: str,
    timeout: float = 30.0,
    default: str = "approve",
) -> str:
    """
    Block the calling (engine) thread until a human decision arrives or
    `timeout` seconds elapse. Returns the decision string.

    default — what to return on timeout: "approve" for FLAG steps,
              "halt" for HALT steps (so silence = conservative choice).
    """
    global _decision
    _decision = default
    _pending.clear()
    _pending.wait(timeout=timeout)
    return _decision


def set_decision(decision: str) -> None:
    """Called by the HTTP handler when the user clicks a button in the dashboard."""
    global _decision
    _decision = decision
    _pending.set()
