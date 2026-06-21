"""
Agent S adapter — wraps Simular gui-agents (AgentS3) as the LIVE execution planner.

Real API (gui-agents package):
  agent.predict(instruction, observation) -> (info, action_list)
  action_list[0] is a Python/pyautogui code string — call exec() on it.

Setup:
  uv add gui-agents
  .env:
    AGENT_S_ENGINE_TYPE = openai | anthropic   (default: openai)
    AGENT_S_MODEL       = gpt-4o               (default)
    OPENAI_API_KEY / ANTHROPIC_API_KEY
    UITARS_BASE_URL     = http://host:port      (optional — UI-TARS grounding endpoint)
                          leave empty to use the LLM for grounding (less accurate)
    SCREEN_WIDTH / SCREEN_HEIGHT               (from .env — match your display)

Click accuracy follows the FaceTimeOS pattern:
  - Screenshot resized to scaled logical dims before the model sees it
  - OSWorldACI width/height = pyautogui.size() (logical click space)
  - Click steps use generate_coords() + resize_coordinates() directly
"""
import io
import os
import sys
import time
from typing import Optional

# Map the host OS to a gui-agents platform tag so generated hotkeys are correct
# (e.g. ctrl+a/ctrl+v on Linux vs command+a on macOS).
_PLATFORM = {"linux": "linux", "darwin": "darwin", "win32": "windows"}.get(sys.platform, "linux")

from config import (
    AGENT_S_ENGINE_TYPE, AGENT_S_MODEL, AGENT_S_BASE_URL,
    UITARS_BASE_URL, UITARS_MODEL,
    SCREEN_WIDTH, SCREEN_HEIGHT,
    GEMINI_ENDPOINT_URL,
)
from shepherd_types import AutonomousStepResult
from engine.agent_s_grounding import (
    capture_observation,
    enrich_instruction,
    ground_pointer_code,
    grounding_target,
    normalize_agent_code,
    screen_geometry,
)

_TERMINAL_TOKENS = {"DONE", "FAIL", "FAILED", "WAIT"}


def _is_actionable(code: str) -> bool:
    c = (code or "").strip()
    if not c:
        return False
    if c.upper().strip(".") in _TERMINAL_TOKENS:
        return False
    return True


