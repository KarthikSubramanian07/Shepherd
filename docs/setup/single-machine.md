# Single-Machine Setup

Run the entire Shepherd stack on one box. This is the fastest path for development, demos, and testing.

---

## Prerequisites

- Python 3.11+
- Node.js 18+
- `uv` (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`

---

## Quick Start

```bash
# 1. Clone and install
git clone https://github.com/KarthikSubramanian07/shepherd.git
cd shepherd
uv sync
cd frontend && npm install && cd ..

# 2. Configure
cp .env.example .env
# Edit .env — at minimum set one LLM key:
#   GEMINI_API_KEY=...        (free tier available)
#   ANTHROPIC_API_KEY=...     (or this)

# 3. Start everything
./scripts/start.sh
```

That's it. Four services launch:

| Service | Port | What it does |
|---------|------|--------------|
| Coordinator | 8770 | Relay between agent and UI |
| Backend (Control Hub) | 8765 | Dashboard API, workflow store |
| Frontend | 3000 | Next.js Command Center UI |
| Agent | — | Connects to coordinator, executes tasks |

---

## Two UI modes

Once running, open your browser:

| URL | Mode | Use case |
|-----|------|----------|
| `http://localhost:3000/command-center` | Direct dispatch | Talks to backend (:8765) directly. Fastest. Shows workflows, routines, task graphs. |
| `http://localhost:3000/remote` | Remote dispatch | Talks through coordinator (:8770). Same UI the remote operator would see. Enter code `DEMO`. |

Both work on the same machine — choose based on what you're testing.

---

## Start script options

```bash
./scripts/start.sh                  # All services (default)
./scripts/start.sh --no-agent       # Skip agent (operator-only mode)
./scripts/start.sh --tunnel         # Also start Cloudflare tunnel for remote access
```

Logs go to `/tmp/shepherd/`. Stop everything with `Ctrl-C` (the script traps SIGINT and cleans up all child processes).

---

## When to use this

- Local development and testing
- Hackathon demos on a single laptop
- Testing the Command Center UI without a second machine
- Verifying the full dispatch pipeline (intent → router → engine → Agent S → screen action)

---

## Next steps

- Want to connect from another machine? → [multi-machine.md](multi-machine.md)
- Need to expose this to the internet? → [tunnel.md](tunnel.md)
- On a restrictive network? → [tailscale.md](tailscale.md)
