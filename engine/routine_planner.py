"""
Draft a routines.json-style step list from a free-form goal before Agent S executes it.

Uses Anthropic or OpenAI for text-only JSON drafting (config: PLANNER_*).
Agent S execution uses separate AGENT_S_* settings.
"""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from config import (
    ANTHROPIC_API_KEY,
    AUTONOMOUS_MAX_STEPS,
    AUTONOMOUS_PLAN_MAX_STEPS,
    OPENAI_API_KEY,
    PLANNER_ENGINE_TYPE,
    PLANNER_MODEL,
    AGENT_S_BASE_URL,
)
from shepherd_types import AUTONOMOUS_ROUTINE_ID, BatchField, RoutineDefinition, RoutineStep

_ALLOWED_ACTIONS = frozenset({
    "move", "click", "double_click", "type", "hotkey",
    "open_app", "wait", "browser", "batch_fill",
})

_STEP_FIELDS = frozenset({
    "action", "target", "text", "keys", "seconds",
    "browser_step", "monitor_trigger", "description",
})

_EXAMPLE_ROUTINE = """
{
  "description": "Open TextEdit and type a note",
  "variables": ["NOTE_TEXT"],
  "steps": [
    {"action": "open_app", "target": "TextEdit", "description": "Open TextEdit"},
    {"action": "wait", "seconds": 2.0, "description": "Wait for app to launch"},
    {"action": "hotkey", "keys": ["cmd", "n"], "description": "New document"},
    {"action": "type", "text": "{NOTE_TEXT}\\n", "description": "Type the note text"},
    {"action": "hotkey", "keys": ["cmd", "s"], "description": "Save document"}
  ]
}
""".strip()

_SYSTEM_PROMPT = f"""You are a desktop automation planner for macOS.
Given a user goal, output a JSON object with the same schema as Shepherd routines.

Rules:
- Break the goal into {AUTONOMOUS_PLAN_MAX_STEPS} or fewer coarse steps (not individual keystrokes).
- Use only these actions: {", ".join(sorted(_ALLOWED_ACTIONS))}.
- Each step MUST have a clear "description" — Agent S reads this to plan pyautogui code.
- Use "open_app" + "wait" before interacting with a new application.
- For URLs use open_app with "text" (not "url"), e.g. {{"action":"open_app","target":"Google Chrome","text":"https://mail.google.com/mail/u/0/#inbox"}}.
- For Gmail: prefer hotkey ["c"] to open Compose (inbox must be focused) instead of clicking the Compose button.
- For YouTube: prefer hotkey ["/"] to focus the search bar instead of clicking it; put the query in type step "text" as {{SEARCH_QUERY}}.
- For click steps: describe the element visually (label text, color, screen region, nearby elements).
- Put recipient/subject/body in type step "text" using {{RECIPIENT_EMAIL}}, {{EMAIL_SUBJECT}}, {{EMAIL_BODY}} placeholders.
- Use "hotkey" for keyboard shortcuts; "wait" for pauses (seconds field).
- Put dynamic text in "text" fields using {{VARIABLE}} placeholders.
- List every variable name used in a top-level "variables" array.
- Prefer simple, reliable steps over overly granular ones.
- Do NOT include markdown fences or commentary — raw JSON only.

Example:
{_EXAMPLE_ROUTINE}
"""


def _extract_variables(goal: str) -> dict[str, str]:
    """Pull concrete values from the user goal for type-step substitution."""
    variables: dict[str, str] = {"GOAL": goal}
    email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", goal)
    if email:
        variables["RECIPIENT_EMAIL"] = email.group(0)
    lower = goal.lower()
    if "test" in lower:
        variables.setdefault("EMAIL_SUBJECT", "Test")
        variables.setdefault("EMAIL_BODY", "This is a test email sent by Shepherd.")
    # "play crazy frog on youtube" → SEARCH_QUERY
    for pat in (
        r"play\s+(.+?)\s+on\s+youtube",
        r"search\s+(?:for\s+)?(.+?)\s+on\s+youtube",
        r"youtube\s+(?:search\s+)?(?:for\s+)?(.+)",
    ):
        m = re.search(pat, goal, re.I)
        if m:
            variables["SEARCH_QUERY"] = m.group(1).strip().strip("'\"")
            break
    return variables


def _fill_type_steps(steps: list[RoutineStep], extracted: dict[str, str]) -> None:
    """Ensure type steps have text when the planner only wrote a description."""
    for step in steps:
        if step.action != "type" or step.text:
            continue
        desc = (step.description or "").lower()
        if "recipient" in desc or "to field" in desc:
            step.text = "{RECIPIENT_EMAIL}"
        elif "subject" in desc:
            step.text = "{EMAIL_SUBJECT}"
        elif "body" in desc or "message" in desc:
            step.text = "{EMAIL_BODY}"
        elif "search" in desc or "query" in desc:
            step.text = "{SEARCH_QUERY}"


