"""
macOS permission preflight.

Agent S drives the machine through (a) pyautogui mouse/keyboard — needs
*Accessibility* — and (b) screenshots it reasons over — needs *Screen Recording*.
These are SEPARATE permissions. Missing Screen Recording is especially nasty: the
screenshot silently captures only the desktop + menu bar (every app window is
blanked), so the agent "can't see" any window and spins forever. This module
detects that up front and tells the user exactly what to do.
"""
import sys


def screen_recording_ok() -> "bool | None":
    """
    True/False if Screen Recording permission is granted; None if undetectable
    (non-macOS, or Quartz unavailable).
    """
    if sys.platform != "darwin":
        return None
    try:
        import Quartz
        return bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception:
        return None


def request_screen_recording() -> None:
    """Trigger the one-time macOS Screen Recording prompt (no-op if already asked)."""
    if sys.platform != "darwin":
        return
    try:
        import Quartz
        Quartz.CGRequestScreenCaptureAccess()
    except Exception:
        pass


def preflight() -> None:
    """Print a prominent warning at startup if Screen Recording isn't granted."""
    ok = screen_recording_ok()
    if ok is True or ok is None:
        return
    request_screen_recording()   # surface the system prompt the first time
    print("\n" + "═" * 64)
    print("  ⚠  SCREEN RECORDING PERMISSION NOT GRANTED")
    print("═" * 64)
    print("  Without it, screenshots capture only the desktop + menu bar —")
    print("  every app window is invisible to Agent S, so it cannot see Mail,")
    print("  the browser, or anything it opens, and will spin in place.")
    print("")
    print("  Fix: System Settings → Privacy & Security → Screen Recording")
    print("       → enable your terminal (Terminal / iTerm / VS Code),")
    print("       then FULLY QUIT and reopen it (required) and re-run.")
    print("═" * 64 + "\n")
