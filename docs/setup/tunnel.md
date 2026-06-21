# Cloudflare Tunnel Setup

Use a Cloudflare Tunnel to expose the coordinator to the internet without port forwarding. Free, unlimited bandwidth, no account required for quick tunnels.

---

## When to use

- Operator and agent are on **different networks** (not same LAN, no Tailscale)
- You have a machine with unrestricted outbound internet (port 7844 not blocked)
- You want a public HTTPS URL in seconds without DNS or SSL configuration

**If port 7844 is blocked** (campus WiFi, corporate networks): Use [Tailscale](tailscale.md) instead.

---

## Install cloudflared

```bash
# Linux
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /usr/local/bin/cloudflared
chmod +x /usr/local/bin/cloudflared

# macOS
brew install cloudflared

# Windows
# Download from https://github.com/cloudflare/cloudflared/releases/latest
```

---

## Quick start (with start.sh)

The simplest way — the start script handles the tunnel for you:

```bash
./scripts/start.sh --tunnel
```

This launches all services + a Cloudflare tunnel. The tunnel URL is printed to stdout and written to `.tunnel_url`.

---

## Manual setup

If you want more control:

```bash
# 1. Start the coordinator
COORDINATOR_TOKEN=demo COORDINATOR_PORT=8770 uv run python -m coordinator &

# 2. Start the tunnel pointing at the coordinator
cloudflared tunnel --url http://localhost:8770
# Prints: https://random-words.trycloudflare.com
```

Use the printed URL as `COORDINATOR_URL` on the agent and `NEXT_PUBLIC_COORDINATOR_URL` on the frontend.

---

## Using the tunnel URL

### On the agent machine (remote)

```bash
COORDINATOR_URL=wss://random-words.trycloudflare.com \
COORDINATOR_TOKEN=demo \
AGENT_PAIRING_CODE=DEMO \
uv run python main.py --listen
```

Note: `wss://` (not `ws://`) because Cloudflare tunnels are always HTTPS/WSS.

### On the Command Center (if remote)

```bash
cat > frontend/.env.local << 'EOF'
NEXT_PUBLIC_COORDINATOR_URL=https://random-words.trycloudflare.com
NEXT_PUBLIC_COORDINATOR_TOKEN=demo
EOF
cd frontend && npm run dev
```

---

## The scripts/tunnel.sh helper

The repo includes a helper script that starts a tunnel and waits for the URL:

```bash
PORT=8770 ./scripts/tunnel.sh
# Writes the URL to .tunnel_url and prints it
```

You can also force the `http2` transport protocol (some networks handle it better):

```bash
TUNNEL_TRANSPORT_PROTOCOL=http2 PORT=8770 ./scripts/tunnel.sh
```

---

## Limitations

| Issue | Impact | Workaround |
|-------|--------|------------|
| URL changes on restart | Agent/frontend need updating | Use a named tunnel with a Cloudflare account |
| Port 7844 blocked | Tunnel can't connect to Cloudflare edge | Use [Tailscale](tailscale.md) |
| TLS inspection (middlebox) | Tunnel handshake fails | Use Tailscale or phone hotspot |
| High latency (~200ms overhead) | Noticeable in screen streaming | Use Tailscale for lower latency |

---

## Named tunnels (persistent URL)

For a stable URL that doesn't change on restart, create a named tunnel (requires free Cloudflare account):

```bash
cloudflared tunnel login
cloudflared tunnel create shepherd
cloudflared tunnel route dns shepherd shepherd-relay.yourdomain.com
cloudflared tunnel run --url http://localhost:8770 shepherd
```

---

## Next steps

- Full multi-machine topology → [multi-machine.md](multi-machine.md)
- Tunnel blocked? → [tailscale.md](tailscale.md)
- Everything on one box? → [single-machine.md](single-machine.md)
