"""
Stagehand web body — natural-language web actions on a real Browserbase cloud
browser, under the same oversight as the desktop.

Stagehand (Browserbase's AI browser framework) is the "act / extract / observe"
layer: instead of brittle CSS selectors, the agent says what it wants in plain
language and Stagehand drives a real cloud Chrome to do it. This is the second
"body" the agent can reach (the first is Agent S on the local desktop), and every
URL it touches passes Shepherd's containment/SSRF guard first.

Lazy + graceful: with `stagehand` uninstalled or no Browserbase key, `available()`
is False and callers fall back to the raw-CDP `browserbase_routine` path. Off the
click path (a routine boundary), never mid-action.
"""
from typing import Optional

from config import FEATURES, BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID, settings

_MODEL = "anthropic/claude-haiku-4-5"


def available() -> bool:
    if not FEATURES["browserbase"]:
        return False
    try:
        import stagehand  # noqa: F401
    except Exception:
        return False
    return bool(BROWSERBASE_API_KEY and BROWSERBASE_PROJECT_ID)


def _contained(url: str) -> bool:
    """True if the URL is allowed by containment (SSRF floor + allowlist)."""
    try:
        from services import policy_engine
        return policy_engine.check_containment("browser", url) is None
    except Exception:
        return True


def web_extract(url: str, instruction: str) -> Optional[str]:
    """Navigate to `url` on a Stagehand cloud browser and extract by natural
    language. Returns the extracted text, or None on block/failure so the caller
    falls back to the raw-CDP read."""
    if not available() or not _contained(url):
        return None
    sh = sid = None
    try:
        from stagehand import Stagehand

        sh = Stagehand(
            browserbase_api_key=BROWSERBASE_API_KEY,
            browserbase_project_id=BROWSERBASE_PROJECT_ID,
            model_api_key=getattr(settings, "anthropic_api_key", "") or None,
        )
        session = sh.sessions.start(model_name=_MODEL)
        sid = session.id
        sh.sessions.navigate(sid, url=url)
        res = sh.sessions.extract(sid, instruction=instruction)
        return _text(res)
    except Exception as e:
        print(f"[stagehand] web_extract non-fatal: {e}")
        return None
    finally:
        _cleanup(sh, sid)


def web_act(url: str, instruction: str) -> Optional[dict]:
    """Navigate to `url` and perform a natural-language action via Stagehand.
    Returns a small status dict or None on block/failure."""
    if not available() or not _contained(url):
        return None
    sh = sid = None
    try:
        from stagehand import Stagehand

        sh = Stagehand(
            browserbase_api_key=BROWSERBASE_API_KEY,
            browserbase_project_id=BROWSERBASE_PROJECT_ID,
            model_api_key=getattr(settings, "anthropic_api_key", "") or None,
        )
        session = sh.sessions.start(model_name=_MODEL)
        sid = session.id
        sh.sessions.navigate(sid, url=url)
        sh.sessions.act(sid, input=instruction)
        return {"status": "ok", "url": url, "action": "stagehand_act", "instruction": instruction}
    except Exception as e:
        print(f"[stagehand] web_act non-fatal: {e}")
        return None
    finally:
        _cleanup(sh, sid)


_TEXT_KEYS = ("extraction", "result", "data", "output", "text", "value")


def _text(res) -> str:
    """Pull a string out of Stagehand's extract result. The live shape is
    SessionExtractResponse(data=Data(result={'extraction': '...'})); we also
    tolerate dicts and bare strings."""
    if res is None:
        return ""
    # SessionExtractResponse.data.result["extraction"]
    data = getattr(res, "data", None)
    result = getattr(data, "result", None) if data is not None else None
    if isinstance(result, dict):
        for k in _TEXT_KEYS:
            if isinstance(result.get(k), str) and result[k].strip():
                return result[k].strip()[:500]
    # Other shapes: attributes or a plain dict on the response itself.
    for k in _TEXT_KEYS:
        v = getattr(res, k, None)
        if isinstance(v, str) and v.strip():
            return v.strip()[:500]
    if isinstance(res, dict):
        for k in _TEXT_KEYS:
            if isinstance(res.get(k), str) and res[k].strip():
                return res[k].strip()[:500]
    return str(res)[:500]


def _cleanup(sh, sid) -> None:
    try:
        if sh and sid:
            sh.sessions.end(sid)
    except Exception:
        pass
    try:
        if sh:
            sh.close()
    except Exception:
        pass
