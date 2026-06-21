<div align="center">

<img src="frontend/public/shepherd-mark.png" alt="Shepherd" width="96" />

# Shepherd

### The agent is the part you cannot trust. Shepherd is the layer that lets you trust it anyway.

</div>

Right now, somewhere, an AI agent is clicking through a real interface on
someone's behalf. It is fast. It is capable. And the moment it does something
nobody intended, the only honest answer to "what did it just do?" is a shrug. No
rewind. No record. No proof. People are shipping these into production and
quietly hoping for the best.

Hoping is not a control.

**Shepherd is a local oversight and governance layer for AI desktop agents.** A
shepherd does not cage the flock or walk every step for it. It watches, it knows
the dangerous ground, and it steps in at exactly the right moment. That is the
job here: a configurable monitor, a tamper-evident audit trail, and a human
decision gate sitting between the agent's intent and your machine, without slowing
the agent down. You teach a task by demonstrating it once, watch it run live,
catch it the instant it strays, and replay precisely what it did while you were
away.

It is the first thing you install *alongside* your agent, not instead of it.

---

## The demo in 90 seconds

```
You say:  "send the candidate decision email"
   |
   v  Deepgram transcribes your voice, the router resolves the intent
   |
Agent S opens the mail composer (Simular drives the real desktop)
   |
   v  It is missing context, so it researches the candidate on the live web
   |     (Browserbase cloud browser, reads their GitHub) and drafts the body
   |
The agent moves to Send.
   |
   v  Policy engine (rule-based, < 1 ms): the recipient is external and the body
   |  carries a secret. Verdict: HALT, before a single irreversible click.
   |
Control Hub lights up. You see the milestone graph that replayed node by node,
the reason it stopped, the verifier's second opinion, and the live screen.
   |
   v  You approve, or you say "stop" out loud, and the run halts at the boundary.
   |
Every action, including the halt, is signed into a SHA-256 hash-chain audit log.
```

Change one rule in `data/policy.yaml`, re-run, and the behavior changes
immediately. That is the difference between a demo and a product.

---

## Why demonstration, not a flowchart

Every comparable tool (n8n, Zapier, Make) asks you to build a node graph.
Shepherd asks you to **just do the task once.**

The agent works in the same workspace you do, so demonstrating the task once is
the authoring step. The demonstration is the routine. Each spoken instruction
attaches to the step it was said at, so you stay opinionated where it matters
("when you reach the credential field, stop and ask me") with far less effort
than writing a detailed prompt. Shepherd renders the result as a graph for
oversight and replay, but you author by doing, not by diagramming.

```bash
python main.py --record ROUTINE_FORM_FILL
# Cmd+Shift+M marks each step boundary, Cmd+Shift+Q stops.
# The recorded run becomes the routine's demonstration. Agent S indexes on it.
```

---

## How it works

```
 Voice / typed intent
   -> Deepgram STT (voice to text, boundary only)
   -> Intent router       Redis vector search (BAAI/bge, local) + keyword fallback
   -> Execution engine    LIVE: Agent S plans against the demonstration
                          LOCKED: deterministic verbatim replay (offline floor)
                          AUTONOMOUS: an LLM drafts a plan, then Agent S executes it
        |  (at high-stakes boundaries only, never mid-click)
        v
   3-layer oversight stack
        1. Policy engine    rule-based, always on, < 1 ms (data/policy.yaml)
                            credential / captcha / phishing / external-send triggers,
                            app + domain containment, rate limits
        2. Verifier         independent Claude Haiku second opinion on a flag
        3. Human gate       any surviving flag blocks; approve, halt, or steer.
                            Spoken "stop" fires the same halt path.
        |
        v
   Tamper-evident audit log     SHA-256 hash chain, GET /api/audit/verify
   Telemetry                    Arize Phoenix spans, routine.run -> action.N -> workflow.node
   Memory + crystallization     Redis replay store; runs coalesce into reusable workflows
        |
        v
   Control Hub  (Next.js, http://localhost:3000)
   Command Center, live milestone graph, interventions, runs replay, workflows,
   audit log, policy, remote command center.
```

A single rule runs through the whole codebase: **the click path is sacred.**
Nothing async, networked, or ML-based runs inside a routine's step sequence.
Every integration is invoked between steps, at boundaries, so a network hiccup
or a model stall can never strand the mouse mid-action.

---

## What makes it different

Most agent tools forget a run the second it ends. Shepherd remembers, and it
remembers at the altitude a person thinks at. A finished run is coalesced (off
the hot path, never blocking the next one) into a **crystallized workflow**: the
hundreds of fine clicks collapse into the handful of milestones a human would
actually narrate (open, research, fill, submit), and the graph thickens across
runs, so the second time you ask for something the agent already knows the road.

When you steer a run ("research the projects page before filling this field"),
you can choose to **remember** it. The steer is baked into the workflow as a
conditional, and a finalize gate at run end lets you persist it, save it as a new
workflow, or discard it. The judgment calls future agents inherit become an
explicit decision, not a silent side effect. Browse them all in the Workflows
tab, including the conditionals baked in.

---

## Remote command center

