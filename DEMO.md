# Shepherd Remote Orchestration Demo

Demonstrate one machine monitoring and operating another through the Shepherd Coordinator relay.

Three deployment topologies are documented below:

| Topology | Cost | Exposure | Best for |
|----------|------|----------|----------|
| **Cloudflare Tunnel** (recommended) | Free | Public `wss://`, unlimited BW | Hackathons, demos, any network |
| **Tailscale private mesh** | Free | None (private) | Development, same-tailnet teams |
| **Hosted coordinator** (VPS + Caddy) | ~€4/mo | Public `wss://` | Permanent public deployments |

---

## Prerequisites

All topologies need these on the **operated machine**:

```bash
# Clone the repo and set up the Python environment
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Optional: install Playwright for browser automation
playwright install chromium
```

---

## Topology 1: Cloudflare Tunnel (Recommended — Free, Unlimited Bandwidth)

Zero config, no domain needed, unlimited bandwidth, automatic TLS + WebSocket support. The coordinator runs on your machine; Cloudflare tunnels traffic to it from a public URL.

### Why This Works

Cloudflare Quick Tunnels give you a random `https://*.trycloudflare.com` URL that proxies all traffic (including WebSockets) to your local port — for free, with no bandwidth cap. The URL stays stable as long as `cloudflared` is running. A keepalive wrapper script auto-restarts it if it drops.

### Machines

| Role | Description |
|------|-------------|
| **Machine A** (coordinator + operated) | Runs the coordinator, the operated agent, AND `cloudflared`. |
| **Machine B** (operator) | Runs the Command Center UI in a browser. Connects to the tunnel URL. |

### Step 1: Install cloudflared (Machine A)

```bash
# Linux (Debian/Ubuntu)
curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
  -o /usr/local/bin/cloudflared && chmod +x /usr/local/bin/cloudflared

# macOS
brew install cloudflare/cloudflare/cloudflared

# Windows — download from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
```

### Step 2: Start the Coordinator (Machine A)

```bash
cd shepherd

export COORDINATOR_TOKEN="my-demo-secret"
export COORDINATOR_PORT=8770

# Start the coordinator
.venv/bin/python -m coordinator
```

### Step 3: Start the Tunnel (Machine A, second terminal)

```bash
cd shepherd

# Option A: One-liner (restarts manually if it dies)
cloudflared tunnel --url http://localhost:8770

# Option B: Auto-restart wrapper (recommended)
./scripts/tunnel.sh
```

The script prints the public URL:

```
==============================================
  COORDINATOR URL: https://random-words-here.trycloudflare.com
==============================================

  Set in frontend/.env.local:
    NEXT_PUBLIC_COORDINATOR_URL=https://random-words-here.trycloudflare.com

  Set on operated machine:
    COORDINATOR_URL=https://random-words-here.trycloudflare.com
```

The URL is also written to `.tunnel_url` in the repo root for programmatic access.

### Step 4: Start the Operated Agent (Machine A, third terminal)

```bash
cd shepherd

export COORDINATOR_URL="$(cat .tunnel_url)"    # or paste the URL manually
export COORDINATOR_TOKEN="my-demo-secret"
export AGENT_PAIRING_CODE="DEMO"
export TARGET_URL="https://example.com"

DISPLAY=:0 .venv/bin/python scripts/operate.py
```

Verify the agent registered:

```bash
curl "$(cat .tunnel_url)/api/health"
# → {"ok":true,"agents":1,"protocol_version":1}
```

### Step 5: Connect the Command Center (Machine B)

```bash
cd shepherd/frontend

# Point the UI at the tunnel URL
echo 'NEXT_PUBLIC_COORDINATOR_URL=https://random-words-here.trycloudflare.com' >> .env.local
echo 'NEXT_PUBLIC_COORDINATOR_TOKEN=my-demo-secret' >> .env.local

npm install && npm run dev
```

Open `http://localhost:3000/remote` in a browser. Enter session code `DEMO`. You should see Machine A's live screen and can send commands.

### Notes

