"""
Browserbase web action — invoked ONLY as the "browser" action at a routine boundary.
VERIFY: confirm current Browserbase Python SDK + Playwright pairing at event.
"""
from config import FEATURES, BROWSERBASE_API_KEY


def run_browser_step(step: dict) -> dict:
    """
    step keys: url, action ("navigate"|"click"|"read"), selector (optional)
    Returns small result dict: {status, url, action, value?}
    """
    if not FEATURES["browserbase"]:
        return _local_fallback(step)

    try:
        # VERIFY: check browserbase SDK docs for current API surface
        from browserbase import Browserbase
        from playwright.sync_api import sync_playwright

        bb      = Browserbase(api_key=BROWSERBASE_API_KEY)
        session = bb.sessions.create(project_id=None)  # VERIFY: project_id param name

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(session.connect_url)
            page    = browser.new_page()
            url     = step.get("url", "https://example.com")
            page.goto(url, wait_until="domcontentloaded", timeout=15000)

            result = {"status": "ok", "url": url, "action": step.get("action", "navigate")}

            if step.get("action") == "click" and step.get("selector"):
                page.click(step["selector"])
                result["clicked"] = step["selector"]
            elif step.get("action") == "read" and step.get("selector"):
                result["value"] = (page.text_content(step["selector"]) or "").strip()[:200]

            browser.close()
            # VERIFY: correct session teardown method
            return result

    except Exception as e:
        print(f"[browserbase] Failed: {e} — local fallback")
        return _local_fallback(step)


def _local_fallback(step: dict) -> dict:
    import webbrowser, time
    url = "http://localhost:8765/demo-web"
    webbrowser.open(url)
    time.sleep(2.0)
    return {"status": "local_fallback", "url": url}
