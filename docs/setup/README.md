# Shepherd Setup

Choose your deployment scenario:

| Scenario | Guide | When to use |
|----------|-------|-------------|
| **Single Machine** | [single-machine.md](single-machine.md) | Everything on one box — fastest way to demo or develop locally |
| **Multi-Machine (Peer)** | [multi-machine.md](multi-machine.md) | Operator watches/commands from one machine, agent runs on another |
| **Tailscale Peering** | [tailscale.md](tailscale.md) | Restrictive network (campus WiFi, corporate firewall) blocks Cloudflare |
| **Cloudflare Tunnel** | [tunnel.md](tunnel.md) | Need a public URL without port forwarding |

---

## How the pieces fit together

Shepherd has three roles that can be placed on 1, 2, or 3 machines:

```
┌──────────────┐       ┌──────────────────┐       ┌──────────────────┐
│    AGENT     │──WS──▶│   COORDINATOR    │◀──WS──│  COMMAND CENTER  │
│              │       │    (relay)       │       │    (Next.js UI)  │
│ main.py      │       │ coordinator/     │       │ frontend/        │
│ --listen     │       │ server.py        │       │ localhost:3000   │
│              │◀──────│                  │──────▶│                  │
│ Executes     │ cmds  │ Relays frames,   │ roster│ Watches screen,  │
│ tasks on     │       │ events, commands │ events│ dispatches tasks │
│ this screen  │       │ Port 8770        │ frames│                  │
└──────────────┘       └──────────────────┘       └──────────────────┘
```

**The coordinator is a pure relay** — it never executes tasks. All intent dispatch happens on the agent via `main.py`'s `ShepherdIntentRouter`.

---

## Quick decision tree

```
Do you want everything on one machine?
├── YES → single-machine.md (./scripts/start.sh)
└── NO → Are your machines on the same network?
    ├── YES (same LAN / Tailscale) → multi-machine.md
    └── NO (different networks)
        ├── Can you use Cloudflare Tunnel? → tunnel.md + multi-machine.md
        └── Network blocks port 7844? → tailscale.md + multi-machine.md
```

---

## Environment variables (all scenarios)

| Variable | Default | Purpose |
|----------|---------|---------|
| `COORDINATOR_PORT` | `8770` | Coordinator listen port |
| `COORDINATOR_TOKEN` | (none) | Shared auth token (all roles must match) |
| `COORDINATOR_URL` | — | Agent: coordinator address to dial into |
| `AGENT_PAIRING_CODE` | `DEMO` | Session code (agent + UI must match) |
| `GEMINI_API_KEY` | — | LLM key for Agent S (Gemini) |
| `ANTHROPIC_API_KEY` | — | LLM key for Agent S (Anthropic) |
| `AGENT_S_ENGINE_TYPE` | `gemini` | Which LLM provider to use |
| `WEBRTC_ENABLED` | `false` | P2P video (works with Tailscale, needs TURN otherwise) |
| `CATALOG_STORE_PATH` | `data/catalog_cache.json` | Where coordinator persists agent catalogs |
| `NEXT_PUBLIC_COORDINATOR_URL` | `http://localhost:8770` | Frontend: coordinator address |
| `NEXT_PUBLIC_COORDINATOR_TOKEN` | — | Frontend: coordinator auth token |
| `NEXT_PUBLIC_BACKEND_BASE` | `http://localhost:8765` | Frontend: local Control Hub address |

---

## Related docs

- [PEERING.md](../PEERING.md) — Detailed 3-machine topology, command reference, troubleshooting
- [PROTOCOL.md](../PROTOCOL.md) — WebSocket message format and protocol version
- [DEVIN_TESTING.md](../DEVIN_TESTING.md) — Running E2E tests with Devin child sessions
