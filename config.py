"""
Central configuration — pydantic-settings backed.

All env vars are loaded from .env into a single typed `settings` object.
Module-level UPPER_CASE aliases are preserved for backward compatibility with
existing `from config import DEEPGRAM_API_KEY` style imports.
"""
import os

import compat as _compat  # noqa: F401  (registers pyautogui/mouseinfo shim early)
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
    band_api_key: str = ""
    band_room_key: str = ""
    orkes_server_url: str = ""
    orkes_api_key: str = ""

    # ── Deepgram STT tuning ────────────────────────────────────────────────
    deepgram_model: str = "nova-2"
    deepgram_language: str = "en-US"

    # ── Engine ─────────────────────────────────────────────────────────────
    # LIVE = Agent S per routine step | LOCKED = deterministic replay
    # AUTONOMOUS = Agent S plans freely from raw intent (no routines.json steps)
    execution_mode: str = "LIVE"
    # When LIVE/LOCKED and router finds no match, run autonomous if Agent S is up
    autonomous_on_unmatched: bool = True
    autonomous_max_steps: int = 30
    # Chain several UI actions per screenshot/request (fewer round-trips, faster)
    # instead of one action per turn. Falls back to single-action Agent S if off
    # or if the chained planner is unavailable.
    autonomous_chain: bool = True
    autonomous_chain_max: int = 6   # max actions to plan in one request
    # Print every workflow event (routing, planning, steps, monitor, graph, halts)
    # to stdout. Comprehensive terminal logging; turn off for a quiet console.
    console_log: bool = True
    # Exit after one task instead of staying up for more goals. Off by default:
    # the agent is a persistent server taking goals from the CLI and/or frontend.
    # Set true for one-shot use. Ignored in remote/--listen mode.
    exit_when_done: bool = False
    # Draft a routines.json-style step list before Agent S executes (vs reactive loop)
    autonomous_plan_first: bool = True
    autonomous_plan_max_steps: int = 12
    # Routine planner LLM — independent of Agent S (text-only JSON drafting)
    planner_engine_type: str = "anthropic"   # "anthropic" | "openai"
    planner_model: str = "claude-haiku-4-5"

    # ── Dashboard ──────────────────────────────────────────────────────────
    dashboard_port: int = 8765
    events_db_path: str = "data/events.db"
    # When set (e.g. "http://localhost:8765"), the agent does NOT start its own
    # in-process dashboard; instead it streams events to this separate, persistent
    # backend. Leave empty for the all-in-one (in-process dashboard) behavior.
    backend_url: str = ""

    # ── Crystallization LLM layer (modular) ────────────────────────────────
    # Provider for milestone segmentation / coalescing (NOT the hot path).
    # "gemini" (Google Generative Language — Gemma/Gemini) | "anthropic".
    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    # gemini-2.5-flash-lite: lightest Flash tier, multimodal (image input), and the
    # highest free-tier headroom (15 RPM / 1,000 RPD) of the stable models — fast
    # (~2.5s) and NOT a preview model (preview limits are throttled). Gemma-4
    # (gemma-4-26b-a4b-it) is a heavier local alternative but reasons before
    # answering (~90s/call); set GEMINI_MODEL to it if you want Gemma specifically.
    gemini_model: str = "gemini-2.5-flash-lite"
    llm_anthropic_model: str = "claude-haiku-4-5"
    # Cold-path ceilings — generous so a slow/reasoning model (e.g. Gemma-4) still fits.
    llm_timeout_s: float = 180.0
    llm_max_tokens: int = 8192

    # ── Remote orchestration (coordinator relay) ───────────────────────────
    # When `coordinator_url` is set, the agent dials OUT to a central
    # coordinator so a remote Command Center can observe and steer it. The
    # coordinator is the only component that needs a public URL (one ngrok /
    # deploy) — agents never expose an inbound port. All of this is
    # boundary-only and degrades to local-only when unset (the click path is
    # never gated on the network).
    coordinator_url: str = ""          # e.g. ws://localhost:8770 or wss://<host>
    coordinator_token: str = ""        # shared secret for agents + UI
    coordinator_port: int = 8770       # port the coordinator itself binds
    agent_pairing_code: str = ""       # session join code; auto-generated if empty
    agent_id: str = ""                  # stable id for this machine (default: hostname)
    agent_name: str = ""               # human label shown in the Command Center
    agent_host: str = ""              # where this agent runs (default: hostname)
    relay_fps: float = 3.0             # screen frames/sec pushed to the coordinator
    relay_frame_width: int = 1024      # downscale width for pushed frames (px)
    relay_frame_quality: int = 55      # JPEG quality for pushed frames (1-95)

    # ── Agent S (gui-agents package) ───────────────────────────────────────
    agent_s_engine_type: str = "anthropic"   # "anthropic" | "openai" | "gemini"
    agent_s_model: str = "claude-haiku-4-5"   # cheap, fast default for dev testing
    agent_s_base_url: str = ""                # custom base URL (e.g. Ollama)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    # Google's OpenAI-compatible endpoint — used when agent_s_engine_type=gemini
    # so Agent S grounds/plans on Gemini (keeps actuation off Anthropic).
    gemini_endpoint_url: str = "https://generativelanguage.googleapis.com/v1beta/openai/"
    uitars_base_url: str = ""                 # empty = use LLM for grounding
    uitars_model: str = "ui-tars-1.5-7b"
    screen_width: int = 1920
    screen_height: int = 1080

    # ── Agentspan (Orkes) — durable agent engine, open-source, self-hosted ──
    # The research digression runs as a real Agentspan agent on this server.
    # No Agentspan key (it is keyless locally); the agent reuses ANTHROPIC_API_KEY.
    agentspan_enabled: bool = True
    agentspan_server_url: str = "http://localhost:6767/api"
    agentspan_model: str = "anthropic/claude-haiku-4-5"

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
            "orkes":       bool(self.orkes_server_url and self.orkes_api_key),
            "agentspan":   bool(self.agentspan_enabled and self.anthropic_api_key),
            "agent_s":     True,
            "remote":      bool(self.coordinator_url),
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
BAND_API_KEY               = settings.band_api_key
BAND_ROOM_KEY              = settings.band_room_key
ORKES_SERVER_URL           = settings.orkes_server_url
ORKES_API_KEY              = settings.orkes_api_key
AGENTSPAN_SERVER_URL       = settings.agentspan_server_url
AGENTSPAN_MODEL            = settings.agentspan_model

