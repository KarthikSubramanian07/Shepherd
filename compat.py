"""
Environment compatibility shims, imported as early as possible.

On headless Linux (CI, containers) two things break at ``import pyautogui`` time:

1. **mouseinfo** — on Linux without ``_tkinter``, mouseinfo calls ``sys.exit()``
   (NOT ``ImportError``), which aborts the interpreter.  Even when tkinter *is*
   installed, mouseinfo accesses ``os.environ['DISPLAY']`` and raises ``KeyError``
   if no X display is set.

2. **pyautogui._pyautogui_x11** — does ``Display(os.environ['DISPLAY'])`` at
   module scope, which also raises ``KeyError`` on headless boxes.

We only ever use pyautogui for screenshot/click/type on machines with a real
display — never the interactive MouseInfo GUI — so on headless Linux we register
harmless stubs for *both* ``mouseinfo`` and ``pyautogui``, letting downstream
imports succeed.  Any actual GUI call at runtime will raise ``RuntimeError``.
"""
import os
import sys
import types


def _is_headless_linux() -> bool:
    return sys.platform.startswith("linux") and not os.environ.get("DISPLAY")


def _install_mouseinfo_stub() -> None:
    if "mouseinfo" in sys.modules:
        return

    if not _is_headless_linux():
        try:
            import tkinter  # noqa: F401  (real tkinter present → let mouseinfo load normally)
            return
        except Exception:
            pass

    stub = types.ModuleType("mouseinfo")

    def _unavailable(*_args, **_kwargs):
        raise RuntimeError(
            "MouseInfo GUI is unavailable (no tkinter); not needed for headless actuation."
        )

    stub.MouseInfoWindow = _unavailable
    stub.mouseInfo = _unavailable
    sys.modules["mouseinfo"] = stub


class _HeadlessPyAutoGUI(types.ModuleType):
    """Stub that satisfies ``import pyautogui`` on headless Linux."""

    FAILSAFE = True
    PAUSE = 0.3

    def __getattr__(self, name: str):
        def _guard(*_a, **_kw):
            raise RuntimeError(
                f"pyautogui.{name}() requires a display (no DISPLAY env var)."
            )
        return _guard


def _install_pyautogui_stub() -> None:
    if "pyautogui" in sys.modules:
        return
    if not _is_headless_linux():
        return
    sys.modules["pyautogui"] = _HeadlessPyAutoGUI("pyautogui")


_install_mouseinfo_stub()
_install_pyautogui_stub()
