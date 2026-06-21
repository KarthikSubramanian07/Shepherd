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
    # Optional override for trace URLs. Leave empty to resolve from Phoenix GraphQL.
    phoenix_project_slug: str = ""

    # ── Sentry ─────────────────────────────────────────────────────────────
    sentry_dsn: str = ""

    # ── Redis ──────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379"

    # ── External service keys ──────────────────────────────────────────────
    browserbase_api_key: str = ""
    browserbase_project_id: str = ""   # required to create a real cloud session
    deepgram_api_key: str = ""
    orkes_server_url: str = ""
    orkes_api_key: str = ""

    # ── Band (band.ai / Thenvoi agentic mesh) — multi-agent oversight ───────
    # Two agents collaborate over a Band room: the engine peer posts a flagged
    # high-stakes action, the independent verifier peer replies with a verdict.
    # Free Agent API tier. Register both agents at app.band.ai/agents.
    band_enabled: bool = False
    band_room_id: str = ""              # the Band oversight room id
    band_engine_api_key: str = ""       # the shepherd-monitor (engine) agent's Band API key
    band_verifier_agent_id: str = ""    # the shepherd-verifier agent's UUID (for @mention)
    band_verifier_handle: str = "shepherd-verifier"
    band_api_base: str = "https://app.band.ai/api/v1/agent"
    # Oversight council: extra specialist verifier agents that deliberate + vote in
    # the room alongside (or instead of) the single verifier. Comma-separated
    # `handle:agent_uuid` pairs, e.g.
    #   "shepherd-security:uuid1,shepherd-privacy:uuid2,shepherd-destructive:uuid3"
    # Empty = fall back to the single shepherd-verifier (a council of one).
    band_council: str = ""

    # ── ArmorIQ — intent-intelligence authorization for the oversight stack ──
    # Before a run executes, the resolved plan is captured and ArmorIQ issues a
    # cryptographically-signed intent token gated by an allow/deny policy derived
    # from containment. A denial halts the run before the first action.
    armoriq_enabled: bool = False
    armoriq_api_key: str = ""           # ak_live_... / ak_test_... / ak_claw_...
    # Strict mode: a tool your ArmorIQ tenant policy denies HALTS the run. Off by
    # default so an unconfigured tenant doesn't block runs — the signed intent
    # token still gates every run and denials are surfaced as advisory.
    armoriq_strict: bool = False

    # ── Deepgram STT tuning ────────────────────────────────────────────────
    deepgram_model: str = "nova-2"
    deepgram_language: str = "en-US"
    # Voice oversight: the agent speaks the high-stakes gate question via Aura TTS
    # and takes a spoken approve/stop answer. Additive to the on-screen gate.
    deepgram_tts_voice: str = "aura-asteria-en"
    voice_oversight: bool = True

    # ── Engine ─────────────────────────────────────────────────────────────
    # The front door for every intent. The old three-mode EXECUTION_MODE enum
    # bundled TWO independent decisions; these un-bundle them:
    #
    #   use_router     — try to match a saved workflow/routine first?
    #                      False (default) = skip routing entirely; run every intent
    #                        as a free-form autonomous Agent S goal (was AUTONOMOUS).
    #                      True            = route first; fall back to autonomous on
    #                        no match (gated by autonomous_on_unmatched). Was LIVE/LOCKED.
    #   routine_replay — when the router DOES match a routine, how to drive it:
    #                      "vision"        = Agent S looks at each step (was LIVE)
    #                      "deterministic" = replay recorded coordinates (was LOCKED)
    #
    # The autonomous_* knobs below tune the free-form loop, so they apply whenever
    # autonomous execution runs (use_router off, OR on with no match). The legacy
    # LIVE/LOCKED/AUTONOMOUS string (EXECUTION_MODE) is DERIVED from these at module
    # level, so the engine, dashboard, and /api/mode override keep working unchanged.
    use_router: bool = False
    routine_replay: str = "vision"   # "vision" (LIVE) | "deterministic" (LOCKED)
    # Legacy override: when set in .env, this LIVE/LOCKED/AUTONOMOUS string wins over
    # the use_router/routine_replay knobs. Empty = derive from them (preferred).
    execution_mode: str = ""
    # When use_router and the router finds no match, run autonomous if Agent S is up
    autonomous_on_unmatched: bool = True
    autonomous_max_steps: int = 20
    # Feed this goal's prior milestone graph to the planner. Off by default — each
    # run does a fresh Agent S loop without relying on memory. Turn on to recall.
    autonomous_use_memory: bool = False
    # Chain several UI actions per screenshot/request (fewer round-trips, faster)
    # instead of one action per turn. Falls back to single-action Agent S if off
    # or if the chained planner is unavailable.
    autonomous_chain: bool = True
    autonomous_chain_max: int = 5   # max actions/request — must fit a full field replace
                                    # (activate + click + select-all + type + confirm); too
                                    # small and the model defers the type and loops forever
    # Print every workflow event (routing, planning, steps, monitor, graph, halts)
    # to stdout. Comprehensive terminal logging; turn off for a quiet console.
    console_log: bool = True
    # Exit after one task instead of staying up for more goals. Off by default:
    # the agent is a persistent server taking goals from the CLI and/or frontend.
    # Set true for one-shot use. Ignored in remote/--listen mode.
    exit_when_done: bool = False
    # Draft a plan first, then execute it through the reactive Agent S loop (which
    # re-screenshots at sensible intervals) using the plan as a roadmap. On by
    # default. Off = skip planning, react turn-by-turn from the goal alone.
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
    webrtc_enabled: bool = False       # enable P2P WebRTC screen streaming (requires aiortc)

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
            "band":        bool(self.band_enabled and self.band_room_id
                                 and self.band_engine_api_key and self.band_verifier_agent_id),
            "orkes":       bool(self.orkes_server_url and self.orkes_api_key),
            "agentspan":   bool(self.agentspan_enabled and self.anthropic_api_key),
            "armoriq":     bool(self.armoriq_enabled and self.armoriq_api_key),
            "agent_s":     True,
            "remote":      bool(self.coordinator_url),
        }


