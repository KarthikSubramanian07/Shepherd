#!/usr/bin/env bash
#
# Shepherd — all-in-one startup.
#
# Launches everything on a single machine:
#   1. Coordinator (:8770)  — relay for remote/multi-machine access
#   2. Backend    (:8765)  — dashboard / Control Hub API
#   3. Frontend   (:3000)  — Next.js UI (serves /command-center and /remote)
#   4. Agent      (main.py --listen) — connects to the coordinator + runs goals
#
# This is the easiest way to get started. The /remote view works via the
# coordinator, and /command-center works directly against :8765.
#
# Usage:
#   ./scripts/start.sh                    # all defaults
#   ./scripts/start.sh --no-agent         # skip launching the agent
#   ./scripts/start.sh --tunnel           # also start a Cloudflare tunnel
#
# Multi-machine: run the coordinator + frontend on machine A (the operator), and
# run the agent with COORDINATOR_URL=<tunnel-url> on machine B (the operated).
# See docs/PEERING.md for details.
#
set -euo pipefail
cd "$(dirname "$0")/.."

COORDINATOR_PORT="${COORDINATOR_PORT:-8770}"
BACKEND_PORT="${BACKEND_PORT:-8765}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
LOGDIR=/tmp/shepherd
mkdir -p "$LOGDIR"

NO_AGENT=false
WITH_TUNNEL=false
for arg in "$@"; do
  case "$arg" in
    --no-agent) NO_AGENT=true ;;
    --tunnel)   WITH_TUNNEL=true ;;
  esac
done

free_port() { lsof -ti "tcp:$1" 2>/dev/null | xargs kill -9 2>/dev/null || true; }

echo "[start] freeing ports $COORDINATOR_PORT, $BACKEND_PORT, $FRONTEND_PORT..."
free_port "$COORDINATOR_PORT"
free_port "$BACKEND_PORT"
free_port "$FRONTEND_PORT"

# ── Frontend env (points at both coordinator and backend) ─────────────────────
cat > frontend/.env.local <<EOF
NEXT_PUBLIC_API_BASE=http://localhost:$BACKEND_PORT
NEXT_PUBLIC_BACKEND_BASE=http://localhost:$BACKEND_PORT
NEXT_PUBLIC_WS_URL=ws://localhost:$BACKEND_PORT/ws
NEXT_PUBLIC_COORDINATOR_URL=http://localhost:$COORDINATOR_PORT
EOF

if [ ! -d frontend/node_modules ]; then
  echo "[start] installing frontend deps (first run)..."
  (cd frontend && npm install)
fi

# ── Start services ────────────────────────────────────────────────────────────
PIDS=()

echo "[start] starting coordinator on :$COORDINATOR_PORT..."
COORDINATOR_PORT="$COORDINATOR_PORT" uv run python -m coordinator.server > "$LOGDIR/coordinator.log" 2>&1 &
PIDS+=($!)

echo "[start] starting backend on :$BACKEND_PORT..."
uv run python -m dashboard.server > "$LOGDIR/backend.log" 2>&1 &
PIDS+=($!)

echo "[start] starting frontend on :$FRONTEND_PORT..."
(cd frontend && npm run dev > "$LOGDIR/frontend.log" 2>&1) &
PIDS+=($!)

if [ "$WITH_TUNNEL" = true ]; then
  echo "[start] starting Cloudflare tunnel for :$COORDINATOR_PORT..."
  PORT="$COORDINATOR_PORT" ./scripts/tunnel.sh > "$LOGDIR/tunnel.log" 2>&1 &
  PIDS+=($!)
fi

# ── Optionally start the agent ────────────────────────────────────────────────
if [ "$NO_AGENT" = false ]; then
  # Small delay to let coordinator start accepting connections.
  sleep 1
  echo "[start] starting agent (main.py --listen) → coordinator..."
  COORDINATOR_URL="http://localhost:$COORDINATOR_PORT" uv run python main.py --listen > "$LOGDIR/agent.log" 2>&1 &
  PIDS+=($!)
fi

# ── Cleanup on exit ───────────────────────────────────────────────────────────
cleanup() {
  echo; echo "[start] stopping all services..."
  for pid in "${PIDS[@]}"; do kill "$pid" 2>/dev/null || true; done
  free_port "$COORDINATOR_PORT"; free_port "$BACKEND_PORT"; free_port "$FRONTEND_PORT"
  exit 0
}
trap cleanup INT TERM

# ── Print summary ─────────────────────────────────────────────────────────────
TUNNEL_URL=""
if [ "$WITH_TUNNEL" = true ]; then
  for i in {1..10}; do
    [ -f .tunnel_url ] && TUNNEL_URL=$(cat .tunnel_url) && break
    sleep 1
  done
fi

cat <<EOF

  ┌────────────────────────────────────────────────────────────┐
  │  Shepherd — all services running                           │
  ├────────────────────────────────────────────────────────────┤
  │  Command Center  →  http://localhost:$FRONTEND_PORT/command-center   │
  │  Remote Control  →  http://localhost:$FRONTEND_PORT/remote           │
  │  Coordinator     →  http://localhost:$COORDINATOR_PORT               │
  │  Backend / API   →  http://localhost:$BACKEND_PORT                   │
EOF

if [ -n "$TUNNEL_URL" ]; then
cat <<EOF
  │  Tunnel (public) →  $TUNNEL_URL    │
EOF
fi

if [ "$NO_AGENT" = false ]; then
cat <<EOF
  │  Agent           →  connected to coordinator (localhost)   │
EOF
else
cat <<EOF
  │  Agent           →  not started (use --no-agent)           │
  │                     Start separately on the operated box:  │
  │                     COORDINATOR_URL=<url> main.py --listen │
EOF
fi

cat <<EOF
  └────────────────────────────────────────────────────────────┘
  Ctrl-C stops everything. Logs in /tmp/shepherd/

EOF

# Tail the coordinator log (most interesting for the operator).
tail -n +1 -f "$LOGDIR/coordinator.log" &
wait "${PIDS[0]}"
