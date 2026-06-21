"""
SimuLang compile-and-replay — the compiler turns a taught workflow into a
deterministic `.ts` script, and replay degrades gracefully when the CLI is absent.
"""
import os

from services import simulang_runner


def test_compile_workflow_emits_simulang_script(tmp_path, monkeypatch):
    monkeypatch.setattr(simulang_runner, "_ARTIFACT_DIR", str(tmp_path))
    wf = {
        "id": "WF_APPLY",
        "nodes": [
            {"kind": "open", "label": "Open the application form"},
            {"kind": "fill", "label": "Full name field", "value": "Ada Lovelace"},
            {"kind": "submit", "label": "Submit button"},
        ],
    }
    path = simulang_runner.compile_workflow(wf)
    assert path and os.path.exists(path)
    script = open(path).read()
    # Real SimuLang shape: a11y-tree driven, no pixels.
    assert "@simular-ai/simulang-js" in script
    assert "AccessibilityTree.fromPid" in script
    assert "tree.setValue" in script and "Ada Lovelace" in script   # the fill
    assert "tree.activate" in script                                # open + submit


def test_compile_handles_missing_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(simulang_runner, "_ARTIFACT_DIR", str(tmp_path))
    assert simulang_runner.compile_workflow({"id": None, "nodes": []}) is None


def test_replay_noop_without_cli(monkeypatch):
    # No simulang CLI on PATH -> replay returns None (caller uses Agent S replay).
    monkeypatch.setattr(simulang_runner, "available", lambda: False)
    assert simulang_runner.replay("WF_APPLY") is None
