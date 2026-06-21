"""
Per-request trace log — follow what happens on every intent AND the AI's reasoning.

Writes a clean, human-readable trace to BOTH the console and data/requests.log:

  ── REQUEST a1b2c3d4 ─────────────────────────────────────────
  intent  : "draft an email to sam"
  mode    : LIVE · freeform=True · task=FREEFORM::draft_an_email
  trail   : new (score 0.0)
  step 0  · agent_s thinking…
    reasoning: I see the desktop. I'll open Mail from the dock first…
    action  : pyautogui.click(112, 1048)
    result  : completed (412ms)
  …
  done    : completed · 6 steps · 18204ms
  graph   : Open email app → Compose → Generate text

The reasoning line is the model's own plan text (gui-agents executor `info["plan"]`),
so you can see WHY each action was chosen, not just what ran.
"""
import logging
import os
import sys
import textwrap

_LOG_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "requests.log")


def _build() -> logging.Logger:
    lg = logging.getLogger("shepherd.request")
    if lg.handlers:                      # already configured
        return lg
    lg.setLevel(logging.INFO)
    lg.propagate = False
    fmt = logging.Formatter("%(asctime)s │ %(message)s", "%H:%M:%S")
    try:
        fh = logging.FileHandler(_LOG_PATH)
        fh.setFormatter(fmt)
        lg.addHandler(fh)
    except Exception:
        pass                             # file logging is best-effort
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    lg.addHandler(sh)
    return lg


_log = _build()


def _short(rid: str) -> str:
    return (rid or "?")[:8]


def request_started(run_id: str, intent: str, mode: str, freeform: bool, task_key: str) -> None:
    _log.info("── REQUEST %s ─────────────────────────────────", _short(run_id))
    _log.info('  intent : "%s"', intent)
    _log.info("  mode   : %s · freeform=%s · task=%s", mode, freeform, task_key)


def trail(run_id: str, info: dict) -> None:
    _log.info("  trail  : %s (from %s, score %s, %s prior runs)",
              info.get("source"), info.get("from"), info.get("score"),
              info.get("run_count", 0))


def agent_turn(run_id: str, index: int, reasoning: str, code: str, signal: str = "") -> None:
    sig = f" [{signal}]" if signal else ""
    _log.info("  step %s · agent_s%s", index, sig)
    if reasoning:
        wrapped = textwrap.fill(
            " ".join(reasoning.split()), width=100,
            initial_indent="    reasoning: ", subsequent_indent="               ",
            max_lines=8, placeholder=" …")
        _log.info(wrapped)
    if code:
        lines = code.strip().splitlines()
        if lines:
            _log.info("    action : %s", lines[0])
            for ln in lines[1:]:
                _log.info("             %s", ln)


def step_result(run_id: str, index: int, status: str, duration_ms: int, error: str = "") -> None:
    if error:
        _log.info("    result : %s (%sms) — %s", status, duration_ms, error)
    else:
        _log.info("    result : %s (%sms)", status, duration_ms)


def note(run_id: str, message: str) -> None:
    _log.info("  · %s", message)


def request_finished(run_id: str, status: str, steps: int, duration_ms: int,
                     milestones: "list[str] | None" = None) -> None:
    _log.info("  done   : %s · %s steps · %sms", status.upper(), steps, duration_ms)
    if milestones:
        _log.info("  graph  : %s", " → ".join(milestones))
    _log.info("")
