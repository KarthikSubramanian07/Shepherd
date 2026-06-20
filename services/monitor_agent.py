"""
MonitorAgent — rule-based failure-mode detection.
Runs at high_stakes_steps boundaries ONLY — never inside a click sequence.

Detects:
  - Planted trigger  (100% reliable for demo — always use this for the demo beat)
  - Credential/password field
  - CAPTCHA
  - Phishing/prompt-injection text
  - Stuck state (no screen change for N seconds)

Rule-based path is always-on. LLM upgrade is optional, gated, never the sole path.
"""
import time
from typing import Optional
from shepherd_types import RoutineStep

_STUCK_THRESHOLD = 8.0  # seconds without screen change
_last_hash: Optional[int] = None
_last_hash_time: float = 0.0


def check_step(step: RoutineStep, screen_state: dict) -> dict:
    """
    Returns {"verdict": "ok"|"flag"|"halt", "reason": str}
    Called at high_stakes_steps boundaries. Never inside the click sequence.
    """
    # 1. Planted trigger — guaranteed demo reliability
    if step.monitor_trigger:
        return _planted(step.monitor_trigger)

    # 2. Screenshot OCR rule checks
    try:
        import pyautogui
        shot = pyautogui.screenshot()
        text = _ocr(shot)
        result = _rules(text, shot)
        if result:
            return result
    except Exception as e:
        print(f"[monitor] screenshot check failed (non-fatal): {e}")

    return {"verdict": "ok", "reason": ""}


def _planted(trigger: str) -> dict:
    """100% reliable detection for planted demo triggers."""
    MAP = {
        "credential": {
            "verdict": "halt",
            "reason":  "Credential / password field detected — halting to protect sensitive data",
        },
        "captcha": {
            "verdict": "halt",
            "reason":  "CAPTCHA detected — human verification required",
        },
        "phishing": {
            "verdict": "halt",
            "reason":  "Possible prompt injection or phishing content detected",
        },
        "stuck": {
            "verdict": "flag",
            "reason":  "Possible stuck state — no screen change detected",
        },
    }
    return MAP.get(trigger, {"verdict": "flag", "reason": f"Unknown trigger: {trigger}"})


def _ocr(screenshot) -> str:
    try:
        import pytesseract
        return pytesseract.image_to_string(screenshot).lower()
    except Exception:
        return ""


def _rules(text: str, screenshot=None) -> Optional[dict]:
    credential_patterns = [
        "password", "enter password", "confirm password",
        "api key", "secret key", "access token",
    ]
    captcha_patterns = [
        "captcha", "i'm not a robot", "verify you are human", "recaptcha",
    ]
    injection_patterns = [
        "ignore previous instructions", "disregard above",
        "you are now", "pretend you are",
    ]

    for p in credential_patterns:
        if p in text:
            return {"verdict": "halt", "reason": f"Credential field detected: '{p}'"}

    for p in captcha_patterns:
        if p in text:
            return {"verdict": "halt", "reason": f"CAPTCHA detected: '{p}'"}

    for p in injection_patterns:
        if p in text:
            return {"verdict": "halt", "reason": f"Possible prompt injection: '{p}'"}

    # Stuck state
    global _last_hash, _last_hash_time
    if screenshot is not None:
        try:
            h = hash(screenshot.tobytes())
            now = time.time()
            if h == _last_hash and (now - _last_hash_time) > _STUCK_THRESHOLD:
                return {"verdict": "flag", "reason": "Stuck state: screen unchanged too long"}
            if h != _last_hash:
                _last_hash = h
                _last_hash_time = now
        except Exception:
            pass

    return None
