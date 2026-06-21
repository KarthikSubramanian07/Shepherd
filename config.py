"""
Central configuration — pydantic-settings backed.

All env vars are loaded from .env into a single typed `settings` object.
Module-level UPPER_CASE aliases are preserved for backward compatibility with
existing `from config import DEEPGRAM_API_KEY` style imports.
"""
import os

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Keep os.environ populated too, so any direct os.getenv() callers still work.
load_dotenv()

_ENV_FILE = os.path.join(os.path.dirname(__file__), ".env")

# Runtime mode override — set via POST /api/mode; overrides EXECUTION_MODE for the next run.
_runtime_mode: str = ""

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Arize Phoenix (local) ──────────────────────────────────────────────
    arize_project_name: str = "shepherd"
    phoenix_collector_endpoint: str = "http://localhost:6006"

    # ── Sentry ─────────────────────────────────────────────────────────────
    sentry_dsn: str = ""

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── External service keys ──────────────────────────────────────────────
    browserbase_api_key: str = ""
    deepgram_api_key: str = ""
    overshoot_api_key: str = ""
    band_api_key: str = ""
    band_room_key: str = ""
    orkes_server_url: str = ""
    orkes_api_key: str = ""

    # ── Deepgram STT tuning ────────────────────────────────────────────────
    deepgram_model: str = "nova-2"
    deepgram_language: str = "en-US"

    # ── Engine ─────────────────────────────────────────────────────────────
    # "LIVE" = Agent S against demonstration | "LOCKED" = deterministic replay
    execution_mode: str = "LIVE"

    # ── Dashboard ──────────────────────────────────────────────────────────
    dashboard_port: int = 8765
    events_db_path: str = "data/events.db"

    # ── Crystallization LLM layer (modular) ────────────────────────────────
    # Provider for milestone segmentation / coalescing (NOT the hot path).
    # "gemini" (Google Generative Language — Gemma/Gemini) | "anthropic".
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = "gemma-4-26b-a4b-it"   # MoE, ~4B active — cheap/fast dev default
    llm_anthropic_model: str = "claude-haiku-4-5"
    # Gemma-4 always reasons before answering (~90s/call, "thought" tokens count
    # against the budget). Generous defaults; this is the COLD path. Tune down for
    # a faster non-reasoning model.
    llm_timeout_s: float = 180.0
    llm_max_tokens: int = 8192

    # ── Agent S (gui-agents package) ───────────────────────────────────────
    agent_s_engine_type: str = "anthropic"   # "anthropic" | "openai"
    agent_s_model: str = "claude-haiku-4-5"   # cheap, fast default for dev testing
    agent_s_base_url: str = ""                # custom base URL (e.g. Ollama)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    uitars_base_url: str = ""                 # empty = use LLM for grounding
    uitars_model: str = "ui-tars-1.5-7b"
    screen_width: int = 1920
    screen_height: int = 1080

    @property
    def features(self) -> dict[str, bool]:
        """Feature flags derived from which credentials are present."""
        return {
            "deepgram":    bool(self.deepgram_api_key),
            "arize":       True,
            "sentry":      bool(self.sentry_dsn),
            "redis":       True,
            "browserbase": bool(self.browserbase_api_key),
            "band":        bool(self.band_api_key and self.band_room_key),
            "overshoot":   bool(self.overshoot_api_key),
            "orkes":       bool(self.orkes_server_url and self.orkes_api_key),
            "context":     False,   # criteria unpublished — check Saturday
            "fieldguide":  False,   # criteria unpublished — check Saturday
            "agent_s":     True,
        }


settings = Settings()


# ── Backward-compatible module-level aliases ───────────────────────────────────
# Existing code imports these UPPER_CASE names directly from `config`.
ARIZE_PROJECT_NAME         = settings.arize_project_name
PHOENIX_COLLECTOR_ENDPOINT = settings.phoenix_collector_endpoint
SENTRY_DSN                 = settings.sentry_dsn
REDIS_URL                  = settings.redis_url
BROWSERBASE_API_KEY        = settings.browserbase_api_key
DEEPGRAM_API_KEY           = settings.deepgram_api_key
DEEPGRAM_MODEL             = settings.deepgram_model
DEEPGRAM_LANGUAGE          = settings.deepgram_language
OVERSHOOT_API_KEY          = settings.overshoot_api_key
BAND_API_KEY               = settings.band_api_key
BAND_ROOM_KEY              = settings.band_room_key
ORKES_SERVER_URL           = settings.orkes_server_url
ORKES_API_KEY              = settings.orkes_api_key

EXECUTION_MODE = settings.execution_mode

DASHBOARD_PORT = settings.dashboard_port
EVENTS_DB_PATH = settings.events_db_path

LLM_PROVIDER        = settings.llm_provider
GEMINI_API_KEY      = settings.gemini_api_key
GEMINI_MODEL        = settings.gemini_model
LLM_ANTHROPIC_MODEL = settings.llm_anthropic_model
LLM_TIMEOUT_S       = settings.llm_timeout_s
LLM_MAX_TOKENS      = settings.llm_max_tokens

AGENT_S_ENGINE_TYPE = settings.agent_s_engine_type
AGENT_S_MODEL       = settings.agent_s_model
AGENT_S_BASE_URL    = settings.agent_s_base_url
ANTHROPIC_API_KEY   = settings.anthropic_api_key
OPENAI_API_KEY      = settings.openai_api_key
UITARS_BASE_URL     = settings.uitars_base_url
UITARS_MODEL        = settings.uitars_model
SCREEN_WIDTH        = settings.screen_width
SCREEN_HEIGHT       = settings.screen_height

FEATURES: dict[str, bool] = settings.features
