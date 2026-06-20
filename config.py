import os
from dotenv import load_dotenv

load_dotenv()

ARIZE_PROJECT_NAME       = os.getenv("ARIZE_PROJECT_NAME", "shepherd")
PHOENIX_COLLECTOR_ENDPOINT = os.getenv("PHOENIX_COLLECTOR_ENDPOINT", "http://localhost:6006")
SENTRY_DSN        = os.getenv("SENTRY_DSN", "")
REDIS_URL         = os.getenv("REDIS_URL", "redis://localhost:6379")
BROWSERBASE_API_KEY = os.getenv("BROWSERBASE_API_KEY", "")
DEEPGRAM_API_KEY  = os.getenv("DEEPGRAM_API_KEY", "")
OVERSHOOT_API_KEY = os.getenv("OVERSHOOT_API_KEY", "")
BAND_API_KEY      = os.getenv("BAND_API_KEY", "")
BAND_ROOM_KEY     = os.getenv("BAND_ROOM_KEY", "")
ORKES_SERVER_URL  = os.getenv("ORKES_SERVER_URL", "")
ORKES_API_KEY     = os.getenv("ORKES_API_KEY", "")

# "LIVE" = Agent S against demonstration  |  "LOCKED" = deterministic verbatim replay
EXECUTION_MODE  = os.getenv("EXECUTION_MODE", "LIVE")
_runtime_mode: str = ""   # set via POST /api/mode; overrides EXECUTION_MODE for next run

DASHBOARD_PORT  = int(os.getenv("DASHBOARD_PORT", "8765"))
EVENTS_DB_PATH  = os.getenv("EVENTS_DB_PATH", "data/events.db")

# Agent S configuration (LIVE-mode planner; gui-agents package)
AGENT_S_ENGINE_TYPE = os.getenv("AGENT_S_ENGINE_TYPE", "anthropic")  # "anthropic" | "openai"
AGENT_S_MODEL       = os.getenv("AGENT_S_MODEL", "claude-opus-4-8")
AGENT_S_BASE_URL    = os.getenv("AGENT_S_BASE_URL", "")             # custom base URL (e.g. Ollama)
ANTHROPIC_API_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY      = os.getenv("OPENAI_API_KEY", "")
UITARS_BASE_URL     = os.getenv("UITARS_BASE_URL", "")              # empty = use LLM for grounding
UITARS_MODEL        = os.getenv("UITARS_MODEL", "ui-tars-1.5-7b")
SCREEN_WIDTH        = int(os.getenv("SCREEN_WIDTH",  "1920"))
SCREEN_HEIGHT       = int(os.getenv("SCREEN_HEIGHT", "1080"))

FEATURES: dict[str, bool] = {
    "deepgram":    bool(DEEPGRAM_API_KEY),
    "arize":       True,
    "sentry":      bool(SENTRY_DSN),
    "redis":       True,
    "browserbase": bool(BROWSERBASE_API_KEY),
    "band":        bool(BAND_API_KEY and BAND_ROOM_KEY),
    "overshoot":   bool(OVERSHOOT_API_KEY),
    "orkes":       bool(ORKES_SERVER_URL and ORKES_API_KEY),
    "context":     False,   # criteria unpublished — check Saturday
    "fieldguide":  False,   # criteria unpublished — check Saturday
    "agent_s":     True,
}
