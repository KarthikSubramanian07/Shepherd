"""
Agent S adapter — wraps Simular gui-agents (AgentS3) as the LIVE execution planner.

Real API (gui-agents package):
  agent.predict(instruction, observation) -> (info, action_list)
  action_list[0] is a Python/pyautogui code string — call exec() on it.
  The engine does NOT dispatch through _dispatch(); it exec()s the code directly.

Setup:
  uv add gui-agents
  .env:
    AGENT_S_ENGINE_TYPE = openai | anthropic   (default: openai)
    AGENT_S_MODEL       = gpt-4o               (default)
    OPENAI_API_KEY / ANTHROPIC_API_KEY
    UITARS_BASE_URL     = http://host:port      (optional — UI-TARS grounding endpoint)
                          leave empty to use the LLM for grounding (less accurate)
    SCREEN_WIDTH / SCREEN_HEIGHT               (default: 1920 × 1080)
"""
import io
import os
from typing import Optional

from config import (
    AGENT_S_ENGINE_TYPE, AGENT_S_MODEL, AGENT_S_BASE_URL,
    UITARS_BASE_URL, UITARS_MODEL,
    SCREEN_WIDTH, SCREEN_HEIGHT,
)

# Control tokens Agent S returns instead of pyautogui code. These are NOT
# executable — exec()'ing them would NameError and (wrongly) fail the step — so
# the adapter reports "no actionable code" and the engine falls back to the
# routine's deterministic defined step.
_TERMINAL_TOKENS = {"DONE", "FAIL", "FAILED", "WAIT"}


def _is_actionable(code: str) -> bool:
    """True only if `code` looks like executable action code, not a control token."""
    c = (code or "").strip()
    if not c:
        return False
    if c.upper().strip(".") in _TERMINAL_TOKENS:
        return False
    return True


class AgentSAdapter:
    """
    Thin wrapper around Simular AgentS3.
    plan_action() returns executable Python code (a string), or None to fall back
    to the routine's defined step. The engine exec()s the returned code directly.
    """

    def __init__(self) -> None:
        self._agent = None
        try:
            self._init_agent()
        except ImportError as e:
            print(f"[agent_s] gui-agents not installed: {e}")
            print("[agent_s] Install: uv add gui-agents")
        except Exception as e:
            print(f"[agent_s] Init failed — LIVE mode will run defined steps: {e}")

    def _init_agent(self) -> None:
        from gui_agents.s3.agents.agent_s import AgentS3
        from gui_agents.s3.agents.grounding import OSWorldACI

        api_key = (
            os.getenv("OPENAI_API_KEY")
            if AGENT_S_ENGINE_TYPE == "openai"
            else os.getenv("ANTHROPIC_API_KEY")
        )

        engine_params: dict = {
            "engine_type": AGENT_S_ENGINE_TYPE,
            "model":       AGENT_S_MODEL,
        }
        if api_key:
            engine_params["api_key"] = api_key
        if AGENT_S_BASE_URL:
            engine_params["base_url"] = AGENT_S_BASE_URL

        # Grounding: UI-TARS endpoint if provided, else fall back to same LLM
        if UITARS_BASE_URL:
            grounding_params: dict = {
                "engine_type":       "huggingface",
                "model":             UITARS_MODEL,
                "base_url":          UITARS_BASE_URL,
                "grounding_width":   SCREEN_WIDTH,
                "grounding_height":  SCREEN_HEIGHT,
            }
            grounding_tag = f"UI-TARS @ {UITARS_BASE_URL}"
        else:
            # LLM-only grounding — works without a local model server
            grounding_params = dict(engine_params)
            grounding_params["grounding_width"] = SCREEN_WIDTH
            grounding_params["grounding_height"] = SCREEN_HEIGHT
            grounding_tag = "LLM grounding (no UI-TARS)"

        grounding_agent = OSWorldACI(
            env=None,
            platform="darwin",
            engine_params_for_generation=engine_params,
            engine_params_for_grounding=grounding_params,
            width=SCREEN_WIDTH,
            height=SCREEN_HEIGHT,
        )

        self._agent = AgentS3(
            engine_params,
            grounding_agent,
            platform="darwin",
            max_trajectory_length=3,
            enable_reflection=False,
        )
        print(
            f"[agent_s] Ready — {AGENT_S_ENGINE_TYPE}/{AGENT_S_MODEL} "
            f"+ {grounding_tag}"
        )

    @property
    def available(self) -> bool:
        return self._agent is not None

    def reset(self) -> None:
        """
        Clear Agent S trajectory / reflection state. Must be called at the start of
        each run — AgentS3 keeps per-task internal state (max_trajectory_length,
        enable_reflection) that would otherwise leak across unrelated runs.
        Safe no-op when Agent S is unavailable.
        """
        if self._agent is None:
            return
        try:
            self._agent.reset()
        except Exception as e:
            print(f"[agent_s] reset failed (non-fatal): {e}")

    def plan_action(
        self,
        instruction: str,
        step_index: int,
        demonstration_context: str = "",
    ) -> Optional[str]:
        """
        Capture current screen, ask Agent S what to do next.

        Returns:
            Python/pyautogui code string to exec(), e.g.:
              "pyautogui.click(760, 300)\\npyautogui.typewrite('hello')"
            None on failure — engine falls back to the routine's defined step.
        """
        if not self._agent:
            return None
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")

            full_instruction = instruction
            if demonstration_context:
                full_instruction = (
                    f"{instruction}\n\n"
                    f"Reference demonstration:\n{demonstration_context}"
                )

            _, action = self._agent.predict(
                instruction=full_instruction,
                observation={"screenshot": buf.getvalue()},
            )

            if action and action[0]:
                code = action[0]
                if _is_actionable(code):
                    return code
                # Terminal/control token (DONE / WAIT / FAIL …) — nothing to actuate.
                print(
                    f"[agent_s] step {step_index}: non-actionable response "
                    f"'{code.strip()[:40]}' — using defined step"
                )

        except Exception as e:
            print(f"[agent_s] plan_action step {step_index} failed (using defined step): {e}")

        return None
