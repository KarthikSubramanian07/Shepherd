#!/usr/bin/env bash
#
# One-command Shepherd dev stack: backend + agent + frontend, driven from the browser.
#
#   ./scripts/dev.sh
#
# Starts TWO processes:
#    the agent in --listen mode  -> also serves the backend/API on :8765 and waits
#     for goals from the frontend (no stdin prompt, keeps serving across goals)
#    the Next.js frontend         -> :3000, pre-wired to the local backend
#
# Then open http://localhost:3000/command-center and type a goal in "Run a goal".
# Ctrl-C here stops everything. Logs: /tmp/shepherd/{agent,frontend}.log
#
set -euo pipefail
cd "$(dirname "$0")/.."

BACKEND_PORT=8765
FRONTEND_PORT=3000
LOGDIR=/tmp/shepherd
mkdir -p "$LOGDIR"

free_port() { lsof -ti "tcp:$1" 2>/dev/null | xargs kill -9 2>/dev/null || true; }

echo "[dev] freeing ports $BACKEND_PORT and $FRONTEND_PORT..."
free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

# Point the frontend at the local backend (live WS + REST).
cat > frontend/.env.local <<EOF
NEXT_PUBLIC_API_BASE=http://localhost:$BACKEND_PORT
NEXT_PUBLIC_BACKEND_BASE=http://localhost:$BACKEND_PORT
NEXT_PUBLIC_WS_URL=ws://localhost:$BACKEND_PORT/ws
EOF

if [ ! -d frontend/node_modules ]; then
  echo "[dev] installing frontend deps (first run)..."
  (cd frontend && npm install)
fi

echo "[dev] starting agent + backend (main.py --listen)..."
uv run python main.py --listen > "$LOGDIR/agent.log" 2>&1 &
AGENT_PID=$!

echo "[dev] starting frontend..."
(cd frontend && npm run dev > "$LOGDIR/frontend.log" 2>&1) &
FE_PID=$!

cleanup() {
  echo
  echo "[dev] stopping..."
  kill "$AGENT_PID" "$FE_PID" 2>/dev/null || true
  free_port "$BACKEND_PORT"
  free_port "$FRONTEND_PORT"
  exit 0
}
trap cleanup INT TERM

cat <<EOF

  
    Shepherd is starting up...                                  
      Frontend     ->  http://localhost:$FRONTEND_PORT/command-center      
      Backend/API  ->  http://localhost:$BACKEND_PORT                       
      Logs         ->  $LOGDIR/{agent,frontend}.log            
                                                              
    Type a goal in the "Run a goal" box. Ctrl-C stops all.    
  

EOF

# Stream the agent's reasoning trace; Ctrl-C triggers cleanup() and stops both.
tail -n +1 -f "$LOGDIR/agent.log" &
wait "$AGENT_PID"
