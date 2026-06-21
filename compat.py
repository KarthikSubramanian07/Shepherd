"""
Environment compatibility shims, imported as early as possible.

pyautogui imports `mouseinfo` at module load; on Linux without `_tkinter`,
mouseinfo calls `sys.exit()` (NOT ImportError), which aborts the interpreter and
defeats pyautogui's own try/except guard. We only ever use pyautogui for
screenshot/click/type (Xlib/scrot) — never the interactive MouseInfo GUI — so we
register a harmless stub when tkinter is unavailable, letting `import pyautogui`
succeed. (Installing python3-tk also fixes it; the stub keeps headless boxes working.)
"""
import sys
import types


def _install_mouseinfo_stub() -> None:
    if "mouseinfo" in sys.modules:
        return
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


_install_mouseinfo_stub()
