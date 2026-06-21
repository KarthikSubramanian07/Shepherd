"""
A Shepherd oversight-council specialist, as a Band agent.

Run one specialist per process (each registered at app.band.ai/agents with its own
credentials in agent_config.yaml, keyed by handle `shepherd-<role>`):

    uv pip install "band-sdk[anthropic]"
    python -m services.band_council_agent security
    python -m services.band_council_agent privacy
    python -m services.band_council_agent destructive

Each connects to the oversight room, waits to be @mentioned by the chair, judges
the flagged action through its specialty, and replies with one VOTE line. The
chair (the engine, as shepherd-monitor) tallies the votes. ANTHROPIC_API_KEY
powers the model locally; Band only mediates the conversation.

The SDK import is deferred into main() so the rest of Shepherd imports cleanly on
machines that never install band-sdk.
"""

import asyncio
import sys

from services.council_prompts import ROLES, prompt_for

MODEL = "claude-haiku-4-5"
# The chair the specialists @mention so it can read their votes. The engine posts
# as shepherd-monitor; fall back to the verifier handle's "monitor" sibling.
CHAIR_HANDLE = "shepherd-monitor"


async def main(role: str) -> None:
    import logging
    from dotenv import load_dotenv

    from band import Agent
    from band.adapters import AnthropicAdapter
    from band.config import load_agent_config

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_dotenv()

    adapter = AnthropicAdapter(model=MODEL, prompt=prompt_for(role, CHAIR_HANDLE))
    agent_id, api_key = load_agent_config(f"shepherd-{role}")
    agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)

    print(f"[band-council] shepherd-{role} specialist is live on Band. Ctrl+C to stop.")
    await agent.run()


if __name__ == "__main__":
    role = sys.argv[1] if len(sys.argv) > 1 else ""
    if role not in ROLES:
        print(f"usage: python -m services.band_council_agent <{'|'.join(ROLES)}>")
        sys.exit(2)
    try:
        asyncio.run(main(role))
    except KeyboardInterrupt:
        print(f"\n[band-council] shepherd-{role} stopped.")
