"""
Regression test for the autonomous agent code executor.

Agent S (and the batched autonomous planner) routinely emit *bare* action calls
like ``hotkey('ctrl','l')`` / ``press('enter')`` / ``click(760, 300)`` instead of
the documented ``pyautogui.`` prefixed form. The exec namespace previously only
bound ``pyautogui``/``time``/``activate_app``/``type_text``, so a bare call raised
``NameError: name 'hotkey' is not defined`` and aborted the run mid-step (observed
live during the AUTONOMOUS e2e). ``_exec_agent_code`` now binds an allowlist of
mouse/keyboard action verbs as top-level names so both forms execute identically.
"""

import pytest

import engine.engine as eng_mod
from engine.engine import ShepherdExecutionEngine


class _Recorder:
    """Stand-in for pyautogui that records calls instead of driving the GUI."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name):
        def _fn(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _fn


# _exec_agent_code never touches `self`, so we can drive it with a bare object().
def _run(code: str, monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr(eng_mod, "pyautogui", rec)
    ShepherdExecutionEngine._exec_agent_code(object(), code)
    return rec


def test_bare_pyautogui_verbs_do_not_raise(monkeypatch):
    code = (
        "hotkey('ctrl', 'l')\n"
        "press('enter')\n"
        "click(10, 20)\n"
        "doubleClick(30, 40)\n"
        "typewrite('hello')\n"
        "write('world')\n"
        "scroll(-3)\n"
        "moveTo(5, 5)\n"
        "sleep(0)\n"
    )
    rec = _run(code, monkeypatch)
    names = [c[0] for c in rec.calls]
    for verb in ("hotkey", "press", "click", "doubleClick", "typewrite", "write", "scroll", "moveTo"):
        assert verb in names, f"bare {verb}() was not dispatched"


def test_prefixed_pyautogui_calls_still_work(monkeypatch):
    rec = _run("pyautogui.hotkey('ctrl', 'a')\npyautogui.click(1, 2)", monkeypatch)
    assert ("hotkey", ("ctrl", "a"), {}) in rec.calls
    assert ("click", (1, 2), {}) in rec.calls


def test_bare_and_prefixed_dispatch_identically(monkeypatch):
    bare = _run("hotkey('ctrl', 'l')", monkeypatch)
    prefixed = _run("pyautogui.hotkey('ctrl', 'l')", monkeypatch)
    assert bare.calls == prefixed.calls == [("hotkey", ("ctrl", "l"), {})]


def test_blocking_dialogs_not_exposed_as_bare_names(monkeypatch):
    # alert/confirm/prompt are deliberately kept out of the bare allowlist so a
    # stray bare call can't pop a modal that stalls the run; a bare alert() must
    # raise NameError rather than silently opening a dialog.
    for dialog in ("alert", "confirm", "prompt"):
        rec = _Recorder()
        monkeypatch.setattr(eng_mod, "pyautogui", rec)
        with pytest.raises(NameError):
            ShepherdExecutionEngine._exec_agent_code(object(), f"{dialog}('x')")
        assert rec.calls == []
    # ...but the explicit pyautogui.-prefixed form is still reachable.
    rec = _run("pyautogui.alert('x')", monkeypatch)
    assert ("alert", ("x",), {}) in rec.calls
