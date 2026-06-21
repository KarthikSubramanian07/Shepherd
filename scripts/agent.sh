#!/usr/bin/env bash
#
# Spin up ONE agent that connects to the persistent backend (./scripts/serve.sh).
# Run this in its own terminal  Agent S needs your screen + Accessibility/Screen
# Recording permissions, and you'll see its reasoning trace here.
#
#   ./scripts/agent.sh                 # default backend at localhost:8765
#   ./scripts/agent.sh --mode LOCKED   # extra flags are passed through to main.py
#
# The agent forwards its events to the backend and polls it for goals submitted
# from the frontend, so backend/frontend keep running while agents come and go.
#
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_URL="${BACKEND_URL:-http://localhost:8765}"

echo "[agent] connecting to backend $BACKEND_URL  listening for goals from the frontend."
echo "[agent] (Ctrl-C to stop just this agent; the backend/frontend keep running.)"
exec env BACKEND_URL="$BACKEND_URL" uv run python main.py --listen "$@"
