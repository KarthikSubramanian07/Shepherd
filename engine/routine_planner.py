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

from urllib.parse import quote_plus

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
  "description": "Type a note in TextEdit",
  "variables": {"NOTE_TEXT": "Buy milk and eggs"},
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
- For YouTube play/search goals: open Chrome directly at
  https://www.youtube.com/results?search_query={{SEARCH_QUERY}} (URL-encode the query)
  instead of navigating to youtube.com and using the in-page search bar.
- For YouTube (when not using the URL above): prefer hotkey ["/"] to focus the search bar;
  put the query in type step "text" as {{SEARCH_QUERY}}.
- For click steps: describe the element visually (label text, color, screen region, nearby elements).
- Put recipient/subject/body in type step "text" using {{RECIPIENT_EMAIL}}, {{EMAIL_SUBJECT}}, {{EMAIL_BODY}} placeholders.
- Use "hotkey" for keyboard shortcuts; "wait" for pauses (seconds field).
- Put dynamic text in "text"/"target" fields using {{VARIABLE}} placeholders.
- "variables" MUST be a JSON object mapping each variable NAME to the concrete
  VALUE you extracted from the goal, e.g.
  {{"SEARCH_QUERY": "despacito", "RECIPIENT_EMAIL": "sam@acme.com"}}.
  Extract values from the goal yourself (do not leave placeholders unfilled).
  For YouTube goals, SEARCH_QUERY is the media to search for (e.g. "despacito").
- Prefer simple, reliable steps over overly granular ones.
- Do NOT include markdown fences or commentary — raw JSON only.

Example:
{_EXAMPLE_ROUTINE}
"""


def _variables_from_payload(goal: str, payload: dict[str, Any]) -> dict[str, str]:
    """Build the name→value substitution map from the planner LLM's output.

    The LLM extracts concrete values straight from the goal (see the prompt's
    "variables" rule), so there is no brittle regex/keyword guessing here. GOAL
    is always present; we keep only non-empty string values."""
    extracted: dict[str, str] = {"GOAL": goal}
    raw_vars = payload.get("variables")
    if isinstance(raw_vars, dict):
        for name, value in raw_vars.items():
            if name and value is not None and str(value).strip():
                extracted[str(name)] = str(value)
    return extracted


_DEFAULT_BROWSER = "Google Chrome"


def _looks_like_url(value: str) -> bool:
    v = (value or "").strip().lower()
    return v.startswith("http://") or v.startswith("https://") or (
        "://" in v or ("/" in v and "." in v)
    )


def normalize_open_app_step(step: RoutineStep) -> None:
    """Routines use target=app name, text=URL. LLMs often swap or omit target."""
    if step.action != "open_app":
        return
    target = (step.target or "").strip()
    if _looks_like_url(target):
        if not step.text:
            step.text = target
        step.target = _DEFAULT_BROWSER
    elif not target and step.text and _looks_like_url(step.text):
        step.target = _DEFAULT_BROWSER


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


def _is_youtube_search_choreography(step: RoutineStep) -> bool:
    """Steps made redundant when we open the search-results URL directly."""
    desc = (step.description or "").lower()
    if step.action == "hotkey":
        if step.keys == ["/"]:
            return True
        if "search" in desc and any(k in desc for k in ("enter", "bar", "focus")):
            return True
    if step.action == "type":
        if step.text == "{SEARCH_QUERY}":
            return True
        if "search" in desc and any(k in desc for k in ("type", "query", "box")):
            return True
    if step.action == "wait" and "search result" in desc:
        return True
    return False


def _optimize_youtube_steps(steps: list[RoutineStep], extracted: dict[str, str]) -> None:
    """Open YouTube at /results?search_query=… — reliable vs in-page search UI."""
    query = extracted.get("SEARCH_QUERY")
    if not query:
        return

    url = f"https://www.youtube.com/results?search_query={quote_plus(query)}"
    for step in steps:
        if step.action == "open_app":
            step.target = step.target or _DEFAULT_BROWSER
            step.text = url
            step.description = f"Open YouTube search for '{query}' in Chrome"
            normalize_open_app_step(step)
            break

    steps[:] = [s for s in steps if not _is_youtube_search_choreography(s)]


class RoutinePlanner:
    def __init__(self) -> None:
        print(
            f"[planner] Ready — {PLANNER_ENGINE_TYPE}/{PLANNER_MODEL} "
            f"(Agent S uses separate AGENT_S_* config)"
        )

    def draft(
        self, goal: str, prior_milestones: "list[str] | None" = None,
    ) -> tuple[RoutineDefinition, dict[str, str]]:
        memory_hint = self._memory_hint(prior_milestones)
        raw = self._call_llm(goal, memory_hint)
        payload = self._parse_json(raw)
        extracted = _variables_from_payload(goal, payload)
        routine = self._to_definition(goal, payload, extracted)
        _fill_type_steps(routine.steps, extracted)
        _optimize_youtube_steps(routine.steps, extracted)
        return routine, extracted

    @staticmethod
    def _memory_hint(prior_milestones: "list[str] | None") -> str:
        """Turn a prior run's milestone trail into a planning hint, so the agent
        reuses what worked before instead of re-deriving the task from scratch."""
        ms = [m for m in (prior_milestones or []) if m]
        if not ms:
            return ""
        trail = "\n".join(f"  {i + 1}. {m}" for i, m in enumerate(ms))
        return (
            "\n\nMEMORY — you have completed this goal before. The milestone trail "
            "that worked last time was:\n" + trail +
            "\nReuse this proven sequence as your plan; refine a step only if the "
            "current screen clearly requires it."
        )

    def _call_llm(self, goal: str, memory_hint: str = "") -> str:
        if PLANNER_ENGINE_TYPE == "openai":
            return self._openai_chat(goal, memory_hint)
        return self._anthropic_chat(goal, memory_hint)

    def _openai_chat(self, goal: str, memory_hint: str = "") -> str:
        if not OPENAI_API_KEY:
            raise RuntimeError("OPENAI_API_KEY not set (PLANNER_ENGINE_TYPE=openai)")
        base = (AGENT_S_BASE_URL or "https://api.openai.com/v1").rstrip("/")
        url = f"{base}/chat/completions"
        body = {
            "model": PLANNER_MODEL,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": f"Goal: {goal}{memory_hint}"},
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

    def _anthropic_chat(self, goal: str, memory_hint: str = "") -> str:
        if not ANTHROPIC_API_KEY:
            raise RuntimeError("ANTHROPIC_API_KEY not set (PLANNER_ENGINE_TYPE=anthropic)")
        url = "https://api.anthropic.com/v1/messages"
        body = {
            "model": PLANNER_MODEL,
            "max_tokens": 4096,
            "system": _SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": f"Goal: {goal}{memory_hint}"}],
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
            normalize_open_app_step(step)
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
