"""
Human approval gate — blocks the engine thread at FLAG/HALT boundaries until
a human decision arrives from the dashboard, or the timeout expires.

Usage (engine side):
    from engine.approvals import request_approval, suggestions_for
    decision = request_approval(step_index, reason, timeout=30.0, default="halt")

Usage (server side):
    from engine.approvals import set_decision, set_override
    set_decision("approve")           # approve or halt
    set_override("use test creds")    # custom instruction → engine passes to Agent S
"""
import queue
import threading

# (decision, override_instruction, flag) — queue is race-free vs Event+global pattern.
# flag is the human's discretionary teaching gate: "one_off" | "save_as_rule".
_q: queue.SimpleQueue = queue.SimpleQueue()
_last_instruction: str = ""
_last_flag: str = "one_off"
_instr_lock = threading.Lock()

# Context-specific suggestions surfaced in the dashboard intervention panel.
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
        ("Retry this step",   "approve"),
        ("Skip and continue", "approve"),
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
    Drains any stale decisions left from a previous cycle first.
    """
    while not _q.empty():
        try:
            _q.get_nowait()
        except queue.Empty:
            break

    try:
        decision, instruction, flag = _q.get(timeout=timeout)
        with _instr_lock:
            global _last_instruction, _last_flag
            _last_instruction = instruction
            _last_flag = flag or "one_off"
        return decision
    except queue.Empty:
        return default


def set_decision(decision: str) -> None:
    """Called by the HTTP handler when the user clicks Approve or Halt."""
    _q.put((decision, "", "one_off"))


def set_override(instruction: str, flag: str = "one_off") -> None:
    """Called when a human submits a custom override instruction.

    flag = "save_as_rule" bakes the resolution into the workflow (teaching loop);
    "one_off" applies it for this run only.
    """
    _q.put(("override", instruction, flag if flag in ("one_off", "save_as_rule") else "one_off"))


def get_override_instruction() -> str:
    """Consume the pending override instruction (returns it once, then clears)."""
    global _last_instruction
    with _instr_lock:
        inst = _last_instruction
        _last_instruction = ""
    return inst


def get_override_flag() -> str:
    """Consume the pending override flag ("one_off" | "save_as_rule")."""
    global _last_flag
    with _instr_lock:
        flag = _last_flag
        _last_flag = "one_off"
    return flag
