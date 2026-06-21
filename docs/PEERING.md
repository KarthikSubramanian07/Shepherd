# Shepherd Peering Guide

How to connect an **Agent** (operated machine) and a **Command Center** (operator UI) through the **Coordinator** relay. This guide treats all three as separate machines — the most general case.

> **Collocating roles:** The coordinator can run on the same machine as the agent OR the Command Center. In practice, "coordinator + agent on one box, Command Center on another" is common (see [Collocated Topologies](#collocated-topologies) below).

---

## Architecture Overview

```
┌──────────────────┐         ┌──────────────────────┐         ┌──────────────────────┐
│   Machine 1      │         │     Machine 2        │         │     Machine 3        │
│   AGENT          │────────▶│     COORDINATOR      │◀────────│   COMMAND CENTER     │
│                  │  WS out │     (relay only)     │  WS out │                      │
│  main.py --listen│         │  coordinator/server  │         │  frontend (Next.js)  │
│  + relay_client  │◀────────│                      │────────▶│  localhost:3000       │
│                  │ commands│  Public URL via       │  roster │                      │
│  Streams screen  │         │  Cloudflare Tunnel   │  events │  Watches live screen │
│  Executes tasks  │         │  or direct port      │  frames │  Sends commands      │
└──────────────────┘         └──────────────────────┘         └──────────────────────┘
```

**Data flows:**
- Agent → Coordinator: screen frames (JPEG base64), event bus events
- Coordinator → Agent: commands (intent, approve, halt, mode, workflow.*)
- Coordinator → Command Center: agent roster, relayed events, relayed frames
- Command Center → Coordinator: commands targeted at specific agents

**Key principle:** The coordinator is a stateless relay — it does NOT execute intents or run workflows. Intent dispatch happens entirely on the agent machine through `main.py`'s engine.

---

## Prerequisites

| Machine | Requirements |
|---------|-------------|
| Coordinator | Python 3.11+, `pip install fastapi uvicorn websockets` (or `uv sync` from the repo) |
| Agent | Python 3.11+, full shepherd repo + deps (`uv sync`), display server (X11) or Playwright for CDP screenshots |
| Command Center | Node.js 18+, `npm install` in `frontend/` |

---

## Step-by-Step: 3 Separate Machines

### 1. Start the Coordinator (Machine 2)

```bash
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd

# Pick a shared token (all 3 machines must use the same one)
export COORDINATOR_TOKEN="your-shared-secret"
export COORDINATOR_PORT=8770

# Start the relay
uv run python -m coordinator
```

**Make it publicly reachable** (choose one):

```bash
# Option A: Cloudflare Tunnel (free, unlimited bandwidth, recommended)
cloudflared tunnel --url http://localhost:8770
# Prints: https://random-words.trycloudflare.com

# Option B: Direct port (if machine has a public IP)
# Just use http://<public-ip>:8770

# Option C: Tailscale (private mesh, no public exposure)
# Use ws://<tailscale-ip>:8770
```

Note the public URL — both the agent and Command Center need it.

### 2. Start the Agent (Machine 1)

```bash
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
uv sync

# Configure connection to coordinator
cat > .env << 'EOF'
COORDINATOR_URL=wss://random-words.trycloudflare.com   # from step 1
COORDINATOR_TOKEN=your-shared-secret                    # same as coordinator
AGENT_PAIRING_CODE=MY-SESSION                           # shared with Command Center
AGENT_ID=my-agent-1
AGENT_NAME=MyAgent
RELAY_FPS=1
RELAY_FRAME_WIDTH=640
RELAY_FRAME_QUALITY=45

# LLM config for Agent S (needed for autonomous execution)
EXECUTION_MODE=AUTONOMOUS
AGENT_S_ENGINE_TYPE=anthropic        # or gemini, openai
AGENT_S_MODEL=claude-haiku-4-5       # or gemini-2.0-flash, gpt-4o
ANTHROPIC_API_KEY=sk-ant-...         # key for the chosen provider
EOF

# Start the agent (full dispatch mode)
DISPLAY=:0 XAUTHORITY=$HOME/.Xauthority uv run python main.py --listen
```

You should see:
```
[agent_s] Ready — anthropic/claude-haiku-4-5 + LLM grounding
[relay] connected to coordinator as 'my-agent-1'
[relay] ┌──────────────────────────────────────────────┐
[relay] │  Command Center session code:  MY-SESSION    │
[relay] └──────────────────────────────────────────────┘
```

### 3. Start the Command Center (Machine 3)

```bash
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd/frontend

# Point at the coordinator
cat > .env.local << 'EOF'
NEXT_PUBLIC_COORDINATOR_URL=https://random-words.trycloudflare.com
NEXT_PUBLIC_COORDINATOR_TOKEN=your-shared-secret
EOF

npm install
npm run dev
```

Open `http://localhost:3000/remote` in a browser:
1. Enter session code: `MY-SESSION`
2. Click **Connect**
3. The agent appears in the Fleet panel
4. Click on it to see its live screen
5. Type a command in "Dispatch a task..." and press Enter

---

## Verifying the Connection

From any machine with `curl`:

```bash
# Health check (shows agent count)
curl "https://random-words.trycloudflare.com/api/health?token=your-shared-secret"
# → {"ok":true,"agents":1,"protocol_version":1}

# Agent roster
curl "https://random-words.trycloudflare.com/api/agents?token=your-shared-secret"
# → [{"id":"my-agent-1","name":"MyAgent","online":true,"code":"MY-SESSION",...}]
```

---

## Collocated Topologies

You don't always need 3 separate machines. Common setups:

### Coordinator + Agent on the same machine (most common)

```
┌─────────────────────────────┐         ┌────────────────────┐
│  Machine A                  │         │  Machine B         │
│  coordinator (port 8770)    │◀────────│  Command Center    │
│  + cloudflared tunnel       │  WS out │  (browser UI)      │
│  + main.py --listen         │────────▶│                    │
│    (connects to localhost)  │         │                    │
└─────────────────────────────┘         └────────────────────┘
```

The agent connects to `ws://localhost:8770` (no tunnel needed for the agent's connection). Only the Command Center uses the public tunnel URL.

```bash
# Machine A: start coordinator
export COORDINATOR_TOKEN="demo" COORDINATOR_PORT=8770
uv run python -m coordinator &

# Machine A: start tunnel
cloudflared tunnel --url http://localhost:8770 &

# Machine A: start agent (connects locally)
export COORDINATOR_URL="ws://localhost:8770"
export COORDINATOR_TOKEN="demo"
export AGENT_PAIRING_CODE="DEMO"
DISPLAY=:0 uv run python main.py --listen
```

### Coordinator + Command Center on the same machine

```
┌────────────────────┐         ┌─────────────────────────────┐
│  Machine A         │         │  Machine B                  │
│  AGENT             │────────▶│  coordinator (port 8770)    │
│  main.py --listen  │◀────────│  + frontend (port 3000)     │
│                    │         │  + cloudflared tunnel       │
└────────────────────┘         └─────────────────────────────┘
```

The Command Center connects to `http://localhost:8770` directly. The agent connects via the tunnel URL.

---

## Command Reference

Commands sent from the Command Center to the agent:

| Command | Payload | Effect on Agent |
|---------|---------|-----------------|
| `intent` | `{"text": "..."}` | Routes through ShepherdIntentRouter → engine executes |
| `approve` | `{}` | Resolves a pending human approval gate |
| `halt` | `{}` | Stops the current execution at the next safe point |
| `mode` | `{"mode": "LIVE"\|"LOCKED"}` | Switches execution mode |
| `override` | `{"instruction": "..."}` | Approves with a steering instruction |
| `workflow.pause` | `{}` | Pauses milestone traversal |
| `workflow.resume` | `{}` | Resumes paused traversal |
| `workflow.intervene` | `{"instruction": "...", ...}` | Steers a running workflow |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| Agent not appearing in roster | Wrong token or session code | Verify `COORDINATOR_TOKEN` and `AGENT_PAIRING_CODE` match on all 3 machines |
| "Waiting for frames" in UI | Agent's screen capture failing | Check `DISPLAY` and `XAUTHORITY` env vars on agent; install `gnome-screenshot` |
| Commands not executing | Agent running `operate.py` instead of `main.py --listen` | Switch to `main.py --listen` for full dispatch |
| "Agent S unavailable" | Missing LLM API key or `gui-agents` not installed | Run `uv sync` and set `AGENT_S_ENGINE_TYPE` + API key in `.env` |
| WebSocket disconnects | Tunnel idle timeout (100s) | Non-issue during active use (frames sent every 1s); if idle, add a ping |
| Tunnel URL changed | `cloudflared` restarted | Update `COORDINATOR_URL` on agent and `NEXT_PUBLIC_COORDINATOR_URL` on frontend |

---

## Security Notes

- The `COORDINATOR_TOKEN` authenticates both agents and UIs. Anyone with the token can connect.
- Session codes (`AGENT_PAIRING_CODE`) scope visibility — agents and UIs must share the same code to see each other.
- For production: use a strong random token, restrict codes to specific sessions, and consider running behind Tailscale (no public exposure).
- The coordinator never sees or stores LLM API keys — those exist only on the agent machine.
