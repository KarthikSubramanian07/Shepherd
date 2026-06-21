"""
BrowserbaseDriver — the Agent-S role for a cloud/local browser session.

Plays the same loop AgentSAdapter plays on the desktop, but on a Playwright
``page``: screenshot the page → ask Claude (vision) for the next batch of browser
actions as JSON → actuate them via Playwright → repeat until done/fail/budget.

Every actuation runs inside the session's arbiter lease (``guard``), so it shows
up in the action-queue UI and is serialized *within* the session — while
different sessions run fully in parallel (each is its own surface).

Oversight is preserved: every ``goto`` URL is checked against the policy
containment allowlist + SSRF floor before navigation, exactly like the desktop
``open_app``/``browser`` paths.

Degrades gracefully: with no session (stub) or no ANTHROPIC_API_KEY it returns a
clean ``failed``/``aborted`` result instead of raising.
"""
from __future__ import annotations

import base64
import json
import threading
import time
import uuid
from contextlib import nullcontext
from typing import Callable, Optional

from shepherd_types import ExecutionResult
from services import policy_engine

_MAX_TURNS = 20
_BROWSERBASE_ROUTINE_ID = "BROWSERBASE"


class BrowserbaseDriver:
    def __init__(
        self,
        session,
        agent_id: str,
        guard: Optional[Callable[[], object]] = None,
        on_event: Optional[Callable[[str, dict], None]] = None,
        halt: Optional[threading.Event] = None,
    ) -> None:
        self._session = session
        self._agent_id = agent_id
        self._guard = guard
        self._on_event = on_event
        self._halt = halt or threading.Event()
        self._history: list[str] = []

    # ── public ────────────────────────────────────────────────────────────────
    def run(self, goal: str, params: Optional[dict] = None) -> ExecutionResult:
        run_id = uuid.uuid4().hex[:8]
        started = time.time()
        self._emit("execution.start", {
            "run_id": run_id, "agent_id": self._agent_id,
            "routine_id": _BROWSERBASE_ROUTINE_ID, "mode": "BROWSERBASE",
            "goal": goal, "total_steps": _MAX_TURNS, "steps": [],
        })

        status, error, steps = "completed", None, 0
        if not getattr(self._session, "available", False):
            status, error = "failed", "browser session unavailable"
        elif not self._has_key():
            status, error = "failed", "ANTHROPIC_API_KEY unset — browser agent needs a planner"
        else:
            status, error, steps = self._loop(goal, run_id)

        ended = time.time()
        result = ExecutionResult(
            routine_id=_BROWSERBASE_ROUTINE_ID, status=status,
            steps_completed=steps, error=error,
            duration_ms=int((ended - started) * 1000),
            variables={"GOAL": goal}, started_at=started, ended_at=ended,
            run_id=run_id,
        )
        self._emit("execution.complete", {
            "run_id": run_id, "agent_id": self._agent_id,
            "status": status, "steps_completed": steps,
            "duration_ms": result.duration_ms,
        })
        return result

    # ── loop ──────────────────────────────────────────────────────────────────
    def _loop(self, goal: str, run_id: str) -> tuple[str, Optional[str], int]:
        steps = 0
        for turn in range(_MAX_TURNS):
            if self._halt.is_set() or self._session.halted:
                return "aborted", "halt_requested", steps

            plan = self._plan(goal, turn)
            if plan is None:
                return "failed", "planner returned nothing actionable", steps
            if plan.get("status") == "done":
                return "completed", None, steps
            if plan.get("status") == "fail":
                return "failed", plan.get("reasoning") or "agent reported failure", steps

            ops = plan.get("ops") or []
            if not ops:
                continue  # nothing to do this turn → re-observe
            for op in ops:
                if self._halt.is_set() or self._session.halted:
                    return "aborted", "halt_requested", steps
                self._emit("step.start", {
                    "run_id": run_id, "agent_id": self._agent_id, "index": steps,
                    "action": op.get("op", "?"), "description": self._describe(op),
                    "total": _MAX_TURNS,
                })
                t0 = time.time()
                try:
                    self._exec_op(op)
                    self._emit("step.complete", {
                        "run_id": run_id, "agent_id": self._agent_id, "index": steps,
                        "status": "completed", "duration_ms": int((time.time() - t0) * 1000),
                    })
                except Exception as e:  # noqa: BLE001
                    self._emit("step.error", {
                        "run_id": run_id, "agent_id": self._agent_id,
                        "index": steps, "error": str(e),
                    })
                    return "failed", str(e), steps
                steps += 1
        return "failed", f"turn budget exhausted ({_MAX_TURNS})", steps

    # ── actuation (under the session lease) ───────────────────────────────────
    def _exec_op(self, op: dict) -> None:
        page = self._session.page
        kind = (op.get("op") or "").lower()
        lease = self._guard() if self._guard else nullcontext()
        with lease:
            if kind == "goto":
                url = op.get("url") or ""
                blocked = policy_engine.check_containment("browser", url)
                if blocked:
                    raise ValueError(f"[containment] {blocked['reason']}")
                page.goto(url, wait_until="domcontentloaded", timeout=20000)
            elif kind == "click":
                self._locate(page, op).click(timeout=8000)
            elif kind == "fill":
                self._locate(page, op).fill(op.get("text", ""), timeout=8000)
            elif kind == "type":
                self._locate(page, op).type(op.get("text", ""), timeout=8000)
            elif kind == "press":
                page.keyboard.press(op.get("key", "Enter"))
            elif kind == "wait":
                page.wait_for_timeout(min(int(op.get("ms", 1000)), 8000))
            elif kind == "read":
                # Record content into history so the planner can use it next turn.
                loc = self._locate(page, op) if (op.get("selector") or op.get("text")) else None
                val = (loc.inner_text(timeout=5000) if loc else page.title())[:240]
                self._history.append(f"read: {val}")
            else:
                raise ValueError(f"unknown browser op: {kind!r}")

    @staticmethod
    def _locate(page, op):
        if op.get("selector"):
            return page.locator(op["selector"]).first
        if op.get("text"):
            return page.get_by_text(op["text"], exact=False).first
        raise ValueError("click/fill/type needs a selector or text")

    # ── planning (Claude vision) ──────────────────────────────────────────────
    def _plan(self, goal: str, turn: int) -> Optional[dict]:
        try:
            from anthropic import Anthropic
            from config import AGENT_S_MODEL, ANTHROPIC_API_KEY

            shot = self._screenshot()
            history = "\n".join(f"  - {h}" for h in self._history[-8:]) or "  (none yet)"
            prompt = (
                "You are an autonomous web agent driving a real browser via "
                "Playwright. Pursue this goal:\n"
                f"  {goal}\n\n"
                f"Current URL: {self._url()}\n"
                f"Recent actions/reads:\n{history}\n\n"
                "Look at the screenshot (the live page) and plan the NEXT batch of "
                "browser actions. Prefer robust locators: a CSS `selector` when you "
                "can infer one, otherwise visible `text`.\n\n"
                "Ops vocabulary (JSON objects):\n"
                '  {"op":"goto","url":"https://..."}\n'
                '  {"op":"click","selector":"button#go"}  or  {"op":"click","text":"Sign in"}\n'
                '  {"op":"fill","selector":"input[name=q]","text":"hello"}\n'
                '  {"op":"type","text":"...","selector":"..."}\n'
                '  {"op":"press","key":"Enter"}\n'
                '  {"op":"wait","ms":1500}\n'
                '  {"op":"read","selector":".result"}\n\n'
                "Return ONLY JSON: "
                '{"reasoning":"...","status":"continue|done|fail","ops":[...]}\n'
                'Use "done" only when the goal is actually achieved on the page; '
                '"fail" if it cannot be. Keep each batch small and safe — stop '
                "before any action whose target only appears after a navigation."
            )
            content = [{"type": "text", "text": prompt}]
            if shot:
                content.insert(0, {"type": "image", "source": {
                    "type": "base64", "media_type": "image/png", "data": shot,
                }})
            client = Anthropic(api_key=ANTHROPIC_API_KEY)
            msg = client.messages.create(
                model=AGENT_S_MODEL, max_tokens=1024,
                messages=[{"role": "user", "content": content}],
            )
            raw = "".join(b.text for b in msg.content
                          if getattr(b, "type", "") == "text").strip()
            start, end = raw.find("{"), raw.rfind("}")
            if start == -1 or end == -1:
                return None
            plan = json.loads(raw[start:end + 1])
            if plan.get("reasoning"):
                self._history.append(f"plan: {plan['reasoning'][:120]}")
            return plan
        except Exception as e:  # noqa: BLE001
            print(f"[browserbase_driver] plan turn {turn} failed: {e}")
            return None

    # ── helpers ───────────────────────────────────────────────────────────────
    def _screenshot(self) -> Optional[str]:
        try:
            png = self._session.page.screenshot(type="png")
            return base64.standard_b64encode(png).decode()
        except Exception:
            return None

    def _url(self) -> str:
        try:
            return self._session.page.url
        except Exception:
            return "(unknown)"

    @staticmethod
    def _has_key() -> bool:
        from config import ANTHROPIC_API_KEY
        return bool(ANTHROPIC_API_KEY)

    @staticmethod
    def _describe(op: dict) -> str:
        kind = op.get("op", "?")
        tgt = op.get("url") or op.get("selector") or op.get("text") or op.get("key") or ""
        return f"{kind} {tgt}".strip()

    def _emit(self, event_type: str, data: dict) -> None:
        if self._on_event is None:
            return
        try:
            self._on_event(event_type, data)
        except Exception:
            pass
