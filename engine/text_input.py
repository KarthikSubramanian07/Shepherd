"""Reliable keyboard text entry on macOS (pyautogui cmd+v often drops the modifier → bare 'v')."""
from __future__ import annotations

import subprocess
import sys
import time

import pyautogui

# AppleScript key names for non-character keys
_AS_KEY: dict[str, str] = {
    "return": "return",
    "enter": "return",
    "tab": "tab",
    "escape": "escape",
    "esc": "escape",
    "space": "space",
    "/": "/",
}


def enter_text(text: str) -> None:
    """
    Type `text` into the focused field.

    Simple ASCII (search queries, names): pyautogui.write — no clipboard.
    Everything else: pbcopy + AppleScript Cmd+V (pyautogui hotkey drops cmd on macOS).
    """
    if not text:
        return

    time.sleep(0.08)

    if _can_direct_write(text):
        pyautogui.write(text, interval=0.03)
        return

    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.1)
    subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to keystroke "v" using command down'],
        check=False,
    )


def hotkey(keys: list[str]) -> None:
    """Press a key combo; uses AppleScript on macOS when modifiers are involved."""
    if not keys:
        return

    if sys.platform == "darwin" and _needs_applescript_hotkey(keys):
        _hotkey_applescript(keys)
        return

    if len(keys) == 1:
        pyautogui.press(keys[0])
        return

    pyautogui.hotkey(*keys)


def _can_direct_write(text: str) -> bool:
    return text.isascii() and all(c.isprintable() or c == "\t" for c in text)


def _needs_applescript_hotkey(keys: list[str]) -> bool:
    mods = {"cmd", "command", "ctrl", "control", "shift", "alt", "option", "win"}
    return any(k.lower() in mods for k in keys)


def _hotkey_applescript(keys: list[str]) -> None:
    mods: list[str] = []
    key = keys[-1]
    for k in keys[:-1]:
        lk = k.lower()
        if lk in ("cmd", "command"):
            mods.append("command down")
        elif lk == "shift":
            mods.append("shift down")
        elif lk in ("alt", "option"):
            mods.append("option down")
        elif lk in ("ctrl", "control"):
            mods.append("control down")

    ks = _AS_KEY.get(key.lower(), key)
    if len(ks) == 1:
        stroke = f'keystroke "{ks}"'
    else:
        stroke = f"keystroke {ks}"

    if mods:
        script = f'tell application "System Events" to {stroke} using {{{", ".join(mods)}}}'
    else:
        script = f'tell application "System Events" to {stroke}'

    subprocess.run(["osascript", "-e", script], check=False)
