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
    SCREEN_WIDTH / SCREEN_HEIGHT               (from .env — match your display)
"""
import io
import os
from typing import Optional

from config import (
    AGENT_S_ENGINE_TYPE, AGENT_S_MODEL, AGENT_S_BASE_URL,
    UITARS_BASE_URL, UITARS_MODEL,
    SCREEN_WIDTH, SCREEN_HEIGHT,
)
from shepherd_types import AutonomousStepResult

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


def _terminal_outcome(code: str) -> Optional[str]:
    """Map Agent S control tokens to autonomous outcomes, or None if actionable code."""
    c = (code or "").strip()
    if not c:
        return "unavailable"
    upper = c.upper().strip(".")
    if upper == "DONE":
        return "done"
    if upper in ("FAIL", "FAILED"):
        return "fail"
    if upper == "WAIT":
        return "wait"
    if _is_actionable(code):
        return "action"
    return "unavailable"


class AgentSAdapter:
    """
    Thin wrapper around Simular AgentS3.
    plan_action() returns executable Python code (a string), or None to fall back
    to the routine's defined step. The engine exec()s the returned code directly.
    """

    def __init__(self) -> None:
        self._agent = None
        self._autonomous_agent = None
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
        # Longer trajectory + reflection for multi-step free-form goals
        self._autonomous_agent = AgentS3(
            engine_params,
            grounding_agent,
            platform="darwin",
            max_trajectory_length=8,
            enable_reflection=True,
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
            if self._autonomous_agent:
                self._autonomous_agent.reset()
        except Exception as e:
            print(f"[agent_s] reset failed (non-fatal): {e}")

    def reset_autonomous(self) -> None:
        """Reset only the long-trajectory agent used for free-form goals."""
        if self._autonomous_agent is None:
            return
        try:
            self._autonomous_agent.reset()
        except Exception as e:
            print(f"[agent_s] autonomous reset failed (non-fatal): {e}")

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

    def predict_autonomous(self, goal: str, step_index: int) -> AutonomousStepResult:
        """
        One turn of free-form planning: screenshot + full goal instruction.
        Agent S returns pyautogui code, or control tokens DONE / FAIL / WAIT.
        """
        agent = self._autonomous_agent or self._agent
        if not agent:
            return AutonomousStepResult(outcome="unavailable")

        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")

            _, action = agent.predict(
                instruction=goal,
                observation={"screenshot": buf.getvalue()},
            )

            raw = (action[0] if action else "") or ""
            outcome = _terminal_outcome(raw)
            if outcome == "action":
                return AutonomousStepResult(outcome="action", code=raw, raw=raw)
            if outcome != "unavailable":
                print(f"[agent_s] autonomous step {step_index}: {outcome.upper()} — {raw.strip()[:60]}")
            return AutonomousStepResult(outcome=outcome, raw=raw or None)

        except Exception as e:
            print(f"[agent_s] predict_autonomous step {step_index} failed: {e}")
            return AutonomousStepResult(outcome="unavailable")

    def plan_batch_action(
        self,
        fields: list,   # list of BatchField
        step_index: int,
        demonstration_context: str = "",
    ) -> Optional[str]:
        """
        Single Agent S call to fill multiple form fields at once.
        Returns one multi-line pyautogui code block covering all fields,
        or None to fall back to deterministic _dispatch(batch_fill).
        """
        if not self._agent:
            return None
        try:
            import pyautogui
            screenshot = pyautogui.screenshot()
            buf = io.BytesIO()
            screenshot.save(buf, format="PNG")

            field_lines = []
            for i, bf in enumerate(fields):
                label = bf.description or f"field {i + 1}"
                value = bf.text or "(skip)"
                field_lines.append(f"  {i + 1}. {label}: {value}")
            fields_desc = "\n".join(field_lines)

            instruction = (
                f"Fill these form fields using Tab to navigate between them:\n"
                f"{fields_desc}\n\n"
                f"Generate a single Python/pyautogui code block that fills ALL fields "
                f"in order. Use Tab to move between fields and clipboard paste for each value."
            )
            if demonstration_context:
                instruction += f"\n\nReference demonstration:\n{demonstration_context}"

            _, action = self._agent.predict(
                instruction=instruction,
                observation={"screenshot": buf.getvalue()},
            )

            if action and action[0]:
                code = action[0]
                if _is_actionable(code):
                    print(f"[agent_s] batch_fill step {step_index}: planned {len(fields)} fields in one call")
                    return code

        except Exception as e:
            print(f"[agent_s] plan_batch_action step {step_index} failed (using defined steps): {e}")

        return None

    def plan_batch_fill_mapping(
        self,
        fields: list,            # list of BatchField (with .description, resolved .text, .html_name)
        screenshot_png: bytes,
    ) -> Optional[list]:
        """
        LIVE-mode vision planning. Claude looks at the actual form screenshot and
        returns which value goes in which field — as structured JSON. The engine
        then actuates the plan via reliable JS injection (no keyboard focus needed).

        Returns: [{"html_name": str, "value": str}, ...]  or None to fall back to
        the routine's hardcoded field mapping.
        """
        try:
            import base64
            import json
            from anthropic import Anthropic
            from config import AGENT_S_MODEL, ANTHROPIC_API_KEY

            if not ANTHROPIC_API_KEY:
                return None

            spec = []
            for bf in fields:
                if not getattr(bf, "html_name", None) or not bf.text:
                    continue
                spec.append({
                    "field": bf.description or bf.html_name,
                    "html_name": bf.html_name,
                    "value": bf.text,
                })
            if not spec:
                return None

            b64 = base64.standard_b64encode(screenshot_png).decode()
            prompt = (
                "You are an oversight agent planning how to fill a web form you can see in "
                "the screenshot. Below is the intended data. Look at the form, match each "
                "value to the correct field, and return ONLY a JSON array of "
                '{"html_name": ..., "value": ...} objects in the order the fields appear on '
                "the form. Omit any field that is not actually present on the form. "
                "Do not include password or credential fields even if present.\n\n"
                f"Intended data:\n{json.dumps(spec, indent=2)}\n\n"
                "Return only the JSON array, no prose."
            )

            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=AGENT_S_MODEL,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {
                            "type": "base64", "media_type": "image/png", "data": b64,
                        }},
                        {"type": "text", "text": prompt},
                    ],
                }],
            )

            raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()
            # Extract the JSON array even if Claude wraps it in prose or markdown fences.
            start, end = raw.find("["), raw.rfind("]")
            if start == -1 or end == -1 or end < start:
                return None
            plan = json.loads(raw[start:end + 1])
            if isinstance(plan, list) and plan:
                print(f"[agent_s] batch_fill: Claude planned {len(plan)} fields from the screenshot")
                return [
                    {"html_name": p["html_name"], "value": p["value"]}
                    for p in plan
                    if "html_name" in p and "value" in p
                ]

        except Exception as e:
            print(f"[agent_s] plan_batch_fill_mapping failed (using hardcoded fields): {e}")

        return None
