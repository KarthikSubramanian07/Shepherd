"""
MonitorAgent — policy-driven failure-mode detection.
Runs at high_stakes_steps boundaries ONLY — never inside a click sequence.

Detection layers (in order):
  1. Planted trigger  — 100% reliable for demo (from policy.yaml triggers map)
  2. Policy screen rules — configurable keyword matching against OCR text
  3. Stuck-state detection — screen hash unchanged for N seconds

The policy is loaded from data/policy.yaml at runtime — no restart needed.
Rule-based path is always-on. LLM verifier is a second-opinion layer in engine.py.
"""
import time
from typing import Optional
from shepherd_types import RoutineStep
from services.policy_engine import evaluate_trigger, evaluate_screen

_STUCK_THRESHOLD = 8.0
_last_hash: Optional[int] = None
_last_hash_time: float = 0.0


def check_step(step: RoutineStep, screen_state: dict = {}) -> dict:  # noqa: B006
    """
    Returns {"verdict": "ok"|"flag"|"halt", "reason": str}
    Called at high_stakes_steps boundaries. Never inside the click sequence.
    """
    # 1. Planted trigger — guaranteed demo reliability, policy-backed
    if step.monitor_trigger:
        return evaluate_trigger(step.monitor_trigger)

    # 2. Screenshot OCR → policy screen rules
    try:
        import pyautogui
        shot = pyautogui.screenshot()
        text = _ocr(shot)
        result = _check_screen(text, shot)
        if result:
            return result
    except Exception as e:
        print(f"[monitor] screenshot check failed (non-fatal): {e}")

    return {"verdict": "ok", "reason": ""}


def _ocr(screenshot) -> str:
    try:
        import pytesseract
        return pytesseract.image_to_string(screenshot).lower()
    except Exception:
        return ""


def _check_screen(text: str, screenshot=None) -> Optional[dict]:
    # Policy engine: configurable rules from policy.yaml
    result = evaluate_screen(text)
    if result:
        return result

    # Stuck state (not in policy.yaml — inherently dynamic)
    global _last_hash, _last_hash_time
    if screenshot is not None:
        try:
            h = hash(screenshot.tobytes())
            now = time.time()
            if h == _last_hash and (now - _last_hash_time) > _STUCK_THRESHOLD:
                _last_hash_time = now
                return {"verdict": "flag", "reason": "Stuck state: screen unchanged too long"}
            if h != _last_hash:
                _last_hash = h
                _last_hash_time = now
        except Exception:
            pass

    return None
