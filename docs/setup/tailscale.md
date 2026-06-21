# Tailscale Setup

Use Tailscale when Cloudflare Tunnel is blocked (campus WiFi, corporate networks that filter port 7844 or inspect TLS). Tailscale creates a private encrypted mesh — works through any NAT/firewall using DERP relays on port 443.

---

## Why Tailscale

| | Cloudflare Tunnel | Tailscale |
|---|---|---|
| **Blocked by** | Port 7844 filtering, TLS inspection | Almost nothing (uses :443 DERP as fallback) |
| **Exposure** | Public URL (anyone with the link) | Private `100.x.y.z` (not on the internet) |
| **Auth** | Token in URL | Machine-level via Tailscale ACLs |
| **Setup time** | ~30 seconds | ~2 minutes |
| **Free tier** | Unlimited bandwidth | 100 devices, unlimited bandwidth |
| **WebRTC P2P** | Fails (symmetric NAT) | Works (routable Tailscale IPs) |

---

## Install (2 minutes)

### Linux (agent / coordinator machine)

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

### macOS (Command Center laptop)

```bash
brew install tailscale
tailscale up
# Or download from https://tailscale.com/download/mac
```

### Windows

Download from https://tailscale.com/download/windows and run the installer.

---

On first run, a login URL is printed — open it to authenticate. **Both machines must be on the same Tailscale account** (or shared via Tailscale ACLs).

After login, each machine gets a stable Tailscale IP:
```bash
tailscale ip -4
# → 100.64.0.1 (example)

tailscale status
# Shows all machines on your tailnet
```

---

## Topology: Coordinator + Agent ↔ Command Center

The most common setup — agent + coordinator on one machine, operator on another:

```
┌────────────────────────────────────┐              ┌──────────────────────────┐
│  Machine A (100.64.0.1)            │              │  Machine B (100.64.0.2)  │
│  coordinator (:8770)               │◀─Tailscale──│  Command Center          │
│  + main.py --listen                │   encrypted  │  browser → :3000         │
│    (connects to localhost:8770)    │──mesh──────▶│                          │
└────────────────────────────────────┘              └──────────────────────────┘
```

### Machine A (agent + coordinator)

```bash
cd shepherd

# Start coordinator + agent
export COORDINATOR_TOKEN="demo"
export COORDINATOR_PORT=8770
export COORDINATOR_URL="ws://localhost:8770"
export AGENT_PAIRING_CODE="DEMO"
./scripts/start.sh
```

### Machine B (Command Center)

```bash
cd shepherd/frontend

cat > .env.local << 'EOF'
NEXT_PUBLIC_COORDINATOR_URL=http://100.64.0.1:8770
NEXT_PUBLIC_COORDINATOR_TOKEN=demo
EOF

npm install && npm run dev
# Open http://localhost:3000/remote → enter code "DEMO"
```

---

## Verify connectivity

```bash
# From Machine B, check Machine A's coordinator
curl http://100.64.0.1:8770/api/health
# → {"ok":true,"agents":1,"protocol_version":1}

# Check Tailscale connection quality
tailscale ping 100.64.0.1
# → pong from machine-a (100.64.0.1) via DERP(nyc) in 42ms
# (or "via direct" if direct connection established)
```

---

## WebRTC P2P bonus

With Tailscale, both machines have routable IPs → STUN/ICE works without a TURN server:

```bash
# On the agent machine, enable WebRTC P2P:
export WEBRTC_ENABLED=true

# ICE candidates use the Tailscale IP as host candidates
# → direct P2P video stream, bypasses coordinator relay
```

This reduces latency and coordinator CPU for screen streaming.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `tailscale ping` shows "via DERP" | Normal for initial connections — it switches to direct after ~30s if UDP is available |
| `tailscale ping` times out | Ensure both machines are logged into the same tailnet; check `tailscale status` |
| Coordinator unreachable on Tailscale IP | Coordinator must bind to `0.0.0.0` (default) not just `127.0.0.1` |
| Connection works but no frames | Check agent's `DISPLAY` env var and screenshot capability |

---

## Next steps

- Full multi-machine setup details → [multi-machine.md](multi-machine.md)
- Want to try Cloudflare Tunnel instead? → [tunnel.md](tunnel.md)
- Keep it simple on one box → [single-machine.md](single-machine.md)
