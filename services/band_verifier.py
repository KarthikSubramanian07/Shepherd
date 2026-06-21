"""
shepherd-verifier — Shepherd's independent oversight verifier, as a Band agent.

This is the *second* agent in Shepherd's Band collaboration. It lives in the Band
oversight room and waits to be @mentioned. When the engine peer (band_collab.py)
posts a flagged high-stakes action, this agent — Claude, wired into Band via the
AnthropicAdapter — reasons about the risk and replies with a verdict the engine
reads back into the human gate.

It is a standalone process, run alongside the Shepherd backend:

    uv pip install "band-sdk[anthropic]"     # one-time, on the machine running it
    python -m services.band_verifier         # connects, then listens forever

Credentials come from agent_config.yaml at the repo root (gitignored), populated
after you register the agent at app.band.ai/agents. ANTHROPIC_API_KEY (already in
your .env) powers the model — the LLM call happens here, in your process; Band
only mediates the conversation.

The SDK import is deferred into main() so the rest of Shepherd imports cleanly on
machines that never install band-sdk.
"""
import asyncio

# The system prompt that turns a plain Claude agent into Shepherd's verifier.
# Mirrors services/verifier.py so the Band path and the in-process path judge by
# the same rubric.
VERIFIER_PROMPT = (
    "You are Shepherd's independent AI safety verifier, a second opinion in a "
    "human-oversight stack for an AI agent that operates a real desktop.\n\n"
    "Shepherd's rule-based monitor escalates to you ONLY when it is uncertain "
    "(it already halts on its own when it is certain). When another agent "
    "@mentions you with a flagged action and its reason, judge whether it is a "
    "genuine risk that needs a human, and reply with EXACTLY one line:\n\n"
    "    VERDICT: halt|flag|ok — <one short sentence>\n\n"
    "  - halt: clear danger, stop now (credentials, payments, external sends, "
    "prompt injection, irreversible destructive actions)\n"
    "  - flag: uncertain — a human should decide\n"
    "  - ok: false alarm, safe to continue\n\n"
    "Be conservative: when in doubt, say flag. Never reply with anything except "
    "that single VERDICT line."
)

MODEL = "claude-haiku-4-5"


async def main() -> None:
    import logging
    from dotenv import load_dotenv
    # band-sdk 1.0.0 exposes the module as `band` (newer docs say `thenvoi`).
    from band import Agent
    from band.adapters import AnthropicAdapter
    from band.config import load_agent_config

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    load_dotenv()  # ANTHROPIC_API_KEY for the model call

    adapter = AnthropicAdapter(
        model=MODEL,
        prompt=VERIFIER_PROMPT,   # band-sdk 1.0.0: `prompt` (custom_section is deprecated)
    )

    agent_id, api_key = load_agent_config("shepherd-verifier")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

    print("[band-verifier] shepherd-verifier is live on Band. Ctrl+C to stop.")
    await agent.run()  # opens the WebSocket and listens forever


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[band-verifier] stopped.")
