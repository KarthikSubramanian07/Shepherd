#!/usr/bin/env bash
#
# Spin up ONE agent that connects to the persistent backend (./scripts/serve.sh).
# Run this in its own terminal  Agent S needs your screen + Accessibility/Screen
# Recording permissions, and you'll see its reasoning trace here.
#
#   ./scripts/agent.sh                 # default backend at localhost:8765
#   ./scripts/agent.sh --mode LOCKED   # extra flags are passed through to main.py
#
# You type goals at the "Intent ->" prompt here, AND the agent also runs goals
# submitted from the frontend (it forwards events to the backend and polls it).
# Both work at once. Ctrl-C stops just this agent; the backend/frontend keep going.
#
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_URL="${BACKEND_URL:-http://localhost:8765}"

echo "[agent] connected to backend $BACKEND_URL — type a goal below, or send one from the frontend."
echo "[agent] (Ctrl-C stops just this agent; the backend/frontend keep running.)"
exec env BACKEND_URL="$BACKEND_URL" uv run python main.py "$@"