EXECUTION_MODE = settings.execution_mode
AUTONOMOUS_ON_UNMATCHED = settings.autonomous_on_unmatched
AUTONOMOUS_MAX_STEPS = settings.autonomous_max_steps
AUTONOMOUS_CHAIN = settings.autonomous_chain
AUTONOMOUS_CHAIN_MAX = settings.autonomous_chain_max
EXIT_WHEN_DONE = settings.exit_when_done
CONSOLE_LOG    = settings.console_log
AUTONOMOUS_PLAN_FIRST = settings.autonomous_plan_first
AUTONOMOUS_PLAN_MAX_STEPS = settings.autonomous_plan_max_steps
PLANNER_ENGINE_TYPE = settings.planner_engine_type
PLANNER_MODEL       = settings.planner_model

DASHBOARD_PORT = settings.dashboard_port
EVENTS_DB_PATH = settings.events_db_path
BACKEND_URL    = settings.backend_url

LLM_PROVIDER        = settings.llm_provider
GEMINI_API_KEY      = settings.gemini_api_key
GEMINI_MODEL        = settings.gemini_model
GEMINI_ENDPOINT_URL = settings.gemini_endpoint_url
LLM_ANTHROPIC_MODEL = settings.llm_anthropic_model
LLM_TIMEOUT_S       = settings.llm_timeout_s
LLM_MAX_TOKENS      = settings.llm_max_tokens

import secrets as _secrets
import socket as _socket

_HOSTNAME = _socket.gethostname()
COORDINATOR_URL     = settings.coordinator_url
COORDINATOR_TOKEN   = settings.coordinator_token
COORDINATOR_PORT    = settings.coordinator_port
# Short, human-typeable session code. Read off the agent machine and entered in
# the Command Center to attach to this session. Auto-generated once per process.
AGENT_PAIRING_CODE  = settings.agent_pairing_code or _secrets.token_hex(3).upper()
AGENT_ID            = settings.agent_id or _HOSTNAME
AGENT_NAME          = settings.agent_name or _HOSTNAME
AGENT_HOST          = settings.agent_host or _HOSTNAME
RELAY_FPS           = settings.relay_fps
RELAY_FRAME_WIDTH   = settings.relay_frame_width
RELAY_FRAME_QUALITY = settings.relay_frame_quality
PROTOCOL_VERSION    = 1  # bump on breaking wire-protocol changes (see docs/PROTOCOL.md)

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
