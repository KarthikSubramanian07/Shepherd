"""
Agentspan researcher — Shepherd's research digression, built as a real Agentspan
agent (Orkes, open-source + self-hosted).

The research milestone in the email / job-application flow is not a hardcoded
scrape. It is a genuine Agentspan agent: it compiles into a durable workflow on
the local Agentspan server (http://localhost:6767), reasons about what to look
up, calls a `fetch_page` tool, and returns a short summary the engine fills into
the form. The two integrations compose cleanly: Agentspan is the agent and the
durable execution engine, Browserbase is the tool the agent's hands reach with.

Durable + observable: every run leaves an execution on the Agentspan server
(query it with `agentspan agent execution --name shepherd-researcher`). The whole
thing degrades to None if the server or SDK is unavailable, so the click path
never depends on it.
"""
import threading
from typing import Optional

from config import AGENTSPAN_SERVER_URL, AGENTSPAN_MODEL, ANTHROPIC_API_KEY, FEATURES

_configured = False
_lock = threading.Lock()
_last_execution_id: Optional[str] = None
_last_summary: Optional[str] = None


def _ensure_configured() -> bool:
    """Point the SDK at the local Agentspan server, once per process."""
    global _configured
    if _configured:
        return True
    with _lock:
        if _configured:
            return True
        try:
            from agentspan.agents import configure
            configure(server_url=AGENTSPAN_SERVER_URL)
            _configured = True
        except Exception as e:
            print(f"[agentspan] configure failed (non-fatal): {e}")
            return False
    return _configured


def available() -> bool:
    if not FEATURES.get("agentspan") or not ANTHROPIC_API_KEY:
        return False
    return _ensure_configured()


_agent = None


def _get_agent():
    """Build the researcher once and reuse it (the definition never changes)."""
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


def _build_agent():
    from agentspan.agents import Agent, tool
    from services.browserbase_routine import run_browser_step

    @tool
    def fetch_page(url: str) -> str:
        """Fetch a web page and return its visible text (title and headings).
        Use this to look up a candidate's public work."""
        # The agent's hands are governed by the SAME containment policy as the
        # engine: a tool the LLM controls cannot reach a disallowed host (no SSRF
        # to localhost / internal / metadata endpoints).
        from services import policy_engine
        blocked = policy_engine.check_containment("browser", url)
        if blocked:
            return f"[blocked by policy: {blocked['reason']}]"
        res = run_browser_step({"action": "read", "url": url})
        return res.get("value") or ""

    return Agent(
        name="shepherd-researcher",
        model=AGENTSPAN_MODEL,
        tools=[fetch_page],
        instructions=(
            "You research a job candidate's notable open-source work. Given a "
            "GitHub profile URL, fetch it and summarize two or three notable "
            "projects in one concise sentence. Return only that sentence, no "
            "preamble."
        ),
    )


def research(url: str) -> Optional[str]:
    """Run the Agentspan researcher agent against a profile URL. Returns a short
    projects summary, or None if Agentspan is unavailable or the run failed (the
    caller then falls back to a direct read)."""
    global _last_execution_id, _last_summary
    if not available():
        return None
    try:
        from agentspan.agents import run
        agent = _get_agent()
        result = run(
            agent,
            f"Research the developer at {url} and summarize their notable projects.",
        )
        _last_execution_id = getattr(result, "execution_id", None)
        if getattr(result, "is_success", False):
            out = result.output
            # The server returns {"result": <text>, "finishReason": ..., ...}
            if isinstance(out, dict):
                out = out.get("result") or out.get("output") or out.get("text") or ""
            out = (out or "").strip()
            _last_summary = out or None
            return _last_summary
        print(f"[agentspan] run not successful: {getattr(result, 'status', '?')}")
        return None
    except Exception as e:
        print(f"[agentspan] research failed (non-fatal): {e}")
        return None


def status() -> dict:
    """For the dashboard: is the durable agent engine reachable, and what did the
    researcher last do."""
    return {
        "available": available(),
        "server_url": AGENTSPAN_SERVER_URL,
        "model": AGENTSPAN_MODEL,
        "last_execution_id": _last_execution_id,
        "last_summary": _last_summary,
    }
