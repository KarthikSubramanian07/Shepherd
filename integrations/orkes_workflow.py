"""
Orkes Agentspan — VERIFY Saturday.
Track judged on Agentspan (NOT Conductor workflows).
Build only if Agentspan maps cleanly to wrapping the agent run; otherwise drop entirely.
"""
from config import FEATURES


def wrap_agent_run(fn, *args, **kwargs):
    if not FEATURES["orkes"]:
        return fn(*args, **kwargs)
    # VERIFY: Agentspan SDK import + instrumentation pattern at event
    print("[orkes] Agentspan: VERIFY API Saturday before enabling.")
    return fn(*args, **kwargs)
