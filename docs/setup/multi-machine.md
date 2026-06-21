# Multi-Machine Setup

Run the operator (Command Center) on one machine and the agent on another. The coordinator relays everything between them.

---

## Topology

```
┌─ Operator Machine ──────────────────────┐         ┌─ Operated Machine ──────────┐
│                                         │         │                             │
│  Coordinator (:8770)                    │◀──WS───│  Agent (main.py --listen)   │
│  Frontend (:3000) — /remote view        │         │  relay_client.py            │
│                                         │         │                             │
│  Optionally: Cloudflare Tunnel or       │         │  Captures screen            │
│  Tailscale for remote access            │         │  Executes dispatched tasks  │
└─────────────────────────────────────────┘         └─────────────────────────────┘
```

The coordinator can also be a **third machine** — see [PEERING.md](../PEERING.md) for the full 3-machine topology.

---

## Operator Machine (runs coordinator + Command Center)

```bash
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd

# Start coordinator + frontend (no agent)
./scripts/start.sh --no-agent
```

Note the coordinator address:
- **Same LAN:** `http://<operator-ip>:8770`
- **Tailscale:** `http://<tailscale-ip>:8770` (see [tailscale.md](tailscale.md))
- **Public internet:** Add `--tunnel` flag → get a `https://xxx.trycloudflare.com` URL (see [tunnel.md](tunnel.md))

---

## Operated Machine (runs the agent)

```bash
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
uv sync

# Configure — point at the operator's coordinator
cat > .env << 'EOF'
COORDINATOR_URL=ws://<operator-ip>:8770       # or wss:// for tunnel URL
COORDINATOR_TOKEN=<same-token-as-coordinator>
AGENT_PAIRING_CODE=DEMO

# LLM for autonomous execution
AGENT_S_ENGINE_TYPE=gemini
GEMINI_API_KEY=your-key-here
EOF

# Start the agent
DISPLAY=:0 uv run python main.py --listen
```

The agent:
1. Connects outbound to the coordinator (no incoming ports needed)
2. Pushes its catalog (routines, workflows, task-graphs)
3. Streams its screen at ~1 FPS
4. Waits for dispatched commands from the operator

---

## Operator: Using the Command Center

1. Open `http://localhost:3000/remote` on the operator machine
2. Enter the session code (e.g., `DEMO`)
3. The agent appears in the Fleet panel
4. Click it to see the live screen
5. Type a command and press Enter to dispatch

---

## Verifying connectivity

From the operator machine:
```bash
# Check coordinator is up and agent is connected
curl "http://localhost:8770/api/health"
# → {"ok":true,"agents":1,"protocol_version":1}

# See the agent in the roster
curl "http://localhost:8770/api/agents?token=<your-token>"
```

From the operated machine:
```bash
# Verify the agent can reach the coordinator
curl "http://<operator-ip>:8770/api/health"
```

---

## Common patterns

### Laptop (operator) + Desktop (agent)
Same WiFi, use direct IP: `COORDINATOR_URL=ws://192.168.x.x:8770`

### Laptop (operator) + Cloud VM (agent)
Agent dials out through tunnel: `COORDINATOR_URL=wss://xxx.trycloudflare.com`

### Laptop (operator) + WSL on same laptop
Use localhost: `COORDINATOR_URL=ws://localhost:8770` — effectively single-machine mode

### Multiple agents
Run `main.py --listen` on multiple machines with the same `COORDINATOR_URL` but different `AGENT_PAIRING_CODE` values. The operator sees all agents in the Fleet panel.

---

## Coordinator on a separate (third) machine

If you want the coordinator on its own box (e.g., a VPS):

```bash
# On the coordinator machine:
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
COORDINATOR_TOKEN=your-secret COORDINATOR_PORT=8770 uv run python -m coordinator

# On the operator machine (frontend only):
cd shepherd/frontend
cat > .env.local << 'EOF'
NEXT_PUBLIC_COORDINATOR_URL=http://<coordinator-ip>:8770
NEXT_PUBLIC_COORDINATOR_TOKEN=your-secret
EOF
npm install && npm run dev

# On the agent machine (same as before):
COORDINATOR_URL=ws://<coordinator-ip>:8770 ...
```

See [PEERING.md](../PEERING.md) for the complete 3-machine walkthrough.

---

## Next steps

- Need to punch through a firewall? → [tunnel.md](tunnel.md)
- Campus WiFi blocking Cloudflare? → [tailscale.md](tailscale.md)
- Want everything on one box instead? → [single-machine.md](single-machine.md)
