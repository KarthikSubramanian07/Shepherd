"""
The console logger renders per-step agent reasoning (the `agent.reasoning`
event emitted by the browser driver and the autonomous loop) to stdout.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telemetry.console_log import _fmt


def test_reasoning_line_includes_agent_turn_and_text():
    line = _fmt("agent.reasoning", {
        "agent_id": "agent-002", "turn": 3, "status": "continue",
        "reasoning": "click the destination field, then type the city",
        "ops": ["click Where to?", "type 'Tokyo'"],
    })
    assert "agent-002" in line
    assert "t3" in line
    assert "click the destination field" in line
    assert "click Where to?" in line and "type 'Tokyo'" in line


def test_reasoning_line_without_ops_or_text():
    line = _fmt("agent.reasoning", {"agent_id": "local", "turn": 0, "status": "done"})
    assert "local" in line and "t0" in line
    assert "(none)" in line          # empty reasoning rendered explicitly
    assert "plan:" not in line       # no ops line when there are no ops


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
