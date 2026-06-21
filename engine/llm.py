"""
Provider-agnostic LLM layer for crystallization.

Crystallization's LLM calls — milestone segmentation (CREATE) and EDIT-mode
patch generation (the teaching loop) — go through this single module so the
segmenter/coalescer never care which model runs. Two providers, both spoken
directly over httpx (already a FastAPI dep) so there is no SDK → no lockfile churn:

  • gemini    — Google Generative Language API (Gemma / Gemini models)
  • anthropic — Messages API

Selection is by config (`LLM_PROVIDER`); each provider carries its own key + model.
The dev default is gemini + `gemma-4-26b-a4b-it` — cheap/fast, conserving the
limited Anthropic budget. Anthropic (`claude-haiku-4-5`) is a drop-in alternative.

The layer normalizes provider quirks behind one `complete()` call:
  - Anthropic: `system` + `messages` (+ optional assistant `prefill`).
  - Gemini: `systemInstruction` + `contents` with roles user/model; Gemma "thought"
    parts are filtered out and only answer text is returned.

NEVER call this on the hot path — it does blocking network I/O. The engine runs
it only at the run boundary / in the async coalescer.
"""
from __future__ import annotations

import json
from typing import Optional

import config as _cfg

Message = tuple[str, str]  # (role, content) where role is "user" | "assistant"

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
_ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def provider() -> str:
    return (_cfg.LLM_PROVIDER or "gemini").strip().lower()


def model_name() -> str:
    """The model that will actually be hit for the active provider."""
    if provider() == "anthropic":
        return _cfg.LLM_ANTHROPIC_MODEL or _cfg.AGENT_S_MODEL or "claude-haiku-4-5"
    return _cfg.GEMINI_MODEL or "gemma-4-26b-a4b-it"


def available() -> bool:
    """True when the active provider has a usable API key configured."""
    if provider() == "anthropic":
        return bool(_cfg.ANTHROPIC_API_KEY)
    return bool(_cfg.GEMINI_API_KEY)


def complete(
    system: str,
    messages: list[Message],
    *,
    prefill: Optional[str] = None,
    max_tokens: Optional[int] = None,
    timeout: Optional[float] = None,
) -> str:
    """
    Run a chat completion through the active provider and return the plain
    completion text.

    `prefill` (an assistant-message head, e.g. "[") is honoured natively by
    Anthropic to force a shape; for Gemini it is best-effort only, so callers
    should still parse defensively (see `parse_json_array`). Raises on transport
    or API error — callers fall back to a heuristic.
    """
    max_tokens = max_tokens if max_tokens is not None else _cfg.LLM_MAX_TOKENS
    timeout = timeout if timeout is not None else _cfg.LLM_TIMEOUT_S
    if provider() == "anthropic":
        return _anthropic(system, messages, prefill, max_tokens, timeout)
    return _gemini(system, messages, prefill, max_tokens, timeout)


# ── Anthropic ──────────────────────────────────────────────────────────────────
def _anthropic(system, messages, prefill, max_tokens, timeout) -> str:
    import httpx

    msgs = [{"role": r, "content": c} for r, c in messages]
    if prefill is not None:
        msgs.append({"role": "assistant", "content": prefill})

    resp = httpx.post(
        _ANTHROPIC_URL,
        headers={
            "x-api-key": _cfg.ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model_name(),
            "max_tokens": max_tokens,
            "system": system,
            "messages": msgs,
        },
        timeout=timeout,
    )
    resp.raise_for_status()
    blocks = resp.json().get("content", [])
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    # Re-attach the prefill so the caller sees the full intended completion.
    return (prefill or "") + text


# ── Gemini / Gemma ──────────────────────────────────────────────────────────────
def _gemini(system, messages, prefill, max_tokens, timeout) -> str:
    import httpx

    # NOTE: prefill is intentionally ignored for Gemini. Seeding a trailing
    # model turn derails Gemma (it leaks reasoning into the answer); callers rely
    # on parse_json_array() instead, which tolerates fences and prose.
    contents = []
    for role, content in messages:
        contents.append({
            "role": "model" if role == "assistant" else "user",
            "parts": [{"text": content}],
        })

    body: dict = {
        "contents": contents,
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.0},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}

    resp = httpx.post(
        _GEMINI_URL.format(model=model_name()),
        params={"key": _cfg.GEMINI_API_KEY},
        headers={"content-type": "application/json"},
        json=body,
        timeout=timeout,
    )
    resp.raise_for_status()
    candidates = resp.json().get("candidates", [])
    if not candidates:
        return ""
    parts = candidates[0].get("content", {}).get("parts", [])
    # Gemma emits reasoning as parts flagged `thought: true` — keep only answers.
    return "".join(p.get("text", "") for p in parts if not p.get("thought"))


# ── Robust JSON-array extraction ────────────────────────────────────────────────
def parse_json_array(text: str) -> list:
    """
    Parse a JSON array out of a model completion, tolerating code fences, a
    leading/missing `[` (from prefill), and surrounding prose. Raises ValueError
    if no array can be recovered — callers degrade to a heuristic.
    """
    if text is None:
        raise ValueError("empty completion")
    s = text.strip()

    try:
        data = json.loads(s)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    # Reasoning models wrap the array in code fences and may emit several
    # candidate arrays / trailing prose; return the FIRST balanced, parseable
    # [...] block. Bracket scanning ignores fences and surrounding text.
    arr = _first_balanced_array(s)
    if arr is not None:
        return arr

    raise ValueError("no JSON array in completion")


def parse_json_object(text: str) -> dict:
    """
    Parse a single JSON object out of a model completion, tolerating code fences
    and surrounding prose (same robustness as parse_json_array, for the worker's
    single-message {did, status, next, …} response). Raises ValueError if none.
    """
    if text is None:
        raise ValueError("empty completion")
    s = text.strip()

    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except (json.JSONDecodeError, ValueError):
        pass

    obj = _first_balanced_object(s)
    if obj is not None:
        return obj

    raise ValueError("no JSON object in completion")


def _first_balanced_object(s: str) -> Optional[dict]:
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    data = json.loads(s[start:i + 1])
                    if isinstance(data, dict):
                        return data
                except (json.JSONDecodeError, ValueError):
                    start = -1
    return None


def _first_balanced_array(s: str) -> Optional[list]:
    depth = 0
    start = -1
    for i, ch in enumerate(s):
        if ch == "[":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "]" and depth > 0:
            depth -= 1
            if depth == 0 and start != -1:
                try:
                    data = json.loads(s[start:i + 1])
                    if isinstance(data, list):
                        return data
                except (json.JSONDecodeError, ValueError):
                    start = -1  # not parseable — keep scanning for the next [
    return None