def _optimize_planned_steps(
    steps: list[RoutineStep], goal: str, extracted: dict[str, str],
) -> list[RoutineStep]:
    """
    Prefer reliable shortcuts over fragile vision clicks where we know them.
    Ensures Gmail opens at the inbox URL when the goal mentions email/Gmail.
    """
    lower = goal.lower()
    gmail_task = "gmail" in lower or ("email" in lower and "youtube" not in lower)
    youtube_task = "youtube" in lower
    optimized: list[RoutineStep] = []
    after_gmail_open = False

    for step in steps:
        if (
            gmail_task
            and step.action == "open_app"
            and not step.target
        ):
            step.target = "Google Chrome"

        if step.action == "open_app" and gmail_task:
            if not step.text or "mail.google" not in step.text.lower():
                step.text = "https://mail.google.com/mail/u/0/#inbox"
            step.target = step.target or "Google Chrome"
            after_gmail_open = True

        if step.action == "open_app" and youtube_task:
            step.target = step.target or "Google Chrome"
            if not step.text or "youtube" not in step.text.lower():
                step.text = "https://www.youtube.com"

        if step.action == "wait" and after_gmail_open and (step.seconds or 0) < 2.5:
            step.seconds = 2.5

        if step.action == "wait" and youtube_task and (step.seconds or 0) < 2.0:
            step.seconds = 2.0

        # YouTube: "/" focuses the search bar — more reliable than vision click
        if (
            youtube_task
            and step.action == "click"
            and step.description
            and "search" in step.description.lower()
        ):
            optimized.append(RoutineStep(
                action="hotkey",
                keys=["/"],
                description="Focus YouTube search bar (shortcut /)",
            ))
            continue

        # Gmail Compose: keyboard shortcut beats vision click
        if (
            after_gmail_open
            and step.action == "click"
            and step.description
            and "compose" in step.description.lower()
        ):
            optimized.append(RoutineStep(
                action="hotkey",
                keys=["c"],
                description="Open new compose window (Gmail shortcut: c)",
            ))
            continue

        optimized.append(step)

    _fill_type_steps(optimized, extracted)
    return optimized


class RoutinePlanner:
    def __init__(self) -> None:
        print(
            f"[planner] Ready — {PLANNER_ENGINE_TYPE}/{PLANNER_MODEL} "
            f"(Agent S uses separate AGENT_S_* config)"
        )

    def draft(self, goal: str) -> tuple[RoutineDefinition, dict[str, str]]:
        raw = self._call_llm(goal)
        payload = self._parse_json(raw)
        extracted = _extract_variables(goal)
        routine = self._to_definition(goal, payload, extracted)
        routine.steps = _optimize_planned_steps(routine.steps, goal, extracted)
        return routine, extracted

    def _call_llm(self, goal: str) -> str:
        if PLANNER_ENGINE_TYPE == "openai":
            return self._openai_chat(goal)
        return self._anthropic_chat(goal)

    def _openai_chat(self, goal: str) -> str:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set (PLANNER_ENGINE_TYPE=openai)")
        base = (AGENT_S_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        body = {
            "model": PLANNER_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Goal: {goal}"},
            ],
        }
        resp = httpx.post(
            url,
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=60.0,
        )
        if resp.is_error:
            raise RuntimeError(f"OpenAI planner error: {self._http_error_detail(resp)}")
        return resp.json()["choices"][0]["message"]["content"]

    def _anthropic_chat(self, goal: str) -> str:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set (PLANNER_ENGINE_TYPE=anthropic)")
        url = "https://api.anthropic.com/v1/messages"
        body = {
            "model": PLANNER_MODEL,
            "max_tokens": 4096,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": f"Goal: {goal}"}],
        }
        resp = httpx.post(
            url,
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60.0,
        )
        if resp.is_error:
            raise RuntimeError(f"Anthropic planner error: {self._http_error_detail(resp)}")
        blocks = resp.json().get("content") or []
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    @staticmethod
    def _http_error_detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
            msg = body.get("error", body)
            if isinstance(msg, dict):
                return msg.get("message", str(msg))
            return str(msg)
        except Exception:
            return resp.text[:300]

    def _parse_json(self, text: str) -> dict[str, Any]:
        text = text.strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if fence:
            text = fence.group(1).strip()
        return json.loads(text)

    def _to_definition(
        self, goal: str, payload: dict[str, Any], extracted: dict[str, str],
    ) -> RoutineDefinition:
        raw_steps = payload.get("steps") or []
        if not raw_steps:
            raise ValueError("planner returned no steps")

        steps: list[RoutineStep] = []
        for raw in raw_steps[:AUTONOMOUS_MAX_STEPS]:
            if not isinstance(raw, dict):
                continue
            action = str(raw.get("action", "")).strip()
            if action not in _ALLOWED_ACTIONS:
                continue
            step_data = {k: v for k, v in raw.items() if k in _STEP_FIELDS}
            step_data["action"] = action
            # LLMs often emit "url" — routines.json uses open_app.text for URLs
            if "text" not in step_data and raw.get("url"):
                step_data["text"] = raw["url"]
            fields_raw = raw.get("fields")
            step = RoutineStep(**step_data)
            if fields_raw:
                step.fields = [BatchField(**f) for f in fields_raw]
            if not step.description:
                step.description = step.action
            steps.append(step)

        if not steps:
            raise ValueError("no valid steps after parsing")

        variables = payload.get("variables") or []
        if isinstance(variables, dict):
            variables = list(variables.keys())
        for key in extracted:
            if key != "GOAL" and key not in variables:
                variables.append(key)
        if "GOAL" not in variables:
            variables = ["GOAL"] + [v for v in variables if v != "GOAL"]

        return RoutineDefinition(
            routine_id=AUTONOMOUS_ROUTINE_ID,
            description=payload.get("description") or goal,
            variables=variables,
            steps=steps,
            mode="LIVE",
        )