settings = Settings()


# ── Backward-compatible module-level aliases ───────────────────────────────────
# Existing code imports these UPPER_CASE names directly from `config`.
ARIZE_PROJECT_NAME         = settings.arize_project_name
PHOENIX_COLLECTOR_ENDPOINT = settings.phoenix_collector_endpoint
PHOENIX_PROJECT_SLUG       = settings.phoenix_project_slug
SENTRY_DSN                 = settings.sentry_dsn
REDIS_URL                  = settings.redis_url
BROWSERBASE_API_KEY        = settings.browserbase_api_key
BROWSERBASE_PROJECT_ID     = settings.browserbase_project_id
DEEPGRAM_API_KEY           = settings.deepgram_api_key
DEEPGRAM_MODEL             = settings.deepgram_model
DEEPGRAM_LANGUAGE          = settings.deepgram_language
DEEPGRAM_TTS_VOICE         = settings.deepgram_tts_voice
VOICE_OVERSIGHT            = settings.voice_oversight
BAND_ENABLED               = settings.band_enabled
BAND_ROOM_ID               = settings.band_room_id
BAND_ENGINE_API_KEY        = settings.band_engine_api_key
BAND_VERIFIER_AGENT_ID     = settings.band_verifier_agent_id
BAND_VERIFIER_HANDLE       = settings.band_verifier_handle
BAND_API_BASE              = settings.band_api_base
BAND_COUNCIL               = settings.band_council
ARMORIQ_ENABLED            = settings.armoriq_enabled
ARMORIQ_API_KEY            = settings.armoriq_api_key
ARMORIQ_STRICT             = settings.armoriq_strict
ORKES_SERVER_URL           = settings.orkes_server_url
ORKES_API_KEY              = settings.orkes_api_key
AGENTSPAN_SERVER_URL       = settings.agentspan_server_url
AGENTSPAN_MODEL            = settings.agentspan_model

USE_ROUTER     = settings.use_router
ROUTINE_REPLAY = settings.routine_replay


def _derive_mode(use_router: bool, replay: str) -> str:
    """Map the un-bundled knobs back to the legacy LIVE/LOCKED/AUTONOMOUS enum that
    the engine, dashboard, and /api/mode override still speak."""
    if not use_router:
        return "AUTONOMOUS"
    return "LOCKED" if replay.lower() == "deterministic" else "LIVE"


# An explicit EXECUTION_MODE in .env wins (back-compat); otherwise derive it.
EXECUTION_MODE = (
    settings.execution_mode.upper()
    if settings.execution_mode
    else _derive_mode(USE_ROUTER, ROUTINE_REPLAY)
)
AUTONOMOUS_ON_UNMATCHED = settings.autonomous_on_unmatched
AUTONOMOUS_MAX_STEPS = settings.autonomous_max_steps
AUTONOMOUS_USE_MEMORY = settings.autonomous_use_memory
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
WEBRTC_ENABLED      = settings.webrtc_enabled
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