def _terminal_outcome(code: str) -> Optional[str]:
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
    def __init__(self) -> None:
        self._agent = None
        self._autonomous_agent = None
        # The model's reasoning for its most recent predict (gui-agents executor
        # info["plan"]). The engine reads this to log WHY each action ran.
        self.last_reasoning: str = ""
        # Running memory of the chained planner so it knows what it already did
        # (each direct vision call is otherwise stateless → it repeats itself).
        self._chain_history: list[str] = []
        self._grounding_agent = None
        self._geom = None
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

        _KEY_ENV = {
            "openai":    "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini":    "GEMINI_API_KEY",
        }
        api_key = os.getenv(_KEY_ENV.get(AGENT_S_ENGINE_TYPE, "OPENAI_API_KEY"))

        if AGENT_S_ENGINE_TYPE == "anthropic":
            # Newer Anthropic models reject `temperature`/`top_p`, which gui-agents
            # always sends — strip them so plan/ground calls don't 400.
            from engine._anthropic_compat import apply as _apply_anthropic_compat
            _apply_anthropic_compat()

        engine_params: dict = {
            "engine_type": AGENT_S_ENGINE_TYPE,
            "model":       AGENT_S_MODEL,
        }
        if api_key:
            engine_params["api_key"] = api_key
        # gui-agents' Gemini engine talks to Google's OpenAI-compatible endpoint,
        # so it needs a base_url (defaults to the Generative Language API).
        base_url = AGENT_S_BASE_URL or (
            GEMINI_ENDPOINT_URL if AGENT_S_ENGINE_TYPE == "gemini" else ""
        )
        if base_url:
            engine_params["base_url"] = base_url

        # FaceTimeOS: logical size for clicks, scaled size for model input
        self._geom = screen_geometry()
        g = self._geom

        if UITARS_BASE_URL:
            grounding_params: dict = {
                "engine_type":     "huggingface",
                "model":           UITARS_MODEL,
                "base_url":        UITARS_BASE_URL,
                "grounding_width": g.ground_w,
                "grounding_height": g.ground_h,
            }
            grounding_tag = f"UI-TARS @ {UITARS_BASE_URL}"
        else:
            grounding_params = dict(engine_params)
            grounding_params["grounding_width"] = g.ground_w
            grounding_params["grounding_height"] = g.ground_h
            grounding_tag = f"LLM grounding ({g.ground_w}×{g.ground_h}px → logical {g.logical_w}×{g.logical_h})"

        self._grounding_agent = OSWorldACI(
            env=None,
            platform=_PLATFORM,
            engine_params_for_generation=engine_params,
            engine_params_for_grounding=grounding_params,
            width=g.logical_w,
            height=g.logical_h,
        )

        self._agent = AgentS3(
            engine_params,
            self._grounding_agent,
            platform=_PLATFORM,
            max_trajectory_length=3,
            enable_reflection=False,
        )
        self._autonomous_agent = AgentS3(
            engine_params,
            self._grounding_agent,
            platform=_PLATFORM,
            max_trajectory_length=8,
            enable_reflection=True,
        )
        print(f"[agent_s] Ready — {AGENT_S_ENGINE_TYPE}/{AGENT_S_MODEL} + {grounding_tag}")

    def _observation(self) -> tuple[dict, object]:
        time.sleep(0.4)
        png, geom = capture_observation(self._geom)
        return {"screenshot": png}, geom

    @property
    def available(self) -> bool:
        return self._agent is not None

    def reset(self) -> None:
        if self._agent is None:
            return
        try:
            self._agent.reset()
            if self._autonomous_agent:
                self._autonomous_agent.reset()
        except Exception as e:
            print(f"[agent_s] reset failed (non-fatal): {e}")

    def reset_autonomous(self) -> None:
        """Reset the free-form goal state (long-trajectory agent + chain memory)."""
        self._chain_history = []
        if self._autonomous_agent is None:
            return
        try:
            self._autonomous_agent.reset()
        except Exception as e:
            print(f"[agent_s] autonomous reset failed (non-fatal): {e}")

    def _capture(self) -> bytes:
        """
        Grab the screen and downscale to the grounding size. Retina screenshots are
        ~4x logical resolution; downscaling keeps the Anthropic request under its
        size limit (avoids HTTP 413) and matches the image's pixel space to the
        coordinate space we ground in (SCREEN_WIDTH x SCREEN_HEIGHT).
        """
        import pyautogui
        screenshot = pyautogui.screenshot()
        if screenshot.size != (SCREEN_WIDTH, SCREEN_HEIGHT):
            from PIL import Image
            screenshot = screenshot.resize((SCREEN_WIDTH, SCREEN_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        screenshot.save(buf, format="PNG")
        return buf.getvalue()

    def plan_action(
        self,
        instruction: str,
        step_index: int,
        demonstration_context: str = "",
        *,
        action: str = "",
        type_text: Optional[str] = None,
    ) -> Optional[str]:
        if not self._agent or not self._grounding_agent:
            return None
        try:
            obs, geom = self._observation()
            act = (action or "").lower()

            # FaceTimeOS: dedicated grounding call for pointer actions
            if act in ("click", "double_click", "move"):
                target = grounding_target(instruction)
                code = ground_pointer_code(
                    self._grounding_agent, target, obs, action=act,
                )
                print(f"[agent_s] step {step_index}: grounded {act} → {target[:60]!r}")
                return code

            full_instruction = (
                enrich_instruction(action, instruction, type_text=type_text)
                if action else instruction
            )
            if demonstration_context:
                full_instruction = (
                    f"{full_instruction}\n\n"
                    f"Reference demonstration:\n{demonstration_context}"
                )

            info, action_out = self._agent.predict(
                instruction=full_instruction,
                observation=obs,
            )
            self.last_reasoning = (info or {}).get("plan", "") or ""

            if action_out and action_out[0]:
                code = action_out[0]
                if _is_actionable(code):
                    return normalize_agent_code(code, geom)
                print(
                    f"[agent_s] step {step_index}: non-actionable response "
                    f"'{code.strip()[:40]}' — using defined step"
                )

        except Exception as e:
            print(f"[agent_s] plan_action step {step_index} failed (using defined step): {e}")

        return None

    def _plan_chain(
        self, goal: str, step_index: int, memory_hint: str = "",
    ) -> Optional[AutonomousStepResult]:
        """
        Plan SEVERAL chained UI actions from one screenshot via a direct Claude
        vision call. Returns an AutonomousStepResult whose `code` is a multi-line
        pyautogui script (the engine exec()s the whole block in one turn), or None
        to fall back to single-action Agent S (no API key / unparseable response).
        """
        try:
            import base64
            import json
            from anthropic import Anthropic
            from config import AGENT_S_MODEL, ANTHROPIC_API_KEY, AUTONOMOUS_CHAIN_MAX

            if not ANTHROPIC_API_KEY:
                return None

            if self._chain_history:
                history = (
                    "Actions you have ALREADY performed in previous turns (do NOT "
                    "repeat them — they are done):\n"
                    + "\n".join(f"  turn {n}: {h}" for n, h in enumerate(self._chain_history))
                    + "\n\n"
                )
            else:
                history = "This is your first turn; nothing has been done yet.\n\n"

            b64 = base64.standard_b64encode(self._capture()).decode()
            prompt = (
                "You are an autonomous desktop agent on macOS pursuing this goal:\n"
                f"  {goal}\n"
                f"{memory_hint}\n"
                f"{history}"
                f"The screenshot is {SCREEN_WIDTH}x{SCREEN_HEIGHT}px (use absolute "
                "pixel coordinates from it). Plan the NEXT BATCH of UI actions that "
                "make progress toward the goal, chaining as many as you safely can "
                "in one go.\n\n"
                "Rules:\n"
                f"- Emit up to {AUTONOMOUS_CHAIN_MAX} actions. Chain actions that DON'T "
                "depend on a screen change you can't predict (typing, Tab between "
                "fields, hotkeys, opening an app, pressing Enter, short waits).\n"
                "- STOP the batch before any action whose target only appears after a "
                "previous action (e.g. a button in a window that hasn't opened yet) — "
                "you'll get a fresh screenshot next turn.\n"
                "- Prefer the keyboard (hotkeys, Tab, typing, the address bar) over "
                "clicking; it's far more reliable than pixel coordinates.\n"
                "- IGNORE any code editor, terminal, or console window in the screenshot "
                "(e.g. logs about an 'autonomous agent') — that is NOT part of your task; "
                "judge progress only from the actual application you're driving.\n"
                "- Do NOT repeat actions listed above as already performed. If the goal "
                "is already satisfied by them (e.g. the email compose window is open with "
                'its fields filled), return status "done" with empty actions.\n'
                "- Each action is one line of Python using only `pyautogui` and `time` "
                "(e.g. pyautogui.hotkey('command','space'); pyautogui.typewrite('Safari', interval=0.02); "
                "pyautogui.press('enter'); time.sleep(1); pyautogui.click(840, 220)).\n\n"
                'Return ONLY JSON: {"reasoning": "...", "status": "continue|done|fail", '
                '"actions": ["<python line>", ...]}\n'
                'Use status "done" the moment the goal is achieved, "fail" if it cannot be done.'
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
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end == -1 or end < start:
                return None
            plan = json.loads(raw[start:end + 1])

            self.last_reasoning = (plan.get("reasoning") or "").strip()
            status = (plan.get("status") or "continue").lower()
            actions = [a for a in (plan.get("actions") or []) if isinstance(a, str) and a.strip()]

            if status == "done":
                return AutonomousStepResult(outcome="done", raw="DONE")
            if status == "fail":
                return AutonomousStepResult(outcome="fail", raw=plan.get("reasoning") or "FAIL")
            if not actions:
                # continue but nothing to do → treat as a wait so the loop re-observes
                return AutonomousStepResult(outcome="wait", raw="WAIT")

            code = "\n".join(actions)
            # Remember what this turn did so the next turn won't repeat it.
            summary = self.last_reasoning or "; ".join(actions)
            self._chain_history.append(summary[:200])
            print(f"[agent_s] autonomous step {step_index}: chained {len(actions)} actions in one request")
            return AutonomousStepResult(outcome="action", code=code, raw=code)

        except Exception as e:
            print(f"[agent_s] chained planning step {step_index} failed (falling back): {e}")
            return None

    def predict_autonomous(
        self, goal: str, step_index: int, memory_hint: str = "",
    ) -> AutonomousStepResult:
        """
        One turn of free-form planning: screenshot + full goal instruction.

        memory_hint (optional) carries the milestone trail from a prior run of this
        goal, so the agent reuses what worked before. With AUTONOMOUS_CHAIN on, this
        plans SEVERAL chained UI actions per request (one screenshot → a multi-action
        pyautogui script). Falls back to single-action Agent S when chaining is off.
        """
        from config import AUTONOMOUS_CHAIN
        if AUTONOMOUS_CHAIN:
            chained = self._plan_chain(goal, step_index, memory_hint)
            if chained is not None:
                return chained
            # else: chained planner unavailable (no key / parse fail) → Agent S below

        agent = self._autonomous_agent or self._agent
        if not agent:
            return AutonomousStepResult(outcome="unavailable")

        try:
            obs, geom = self._observation()
            info, action = agent.predict(instruction=goal + memory_hint, observation=obs)
            self.last_reasoning = (info or {}).get("plan", "") or ""

            raw = (action[0] if action else "") or ""
            outcome = _terminal_outcome(raw)
            if outcome == "action":
                code = normalize_agent_code(raw, geom)
                return AutonomousStepResult(outcome="action", code=code, raw=raw)
            if outcome != "unavailable":
                print(f"[agent_s] autonomous step {step_index}: {outcome.upper()} — {raw.strip()[:60]}")
            return AutonomousStepResult(outcome=outcome, raw=raw or None)

        except Exception as e:
            print(f"[agent_s] predict_autonomous step {step_index} failed: {e}")
            return AutonomousStepResult(outcome="unavailable")

    def plan_workflow_chain(
        self,
        goal: str,
        step_no: int,
        instruction: str,
        next_label: str = "",
        options: Optional[list[dict]] = None,
        resolved: Optional[dict[str, str]] = None,
        missing: Optional[list[str]] = None,
    ) -> AutonomousStepResult:
        """One turn of a **dispatched workflow**, run through the same batched agentic
        loop a plain goal uses (design §0.1) — NOT a separate per-milestone grounding.

        The workflow is handed in as *background intent*: the current milestone
        instruction, the next milestone, and the outgoing edges / taught conditionals
        as NL clauses. In ONE Claude vision call the agent both plans a batch of UI
        actions AND self-routes — it returns `next` (the milestone to advance to,
        "SAME" to stay, or "END"), `branch` (the conditional `when` it matched), and
        any `extracted` KB — so advancing the graph costs zero extra round-trips
        (design §0.2). Returns an AutonomousStepResult carrying code + next/branch/
        extracted, or outcome="unavailable" when no API key (caller decides fallback).
        """
        options = options or []
        resolved = resolved or {}
        missing = missing or []
        try:
            import base64
            import json
            from anthropic import Anthropic
            from config import AGENT_S_MODEL, ANTHROPIC_API_KEY, AUTONOMOUS_CHAIN_MAX

            if not ANTHROPIC_API_KEY:
                return AutonomousStepResult(outcome="unavailable")

            if self._chain_history:
                history = (
                    "Actions you have ALREADY performed in previous turns (do NOT "
                    "repeat them — they are done):\n"
                    + "\n".join(f"  turn {n}: {h}" for n, h in enumerate(self._chain_history))
                    + "\n\n"
                )
            else:
                history = "This is your first turn; nothing has been done yet.\n\n"

            # Forward preview: each outgoing edge / taught conditional as an NL clause
            # the agent evaluates against the live screen, then names in `next`.
            opt_lines = []
            for o in options:
                key = o.get("key", "")
                label = o.get("label", "") or key
                when = o.get("when")
                do = o.get("do")
                if when:
                    do_txt = f" (do: {do})" if do else ""
                    opt_lines.append(
                        f'  - when {when} → next "{key}" — {label}{do_txt}')
                else:
                    opt_lines.append(f'  - (default) → next "{key}" — {label}')
            options_block = (
                "From this milestone you may go to one of these next milestones. "
                "Evaluate each `when` guard against what you SEE; when the actions you "
                "are taking satisfy/handle a guarded edge, emit that edge's target as "
                '`next`:\n' + "\n".join(opt_lines) + "\n\n"
                if opt_lines else
                'This is the last milestone — return `next`: "END" when done.\n\n'
            )

            resolved_block = (
                "Inputs available to you: "
                + ", ".join(f"{k}={v}" for k, v in resolved.items()) + "\n"
                if resolved else ""
            )
            missing_block = (
                f"Inputs you could NOT fill from memory (you may need to find them): "
                f"{missing}\n" if missing else ""
            )
            next_block = (
                f'After this milestone the workflow heads toward: "{next_label}".\n'
                if next_label else ""
            )

            b64 = base64.standard_b64encode(self._capture()).decode()
            prompt = (
                "You are an autonomous desktop agent executing a saved WORKFLOW on "
                f"macOS. Overall goal:\n  {goal}\n\n"
                f"CURRENT high-level milestone:\n  {instruction}\n\n"
                f"{next_block}{resolved_block}{missing_block}\n"
                f"{options_block}"
                f"{history}"
                f"The screenshot is {SCREEN_WIDTH}x{SCREEN_HEIGHT}px (use absolute "
                "pixel coordinates from it). Do the CURRENT milestone, chaining as many "
                "UI actions as you safely can in one go, THEN decide where to go next.\n\n"
                "Rules:\n"
                f"- Emit up to {AUTONOMOUS_CHAIN_MAX} actions. Chain actions that DON'T "
                "depend on a screen change you can't predict (typing, Tab between "
                "fields, hotkeys, pressing Enter, short waits).\n"
                "- STOP the batch before any action whose target only appears after a "
                "previous action — you'll get a fresh screenshot next turn.\n"
                "- Prefer the keyboard (hotkeys, Tab, typing, the address bar) over "
                "clicking; it's far more reliable than pixel coordinates.\n"
                "- IGNORE any code editor, terminal, or console window in the screenshot "
                "— that is NOT part of your task.\n"
                "- ROUTING: if the CURRENT milestone needs more turns, set `next` to "
                '"SAME". When it is complete, set `next` to the key of the milestone you '
                "chose from the list above (matching a `when` guard if one is true), or "
                '"END" if there are none. When you take a guarded edge, put that guard '
                "text in `branch`.\n"
                "- If you learned a value later milestones need (e.g. a projects "
                'summary), return it under "extracted" as {name: value}.\n\n'
                'Return ONLY JSON: {"reasoning": "...", "status": "continue|done|fail", '
                '"actions": ["<python line>", ...], "next": "<node key|SAME|END>", '
                '"branch": "<guard text or null>", "extracted": {}}\n'
                'Use status "done" only when the WHOLE workflow goal is achieved, '
                '"fail" if it cannot be done.'
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
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end == -1 or end < start:
                return AutonomousStepResult(outcome="unavailable")
            plan = json.loads(raw[start:end + 1])

            self.last_reasoning = (plan.get("reasoning") or "").strip()
            status = (plan.get("status") or "continue").lower()
            actions = [a for a in (plan.get("actions") or []) if isinstance(a, str) and a.strip()]
            nxt = (plan.get("next") or "SAME").strip()
            branch = plan.get("branch") or None
            extracted = {k: str(v) for k, v in (plan.get("extracted") or {}).items()
                         if isinstance(k, str)}

            if actions:
                summary = self.last_reasoning or "; ".join(actions)
                self._chain_history.append(summary[:200])
                print(f"[agent_s] workflow step {step_no}: chained {len(actions)} "
                      f"actions, next={nxt}")
                code = "\n".join(actions)
                return AutonomousStepResult(outcome="action", code=code, raw=code,
                                            next=nxt, branch=branch, extracted=extracted)

            if status == "fail":
                return AutonomousStepResult(outcome="fail", raw=plan.get("reasoning") or "FAIL",
                                            next="END", branch=branch, extracted=extracted)
            # No actions this turn (milestone already satisfied) → just route.
            outcome = "done" if status == "done" else "wait"
            return AutonomousStepResult(outcome=outcome, raw=self.last_reasoning or None,
                                        next=nxt, branch=branch, extracted=extracted)

        except Exception as e:
            print(f"[agent_s] workflow planning step {step_no} failed: {e}")
            return AutonomousStepResult(outcome="unavailable")

    def plan_batch_action(
        self,
        fields: list,
        step_index: int,
        demonstration_context: str = "",
    ) -> Optional[str]:
        if not self._agent:
            return None
        try:
            obs, geom = self._observation()

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

            info, action = self._agent.predict(instruction=instruction, observation=obs)
            self.last_reasoning = (info or {}).get("plan", "") or ""

            if action and action[0]:
                code = action[0]
                if _is_actionable(code):
                    print(f"[agent_s] batch_fill step {step_index}: planned {len(fields)} fields in one call")
                    return normalize_agent_code(code, geom)

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
