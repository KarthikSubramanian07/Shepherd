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


class AccessibilityDenied(RuntimeError):
    """Raised at actuation time when macOS Accessibility permission is missing.

    pyautogui/osascript silently no-op without this permission, so we raise this
    explicitly the moment Agent S tries to drive the mouse/keyboard — turning an
    invisible failure into a failed step that surfaces to Sentry.
    """


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


def accessibility_ok() -> "bool | None":
    """
    True/False if Accessibility (synthetic input) permission is granted; None if
    undetectable (non-macOS, or pyobjc unavailable).

    This is the permission pyautogui needs to post mouse/keyboard events. Without
    it, ``pyautogui.click()`` and ``osascript ... keystroke`` SILENTLY no-op (no
    exception, no error), so steps log as "completed" while nothing happens on
    screen. We detect it via the same API pyautogui relies on, AXIsProcessTrusted.
    """
    if sys.platform != "darwin":
        return None
    try:
        from ApplicationServices import AXIsProcessTrusted
        return bool(AXIsProcessTrusted())
    except Exception:
        return None


def preflight() -> None:
    """Print a prominent warning at startup for any missing actuation permission."""
    if screen_recording_ok() is False:
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

    if accessibility_ok() is False:
        print("\n" + "═" * 64)
        print("  ⚠  ACCESSIBILITY PERMISSION NOT GRANTED")
        print("═" * 64)
        print("  Without it, mouse clicks and keystrokes SILENTLY do nothing —")
        print("  macOS drops the synthetic events and pyautogui raises no error,")
        print("  so steps log as 'completed' while nothing happens on screen.")
        print("")
        print("  Fix: System Settings → Privacy & Security → Accessibility")
        print("       → enable your terminal (Terminal / iTerm / VS Code),")
        print("       then FULLY QUIT and reopen it (required) and re-run.")
        print("═" * 64 + "\n")
