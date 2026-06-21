#!/usr/bin/env bash
#
# Shepherd Coordinator Tunnel — cloudflared quick-tunnel with auto-restart.
#
# Exposes the local coordinator (default localhost:8770) via a Cloudflare Quick
# Tunnel. The URL is random but stable for the lifetime of the process. If
# cloudflared crashes or the connection drops, this script restarts it
# automatically and prints the new URL.
#
# Usage:
#   ./scripts/tunnel.sh              # expose localhost:8770
#   ./scripts/tunnel.sh 9000         # expose localhost:9000
#   COORDINATOR_PORT=8770 ./scripts/tunnel.sh
#
# The script writes the current tunnel URL to .tunnel_url in the repo root so
# other scripts/processes can read it programmatically.
#
# Requirements:
#   - cloudflared (https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
#     Install: brew install cloudflared  OR  sudo apt install cloudflared
#              OR  curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

set -euo pipefail

PORT="${1:-${COORDINATOR_PORT:-8770}}"
URL_FILE="$(cd "$(dirname "$0")/.." && pwd)/.tunnel_url"
RESTART_DELAY=3

echo "[tunnel] Exposing localhost:$PORT via Cloudflare Quick Tunnel"
echo "[tunnel] URL file: $URL_FILE"
echo "[tunnel] Press Ctrl-C to stop"
echo ""

cleanup() {
    # Kill the cloudflared pipeline (CF_PID is the tee, but killing it
    # breaks the pipe causing cloudflared to receive SIGPIPE).
    [ -n "${CF_PID:-}" ] && kill "$CF_PID" 2>/dev/null
    # Also kill any child cloudflared processes directly.
    pkill -P $$ 2>/dev/null || true
    rm -f "$URL_FILE"
    echo ""
    echo "[tunnel] Shut down."
}
trap cleanup EXIT

while true; do
    # cloudflared prints the URL to stderr. We capture it with a temp file + tee.
    LOGFILE=$(mktemp)

    # Run cloudflared in the background, tee stderr so we can parse the URL.
    cloudflared tunnel --url "http://localhost:$PORT" 2>&1 | tee "$LOGFILE" &
    CF_PID=$!

    # Wait for the URL to appear in the logs (up to 30s).
    TUNNEL_URL=""
    for i in $(seq 1 60); do
        sleep 0.5
        TUNNEL_URL=$(grep -oP 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOGFILE" 2>/dev/null | head -1 || true)
        if [ -n "$TUNNEL_URL" ]; then
            break
        fi
    done

    if [ -n "$TUNNEL_URL" ]; then
        echo "$TUNNEL_URL" > "$URL_FILE"
        echo ""
        echo "=============================================="
        echo "  COORDINATOR URL: $TUNNEL_URL"
        echo "=============================================="
        echo ""
        echo "  Set in frontend/.env.local:"
        echo "    NEXT_PUBLIC_COORDINATOR_URL=$TUNNEL_URL"
        echo ""
        echo "  Set on operated machine:"
        echo "    COORDINATOR_URL=$TUNNEL_URL"
        echo ""
        echo "=============================================="
        echo ""
    else
        echo "[tunnel] WARNING: could not detect tunnel URL within 30s"
    fi

    # Wait for cloudflared to exit (crash, network drop, etc.)
    wait $CF_PID 2>/dev/null || true
    rm -f "$LOGFILE"

    echo "[tunnel] cloudflared exited — restarting in ${RESTART_DELAY}s..."
    sleep "$RESTART_DELAY"
done
