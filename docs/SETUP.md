# Shepherd Setup Guide

## Quick Start (Single Machine)

The simplest way to run Shepherd — everything on one box:

```bash
# 1. Clone and install
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
uv sync          # Python deps (includes gui-agents for Agent S)
cd frontend && npm install && cd ..

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set GEMINI_API_KEY or ANTHROPIC_API_KEY

# 3. Start everything
./scripts/start.sh
```

This launches:
| Service       | Port  | Purpose                              |
|---------------|-------|--------------------------------------|
| Coordinator   | 8770  | Relay between operator & agent       |
| Backend       | 8765  | Control Hub / Dashboard API          |
| Frontend      | 3000  | Next.js UI                           |
| Agent         | —     | Connects to coordinator, runs goals  |

**Two UI modes are available:**
- `http://localhost:3000/command-center` — direct dispatch (talks to :8765 directly)
- `http://localhost:3000/remote` — remote dispatch (talks through coordinator at :8770)

Both work in single-machine mode. The remote view is what you'd use from a separate operator machine.

### Start Options

```bash
./scripts/start.sh                  # all services + agent
./scripts/start.sh --no-agent       # operator-only (coordinator + frontend)
./scripts/start.sh --tunnel         # also start Cloudflare tunnel for public URL
```

---

## Multi-Machine Setup

When the operator and the operated machine are separate:

### Machine A (Operator — watches + dispatches)

```bash
./scripts/start.sh --no-agent --tunnel
# Note the tunnel URL printed, e.g. https://abc-xyz.trycloudflare.com
```

Or without a tunnel (e.g. same LAN or Tailscale):
```bash
./scripts/start.sh --no-agent
# Coordinator is at http://<machine-a-ip>:8770
```

### Machine B (Operated — runs goals)

```bash
# Point the agent at the coordinator on Machine A:
COORDINATOR_URL=https://abc-xyz.trycloudflare.com uv run python main.py --listen

# Or over LAN/Tailscale:
COORDINATOR_URL=http://100.x.y.z:8770 uv run python main.py --listen
```

The agent dials into the coordinator, pushes its catalog (routines, workflows, task-graphs), and starts streaming its screen. The operator sees it in the /remote UI.

### Machine C (Optional — additional agents)

Same as Machine B. Multiple agents can connect to one coordinator, each gets its own entry in the Fleet roster.

---

## Connectivity Options

| Method            | When to use                            | Setup                         |
|-------------------|----------------------------------------|-------------------------------|
| localhost         | Everything on one machine              | `./scripts/start.sh`          |
| LAN IP            | Same network, no firewall issues       | Use `http://<ip>:8770`        |
| Tailscale         | Different networks, restrictive WiFi   | `tailscale up` on both sides  |
| Cloudflare Tunnel | Quick public URL, no port forwarding   | `--tunnel` flag               |

See [docs/PEERING.md](PEERING.md) for detailed topology diagrams and Tailscale setup.

---

## Environment Variables

| Variable                         | Default           | Purpose                                    |
|----------------------------------|-------------------|--------------------------------------------|
| `COORDINATOR_PORT`               | `8770`            | Coordinator listen port                    |
| `COORDINATOR_TOKEN`              | (empty)           | Auth token (if set, agents must match)     |
| `COORDINATOR_URL`                | —                 | Agent: where to dial in                    |
| `GEMINI_API_KEY`                 | —                 | For Agent S (vision-based planning)        |
| `ANTHROPIC_API_KEY`              | —                 | Alternative LLM backend                    |
| `AGENT_S_ENGINE_TYPE`            | `gemini`          | Which LLM: gemini, anthropic, openai       |
| `WEBRTC_ENABLED`                 | `false`           | P2P video (needs TURN for cloud VMs)       |
| `CATALOG_STORE_PATH`             | `data/catalog_cache.json` | Where coordinator persists catalogs |
| `NEXT_PUBLIC_COORDINATOR_URL`    | `http://localhost:8770`   | Frontend coordinator address       |
| `NEXT_PUBLIC_BACKEND_BASE`       | `http://localhost:8765`   | Frontend backend address           |

---

## Architecture Overview

```
┌─ Operator Machine ─────────────────────────────────┐
│                                                     │
│  ┌─────────┐      ┌─────────────┐                  │
│  │ Browser │─────▶│  Frontend   │ :3000             │
│  │ (/remote)│      │  (Next.js)  │                  │
│  └─────────┘      └──────┬──────┘                  │
│                           │ WS + REST               │
│                    ┌──────▼──────┐                  │
│                    │ Coordinator │ :8770             │
│                    │  (relay)    │                  │
│                    └──────┬──────┘                  │
│                           │ WS (tunnel/LAN/VPN)     │
└───────────────────────────┼─────────────────────────┘
                            │
┌─ Operated Machine ────────┼─────────────────────────┐
│                    ┌──────▼──────┐                  │
│                    │relay_client │                  │
│                    │ (connects   │                  │
│                    │  outbound)  │                  │
│                    └──────┬──────┘                  │
│                           │                         │
│                    ┌──────▼──────┐                  │
│                    │  main.py    │ (Agent + Engine)  │
│                    │  + :8765    │ (Control Hub)     │
│                    └─────────────┘                  │
└─────────────────────────────────────────────────────┘
```

In single-machine mode, both boxes collapse into one — the coordinator, backend, frontend, and agent all run on `localhost`.

---

## Catalog Persistence

When an agent connects to the coordinator, it pushes its current catalog (routines, workflows, task-graphs). The coordinator persists this to `data/catalog_cache.json` so:
- The catalog survives coordinator restarts
- The UI can show the last-known catalog even if the agent is temporarily offline
- Version tracking (monotonic counter) lets the UI detect stale data

The catalog is refreshed every time the agent reconnects.