- **Idle timeout**: Cloudflare free plan closes WebSockets after 100s of no traffic. The relay client sends frames every ~0.3s, so this is a non-issue during active use. If the agent is idle, the WebSocket ping/pong (every 20s) keeps it alive.
- **URL stability**: The URL stays the same as long as `cloudflared` is running. The `scripts/tunnel.sh` wrapper auto-restarts on crash and prints the new URL.
- **No account needed**: Quick tunnels work without a Cloudflare account. For a persistent subdomain (same URL across restarts), create a free account + add a domain to Cloudflare DNS.

---

## Topology 2: Tailscale Private Mesh (Free, Private)

No public exposure, no hosting costs, no TLS certificates to manage. Both machines communicate over Tailscale's encrypted WireGuard mesh using private `100.x.y.z` IPs.

### Machines

| Role | Description |
|------|-------------|
| **Machine A** (coordinator + operated) | Runs the coordinator AND the operated agent. Its screen is streamed. |
| **Machine B** (operator) | Runs the Command Center UI. Watches and steers Machine A. |

> You can also split the coordinator onto a third machine — the topology is flexible.

### Step 1: Install Tailscale on Both Machines

```bash
# Linux (Debian/Ubuntu)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# macOS
brew install tailscale
# Or download from https://tailscale.com/download/mac
```

After `tailscale up` on both machines, note each machine's Tailscale IP:

```bash
tailscale ip -4
# → ex. 100.64.0.1 (Machine A), 100.64.0.2 (Machine B)
```

### Step 2: Start the Coordinator (Machine A)

```bash
cd shepherd

# Pick a shared secret and pairing code
export COORDINATOR_TOKEN="my-demo-secret"
export COORDINATOR_PORT=8770

# Run the coordinator
.venv/bin/python -m coordinator
```

Verify it's up:

```bash
# From either machine (replace with Machine A's Tailscale IP)
curl http://100.64.0.1:8770/api/health
# → {"ok":true,"agents":0,"protocol_version":1}
```

### Step 3: Start the Operated Agent (Machine A)

In a second terminal on Machine A:

```bash
cd shepherd

export COORDINATOR_URL="ws://100.64.0.1:8770"
export COORDINATOR_TOKEN="my-demo-secret"
export AGENT_PAIRING_CODE="DEMO"
export TARGET_URL="https://example.com"  # or any app URL

DISPLAY=:0 .venv/bin/python scripts/operate.py
```

Verify the agent registered:

```bash
curl http://100.64.0.1:8770/api/health
# → {"ok":true,"agents":1,"protocol_version":1}
```

### Step 4: Connect the Command Center (Machine B)

```bash
cd shepherd/frontend

# Point the UI at Machine A's coordinator
echo 'NEXT_PUBLIC_COORDINATOR_URL=http://100.64.0.1:8770' >> .env.local
echo 'NEXT_PUBLIC_COORDINATOR_TOKEN=my-demo-secret' >> .env.local

npm install && npm run dev
```

Open `http://localhost:3000/remote` in a browser. Enter session code `DEMO`. You should see Machine A's live screen and can send commands.

### Step 5: Verify Token Rejection

```bash
# Wrong token → connection refused (close code 4401)
python -c "
import asyncio, websockets
async def test():
    try:
        async with websockets.connect('ws://100.64.0.1:8770/agent?token=WRONG') as ws:
            print('ERROR: should have been rejected')
    except websockets.exceptions.ConnectionClosed as e:
        print(f'Correctly rejected: code={e.code}')
asyncio.run(test())
"
# → Correctly rejected: code=4401
```

### WSL2 Note (Windows Users)

WSL2 runs behind its own NAT layer, which means:

- **Tailscale inside WSL2** uses userspace networking by default and may not route correctly to the host or other tailnet peers.
- **Recommended**: Install and run Tailscale on the **Windows host** (not inside WSL2). The coordinator can then bind to the Windows host's Tailscale IP.

If you must run inside WSL2:

```bash
# Start tailscaled with userspace networking (no iptables)
sudo tailscaled --tun=userspace-networking &
sudo tailscale up
```

Then access the coordinator from WSL2 using the Windows host's Tailscale IP (find it with `tailscale ip -4` on the Windows side).

Alternatively, run the coordinator on the Windows host directly:

```powershell
# PowerShell on Windows host
cd shepherd
python -m coordinator
```

And point WSL2's agent/UI at the Windows host's Tailscale IP.

---

## Topology 3: Hosted Coordinator (Public Internet)

