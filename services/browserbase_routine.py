"""
Browserbase web action — invoked ONLY as the "browser" action at a routine boundary.
VERIFY: confirm current Browserbase Python SDK + Playwright pairing at event.

Used two ways:
  - "navigate"/"click": drive a real cloud browser as a visible web beat.
  - "read": pull live content off a real page (e.g. the research digression in
    ROUTINE_JOB_APPLICATION) and hand it back so the engine can store it into a
    variable the next step fills. Degrades to a deterministic fallback value so
    the beat still works offline.
"""

from config import FEATURES, BROWSERBASE_API_KEY, BROWSERBASE_PROJECT_ID


def run_browser_step(step: dict) -> dict:
    """
    step keys: url, action ("navigate"|"click"|"read"), selector (optional),
               store_as (optional — variable name the engine sets from `value`),
               fallback_value (optional — value returned when offline).
    Returns: {status, url, action, value?}
    """
    if not FEATURES["browserbase"]:
        return _local_fallback(step)

    if not BROWSERBASE_PROJECT_ID:
        # A cloud session needs a project id, not just a key. Make the miss loud
        # so it's not mistaken for "Browserbase isn't working" — then degrade.
        print(
            "[browserbase] BROWSERBASE_PROJECT_ID not set — "
            "set it (dashboard → Settings) to create a real cloud session; "
            "using local fallback."
        )
        return _local_fallback(step)

    try:
        from browserbase import Browserbase
        from playwright.sync_api import sync_playwright

        # browserbase 0.3.0: project_id on the client, flat create_session() +
        # get_connect_url(session_id) — there is no bb.sessions namespace.
        bb = Browserbase(api_key=BROWSERBASE_API_KEY, project_id=BROWSERBASE_PROJECT_ID)
        session = bb.create_session()
        connect_url = bb.get_connect_url(session.id)
        # The embeddable, interactive live-view URL — the Control Hub renders this
        # in an iframe so you watch (and on a halt, take over) the cloud browser.
        live_view_url = _live_view_url(bb, session.id)
        if live_view_url:
            _emit_live_view(session.id, live_view_url, step.get("url", ""))
        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(connect_url)
                page = browser.new_page()
                url = step.get("url", "https://example.com")
                page.goto(url, wait_until="domcontentloaded", timeout=15000)

                action = step.get("action", "navigate")
                result = {"status": "ok", "url": url, "action": action, "live_view_url": live_view_url}

                if action == "click" and step.get("selector"):
                    page.click(step["selector"])
                    result["clicked"] = step["selector"]
                elif action == "read":
                    result["value"] = _read_content(page, step.get("selector"))

                browser.close()
                return result
        finally:
            # Release the cloud session immediately instead of leaking it until
            # Browserbase's idle timeout (which would burn the account's quota).
            try:
                bb.complete_session(session.id)
            except Exception:
                pass

    except Exception as e:
        print(f"[browserbase] Failed: {e} — local fallback")
        return _local_fallback(step)


def _read_content(page, selector) -> str:
    """Extract something useful from the page. With a selector, read it; without,
    fall back to the title + the first couple of headings so a read never comes
    back empty."""
    try:
        if selector:
            return (page.text_content(selector) or "").strip()[:240]
        title = (page.title() or "").strip()
        heads = page.eval_on_selector_all(
            "h1, h2", "els => els.slice(0,3).map(e => e.textContent.trim()).join(' · ')"
        )
        return (f"{title} — {heads}" if heads else title)[:240]
    except Exception:
        return ""


def _local_fallback(step: dict) -> dict:
    action = step.get("action", "navigate")
    # A read offline still returns a deterministic value so the flow continues.
    if action == "read":
        return {
            "status": "local_fallback",
            "url": step.get("url", ""),
            "action": "read",
            "value": step.get("fallback_value", ""),
        }
    import webbrowser
    import time

    url = "http://localhost:8765/demo-web"
    webbrowser.open(url)
    time.sleep(2.0)
    return {"status": "local_fallback", "url": url, "action": action}


def _live_view_url(bb, session_id: str):
    """The interactive, embeddable live-view URL for a Browserbase session
    (debuggerFullscreenUrl). Lets the Control Hub render the cloud browser in an
    iframe and a human take control on a halt. Best-effort; None on failure."""
    try:
        urls = bb.get_debug_connection_urls(session_id)
        return getattr(urls, "debuggerFullscreenUrl", None) or getattr(urls, "debuggerUrl", None)
    except Exception as e:
        print(f"[browserbase] live-view url non-fatal: {e}")
        return None


def _emit_live_view(session_id: str, url: str, target: str) -> None:
    """Tell the Control Hub a cloud-browser session is live so it can embed it."""
    try:
        from dashboard.events import event_bus
        event_bus.emit("browser.live_view", {
            "session_id": session_id, "live_view_url": url, "target": target,
        })
    except Exception:
        pass
