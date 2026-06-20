"""
Agent S adapter — wraps Simular's Agent S as the LIVE execution planner.

VERIFY at event:
  git clone https://github.com/simular-ai/Agent-S
  Check the actual import path and API surface in that repo.
  Then fill in the __init__ and plan_action methods below.

Jean (Lane A) owns this file.
The engine calls adapter.plan_action() in LIVE mode; falls back to the
defined step if Agent S is unavailable or returns None.
"""
from typing import Optional


class AgentSAdapter:
    """
    Thin wrapper around Simular Agent S.
    Returns None from plan_action() to fall back to the routine's defined step.
    """

    def __init__(self) -> None:
        self._agent = None
        try:
            # VERIFY: actual import path from Simular's Agent-S repo.
            # Common candidates (check at event):
            #   from gui_agents.s2.agents.agent_s import AgentS
            #   from agent_s import AgentS
            #   from simular_agent_s import AgentS
            # Example init (VERIFY kwargs):
            #   self._agent = AgentS(
            #       model="gpt-4o",
            #       grounding_model="osatlas-7b",
            #   )
            raise ImportError("VERIFY Agent S import path at event — see comment above")
        except ImportError as e:
            print(f"[agent_s] Not available — LIVE mode runs defined steps: {e}")

    @property
    def available(self) -> bool:
        return self._agent is not None

    def plan_action(
        self,
        instruction: str,
        step_index: int,
        demonstration_context: str = "",
    ) -> Optional[dict]:
        """
        Given a step instruction and optional demonstration context, ask Agent S
        what action to take. Returns a dict with keys matching RoutineStep fields
        (action, target, text, keys, seconds), or None to use the routine's
        pre-defined step as-is.

        VERIFY: actual Agent S planning method signature.
        Example (check at event):
          result = self._agent.run(instruction=instruction)
          return {"action": "click", "target": result.target, ...}
        """
        if not self._agent:
            return None
        try:
            # VERIFY: replace with real Agent S call
            result = self._agent.run(instruction=instruction)  # VERIFY method name + args
            return {
                "action": result.action_type,   # VERIFY field names
                "target": getattr(result, "target", None),
                "text":   getattr(result, "text", None),
                "keys":   getattr(result, "keys", None),
            }
        except Exception as e:
            print(f"[agent_s] plan_action failed (falling back to defined step): {e}")
            return None