Shepherd can operate another machine. The operated agent dials out to a
coordinator relay (one outbound connection, no inbound ports), and a remote
Command Center watches its live screen beside the workflow graph it builds as it
goes. You can steer or teach it inline, mid-run, and dispatch ad-hoc tasks while
seeing exactly how the vector router resolved them. See `docs/PEERING.md`.

---

## Integrations

| Sponsor | How Shepherd uses it |
|---|---|
| **Simular (Agent S)** | The execution engine. Agent S plans every LIVE action against the demonstration and actuates via pyautogui. The cursor moving on its own is Agent S. |
| **Arize Phoenix** | OpenTelemetry spans on every run, action, and workflow node. `routine.run -> action.N -> workflow.node` nests into one trace. Phoenix caught real coordinate and variable bugs during the build. |
| **Deepgram** | Voice three ways: speak the intent, narrate per-step instructions while recording a demonstration, and say "stop" to halt mid-run. Voice is the authoring tool and the oversight control surface. |
| **Browserbase** | The research digression. Mid-task the agent opens a real cloud browser, reads a live page, and fills from what it found. Degrades to a deterministic local value offline. |
| **Redis** | Beyond caching: vector search for intent routing (Redis 8 vectorset, VADD/VSIM), agent replay memory, and a semantic LLM cache that skips repeat milestone segmentation by meaning. |
| **Sentry** | Error monitoring on a tool performing destructive OS actions. Auto-captures exceptions; a deliberately triggered error shows the full trace. |
| **Band** | Boundary-only multi-agent messaging: the monitor agent and the engine agent collaborate around the run, never inside the click path. |
| **Orkes / Agentspan** | The research digression is a real Agentspan agent. `shepherd-researcher` compiles into a durable workflow on the self-hosted Agentspan server, reasons, and calls a `fetch_page` tool (Browserbase under the hood, so the two compose). Every run leaves a queryable execution. Open-source and keyless locally; the agent reuses the Anthropic key. Falls back to a direct read if the server is down. |
| **Anthropic / Claude Code** | Built end to end with Claude Code. Claude is the verifier (independent second opinion), the milestone segmenter, and the optional reasoning layer behind the router, with a deterministic keyword fallback always retained. |

Every integration is feature-flagged and degrades gracefully. With all flags off,
the core automation and Control Hub run fully offline.

---

## Governance policy

`data/policy.yaml` is the whole oversight contract, hot-reloaded on every
evaluation. No code change needed.

```yaml
screen_rules:
  - name: no-credentials
    match_text: ["password", "api key", "secret key", "ssn"]
    action: halt
    reason: "Credential or sensitive data field detected"

triggers:            # deterministic, planted demo triggers (no OCR needed)
  credential: halt
  external_send: halt
  captcha: halt
  payment: flag

containment:
  allowed_apps: ["Google Chrome", "Safari", "TextEdit"]
  allowed_domains: ["localhost", "workbridge.com", "github.com"]
  max_actions_per_minute: 60
  max_steps_per_run: 100
```

## Audit log

Every action is appended to a SHA-256 hash chain. Change one byte anywhere and
verification pinpoints the break.

```bash
curl localhost:8765/api/audit/verify
# {"valid": true, "entries": 47, "tampered_at": null, "reason": "chain intact"}
```

## Execution modes

`LIVE` (Agent S plans against the demonstration), `LOCKED` (deterministic replay,
the offline demo floor), `AUTONOMOUS` (an LLM drafts a plan from the raw goal,
then Agent S executes it). Switch at runtime with `POST /api/mode/<MODE>` or the
Control Hub sidebar, no restart.

---

## Quick start

```bash
# 1. Install (Python via uv, Node for the Control Hub)
uv sync
cd frontend && npm install && cd ..

# 2. Configure: copy .env.example to .env. ANTHROPIC_API_KEY is the only
#    must-have. Every other key degrades gracefully if absent.
cp .env.example .env

# 3. Supporting services (optional, both degrade gracefully)
redis-server                 # vector routing, memory, semantic cache
uv run phoenix serve         # developer traces at http://localhost:6006

# 4. Run the agent + dashboard backend
uv run python main.py        # Control Hub API + WebSocket on :8765

# 5. Run the Control Hub UI
cd frontend && npm run dev    # http://localhost:3000
```

Open **http://localhost:3000** and speak or type an intent.

## Project layout

```
main.py            Entry loop: intent -> router -> engine -> telemetry + memory
config.py          Typed settings + feature flags (all from .env)
router/            Intent router: Redis vector search + deterministic keyword fallback
engine/            Execution core (the only code that actuates), recorder, task graph,
                   coalescer, milestones, workflow executor + store, routine planner
services/          Boundary integrations: monitor, policy, verifier, deepgram,
                   browserbase, band, embeddings, semantic cache, relay client
telemetry/         Arize Phoenix spans, hash-chain audit log, Redis replay memory
dashboard/         FastAPI: REST + WebSocket event stream
coordinator/       Remote relay so a Command Center can watch + steer a remote agent
frontend/          Next.js Control Hub
data/              routines.json, policy.yaml, workflows, demo pages
```

---

<div align="center">

Built with Claude Code and Devin. The cursor moving is the hook. The audit trail beside it
is the product.

</div>
