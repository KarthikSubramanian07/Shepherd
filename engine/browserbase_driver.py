"""
BrowserbaseDriver — the Agent-S role for a cloud/local browser session.

Plays the same loop AgentSAdapter plays on the desktop, but on a Playwright
``page``: screenshot the page → ask Claude (vision) for the next batch of browser
actions as JSON → actuate them via Playwright → repeat until done/fail/budget.

Each session is isolated (own page/browser) and driven by exactly one worker
thread, so sessions run fully in parallel with NO action queue — the orchestrator
passes ``guard=None`` and actuation goes straight to Playwright. (An optional
``guard`` is still supported for contended surfaces; the shared LOCAL desktop
keeps its arbiter lease. Halt is independent of the lease — the loop polls the
session/worker halt flag between ops.)

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
# A single op failing (e.g. a guessed selector that doesn't match yet) must NOT
# kill the whole run — the agent re-screenshots and re-plans each turn, so a miss
# is recoverable. Only give up after this many CONSECUTIVE failures.
_MAX_CONSEC_ERRORS = 4
# Per-action timeout. Kept short so a wrong locator costs ~a few seconds and the
# agent gets to re-plan, instead of burning the old 8s on every miss.
_ACTION_TIMEOUT_MS = 5000
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
        from engine.run_summary import summarize_run
        response = summarize_run(goal, status, list(self._history), error=error or "")
        result = ExecutionResult(
            routine_id=_BROWSERBASE_ROUTINE_ID, status=status,
            steps_completed=steps, error=error,
            duration_ms=int((ended - started) * 1000),
            variables={"GOAL": goal}, started_at=started, ended_at=ended,
            run_id=run_id, response=response,
        )
        self._emit("execution.complete", {
            "run_id": run_id, "agent_id": self._agent_id,
            "status": status, "steps_completed": steps,
            "duration_ms": result.duration_ms, "response": response,
        })
        return result

    # ── loop ──────────────────────────────────────────────────────────────────
    def _loop(self, goal: str, run_id: str) -> tuple[str, Optional[str], int]:
        steps = 0
        consec_errors = 0
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
                    consec_errors = 0
                except Exception as e:  # noqa: BLE001
                    self._emit("step.error", {
                        "run_id": run_id, "agent_id": self._agent_id,
                        "index": steps, "error": str(e),
                    })
                    # A miss (e.g. a locator that isn't on the page yet) is
                    # recoverable: record it so the planner sees what failed,
                    # then break to re-screenshot and re-plan instead of killing
                    # the run. Only give up after repeated consecutive failures.
                    self._history.append(f"FAILED {self._describe(op)}: {str(e)[:120]}")
                    consec_errors += 1
                    if consec_errors >= _MAX_CONSEC_ERRORS:
                        return "failed", f"{consec_errors} consecutive op failures; last: {e}", steps
                    steps += 1
                    break  # abandon the rest of this batch; re-plan next turn
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
                self._locate(page, op).click(timeout=_ACTION_TIMEOUT_MS)
            elif kind == "fill":
                self._locate(page, op).fill(op.get("text", ""), timeout=_ACTION_TIMEOUT_MS)
            elif kind == "type":
                self._locate(page, op).type(op.get("text", ""), timeout=_ACTION_TIMEOUT_MS)
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

    # Roles a text target most likely refers to when the agent says "click X" /
    # "fill X" — interactive controls whose ACCESSIBLE NAME is that text. Tried
    # before raw text so we hit the real input, not its decorative label.
    _INTERACTIVE_ROLES = ("textbox", "combobox", "searchbox", "button", "link", "menuitem")

    @staticmethod
    def _locate(page, op):
        if op.get("selector"):
            loc = page.locator(op["selector"])
            # Prefer the first VISIBLE match (a selector can resolve to a hidden
            # duplicate); fall back to first so a genuine miss surfaces a clear
            # timeout the planner can react to.
            vis = BrowserbaseDriver._first_visible(loc)
            return vis if vis is not None else loc.first
        if op.get("text"):
            return BrowserbaseDriver._locate_text(page, op["text"])
        raise ValueError("click/fill/type needs a selector or text")

    @staticmethod
    def _locate_text(page, text):
        """Resolve a text target to a clickable element.

        `get_by_text(...).first` is a trap: on real pages the first match is
        often an ``aria-hidden`` placeholder label (e.g. Google Flights'
        "Where to?") that is never visible, so every click retries for the full
        timeout and fails. Instead:
          1. an accessible interactive control named `text` (the actual input/
             button the agent means), then
          2. the first VISIBLE text node (skip hidden/decorative labels), then
          3. the original first match as a last resort.
        """
        # 1) Interactive control by accessible name.
        for role in BrowserbaseDriver._INTERACTIVE_ROLES:
            try:
                loc = page.get_by_role(role, name=text, exact=False)
                vis = BrowserbaseDriver._first_visible(loc)
                if vis is not None:
                    return vis
            except Exception:
                pass  # role engine unavailable / bad name — fall through
        # 2) First visible text node.
        by_text = page.get_by_text(text, exact=False)
        vis = BrowserbaseDriver._first_visible(by_text)
        if vis is not None:
            return vis
        # 3) Last resort: original behavior (lets Playwright surface a clear error).
        return by_text.first

    @staticmethod
    def _first_visible(loc, limit: int = 8):
        """Return the first visible match of a locator, or None. Bounded so a
        broad text match can't iterate hundreds of nodes."""
        try:
            n = min(loc.count(), limit)
        except Exception:
            return None
        for i in range(n):
            try:
                nth = loc.nth(i)
                if nth.is_visible():
                    return nth
            except Exception:
                continue
        return None

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
                "can infer one, otherwise visible `text`.\n"
                "For ANY web search, use DuckDuckGo — navigate directly to "
                "`https://duckduckgo.com/?q=<url-encoded query>`. Do NOT use Google: "
                "it serves a CAPTCHA to automated browsers and you cannot proceed. "
                "If you ever land on a CAPTCHA / 'I'm not a robot' / 'unusual traffic' "
                "page, do NOT try to solve or click it — `goto` the DuckDuckGo search "
                "URL instead.\n"
                "To put text in a field, prefer `fill` targeting the INPUT — by "
                "selector (input[aria-label=...], input[placeholder=...]) or by its "
                "label text. `text` matches VISIBLE on-page text only, so do NOT target "
                "a faint placeholder/label as if it were the field; fill the field "
                "itself.\n"
                "Some widgets (e.g. Google Flights 'Where to?') are a button/combobox "
                "that only becomes a real input AFTER you click it — if a `fill` just "
                "failed, CLICK the field first, then `fill`/`type`. A failed op is fed "
                "back to you here; adapt rather than repeating the same locator.\n\n"
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
            reasoning = (plan.get("reasoning") or "").strip()
            if reasoning:
                self._history.append(f"plan: {reasoning[:120]}")
            # Surface the agent's per-step thinking on stdout (via the console
            # logger) so parallel agents' decisions are legible live.
            self._emit("agent.reasoning", {
                "agent_id":  self._agent_id,
                "turn":      turn,
                "status":    plan.get("status", "continue"),
                "reasoning": reasoning,
                "ops":       [self._describe(o) for o in (plan.get("ops") or [])],
            })
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