For deployments where the operated machine and operator are on different networks without Tailscale, host the coordinator on a VPS with a public IP and TLS termination.

### Option A: VPS + Caddy (Provider-Agnostic, ~€4/mo)

Any Linux VPS works. [Hetzner](https://hetzner.com) at ~€4/mo offers generous egress (20 TB), making it ideal for frame streaming. DigitalOcean, Vultr, Linode, etc. all work too.

#### On the VPS:

```bash
# 1. Install Docker
curl -fsSL https://get.docker.com | sh

# 2. Build and run the coordinator
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
docker build -f coordinator/Dockerfile -t shepherd-coordinator .
docker run -d --name coordinator \
  -p 127.0.0.1:8770:8770 \
  -e COORDINATOR_TOKEN="your-secret-token" \
  shepherd-coordinator

# 3. Install Caddy for automatic TLS (one-liner reverse proxy)
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy

# 4. Configure Caddy — replace coordinator.example.com with your domain
#    (point DNS A record to this VPS's IP first)
sudo tee /etc/caddy/Caddyfile << 'EOF'
coordinator.example.com {
    reverse_proxy localhost:8770
}
EOF
sudo systemctl restart caddy
```

That's it. Caddy auto-provisions a TLS certificate via Let's Encrypt. Your coordinator is now at `wss://coordinator.example.com`.

#### On the Operated Machine:

```bash
export COORDINATOR_URL="wss://coordinator.example.com"
export COORDINATOR_TOKEN="your-secret-token"
export AGENT_PAIRING_CODE="DEMO"

DISPLAY=:0 python scripts/operate.py --target-url https://example.com
```

#### On the Operator Machine (Command Center):

```bash
cd frontend
echo 'NEXT_PUBLIC_COORDINATOR_URL=https://coordinator.example.com' >> .env.local
echo 'NEXT_PUBLIC_COORDINATOR_TOKEN=your-secret-token' >> .env.local
npm run dev
# Open http://localhost:3000/remote, enter code DEMO
```

### Option B: Container Platform (Paid)

For managed deployments, you can use any container platform:

| Platform | Notes |
|----------|-------|
| [Railway](https://railway.app) | Deploy from GitHub, auto-TLS, ~$5/mo |
| [Render](https://render.com) | Docker deploy, auto-TLS, free tier for web services |
| [Fly.io](https://fly.io) | Edge deploy, `fly.toml` included in `coordinator/`, ~$3-5/mo |

All platforms need:
1. Build from `coordinator/Dockerfile` (build context = repo root)
2. Set env var `COORDINATOR_TOKEN`
3. Expose port 8770 with TLS termination

See `coordinator/fly.toml` for a Fly.io-specific example config.

---

## Bandwidth Tuning

The live screen stream is the primary bandwidth cost. At defaults (~1-2 Mbps per agent):

| Setting | Default | Low-bandwidth alternative |
|---------|---------|--------------------------|
| `RELAY_FPS` | 3.0 | 1.0 (66% reduction) |
| `RELAY_FRAME_WIDTH` | 1024 | 640 (60% reduction) |
| `RELAY_FRAME_QUALITY` | 55 | 30 (40% reduction) |

Set these as env vars on the **operated machine** (or pass as CLI args to `scripts/operate.py`):

```bash
RELAY_FPS=1 RELAY_FRAME_WIDTH=640 RELAY_FRAME_QUALITY=30 \
  python scripts/operate.py --coordinator ws://100.64.0.1:8770
```

Combined: drops to ~100-200 Kbps per agent.

---

## Quick Reference: The Handshake

Both topologies use the same protocol (see `docs/PROTOCOL.md` for full spec):

1. **Agent connects**: `ws(s)://coordinator/agent?agent_id=ID&code=SESSION&token=SECRET`
2. **Agent sends hello**: `{"type":"hello","name":"...","host":"...","protocol_version":1}`
3. **UI connects**: `ws(s)://coordinator/ui?code=SESSION&token=SECRET`
4. **UI receives roster**: `{"type":"agents","agents":[...]}`
5. **UI sends watch**: `{"type":"watch","agent_id":"ID"}`
6. **UI sends commands**: `{"type":"command","agent_id":"ID","command":"intent","payload":{...}}`

The `code` scopes everything — agents and UIs with the same code see each other. The `token` authenticates both sides against the coordinator.
