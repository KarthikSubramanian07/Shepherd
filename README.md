<div align="center">

<img src="frontend/public/shepherd-mark.png" alt="Shepherd" width="104" />

# Shepherd

### The agent is the part you cannot trust. Shepherd is the layer that lets you trust it anyway.

</div>

Right now, somewhere, an AI agent is clicking through a real interface on
someone's behalf. It is fast. It is capable. And the moment it does something
nobody intended, the only honest answer to "what did it just do?" is a shrug. No
rewind. No record. No proof. Teams are shipping these into production and quietly
hoping for the best.

Hoping is not a control.

**Shepherd is a local oversight and governance layer for AI desktop agents.** A
shepherd does not cage the flock or walk every step for it. It watches, it knows
the dangerous ground, and it steps in at exactly the right moment. That is the
job here: a configurable monitor, a tamper-evident audit trail, and a human
decision gate sitting between the agent's intent and your machine, without slowing
the agent down. You teach a task by demonstrating it once, watch it run live,
catch it the instant it strays, replay precisely what it did while you were away,
and operate it on a machine across the country.

It is the first thing you install *alongside* your agent, not instead of it.

---

## Why now

The agent-capability curve went vertical and the trust curve did not move. Every
month a new model can drive a computer better, and every month the gap widens
between "the agent can do this" and "I would let it do this unattended." The
blocker to deploying agents in anything that matters (a clinic, a finance back
office, a benefits desk) is not capability. It is that a black box touching a
real machine is a liability nobody can sign off on. Shepherd is the missing
control plane: the thing that turns "impressive demo" into "approved in prod."

---

## The demo in 90 seconds

```
You say:  "send the candidate decision email"
   |
   v  Deepgram transcribes your voice, the router resolves the intent
   |
Agent S opens the mail composer (Simular drives the real desktop)
   |
   v  It is missing context, so a durable Agentspan agent researches the
   |     candidate on the live web (Browserbase) and drafts the body
   |
The agent moves to Send.
   |
   v  Policy engine (rule-based, under 1 ms): external recipient, secret in the
   |  body. Verdict: HALT, before a single irreversible click.
   |
Control Hub lights up: the milestone graph that replayed node by node, the reason
it stopped, the independent verifier's second opinion, the live screen.
   |
   v  You approve, or say "stop" out loud, and it halts at the boundary.
   |
Every action, including the halt, is signed into a SHA-256 hash-chain audit log.
```

Change one rule in `data/policy.yaml`, re-run, and the behavior changes
immediately. That is the difference between a demo and a product.

---

## What you get (the Control Hub)

A single console, not a pile of scripts. Every tab is live over WebSocket.

- **Command Center**: dispatch a task by voice or text, watch the live execution
  graph light up milestone by milestone, approve or halt at the gate.
- **Remote**: operate another machine. Its live screen (WebRTC peer-to-peer)
  beside the workflow graph it builds as it goes. Steer or teach it inline.
- **Routines**: the recorded "tools," each a demonstration plus per-step notes.
- **Task Graph**: the crystallized milestone DAG a task has learned across runs.
- **Workflows**: the dispatchable, versioned workflows, including the judgment
  calls baked in from your steers.
- **Runs**: full scrubbable replay of any past run, step by step.
- **Interventions**: the queue of moments that needed a human, and how they
  resolved.
- **Audit**: the tamper-evident hash chain, verifiable in one click.
- **Policy**: the live governance rules.
- **Voice Lab**: the Deepgram transcription surface.

---

## How it works

```
 Voice / typed intent
   -> Deepgram STT (voice to text, boundary only)
   -> Intent router       Redis vector search (BAAI/bge, local) + keyword fallback
   -> Execution engine    LIVE: Agent S plans against the demonstration
                          LOCKED: deterministic verbatim replay (offline floor)
                          AUTONOMOUS: an LLM drafts a plan, then Agent S executes,
                                      reading a per-goal memory graph from past runs
        |  (at high-stakes boundaries only, never mid-click)
        v
   3-layer oversight stack
        1. Policy engine    rule-based, always on, under 1 ms (data/policy.yaml)
                            credential / captcha / phishing / external-send triggers,
                            app + domain containment, action + step rate limits
        2. Verifier         independent Claude second opinion on a flag
        3. Human gate       any surviving flag blocks; approve, halt, or steer.
                            Spoken "stop" fires the same halt path.
        |
        v
   Tamper-evident audit log     SHA-256 hash chain, GET /api/audit/verify
   Telemetry                    Arize Phoenix spans, routine.run -> action.N -> workflow.node
   Memory + crystallization     Redis replay store; runs coalesce into workflows
        |
        v
   Control Hub (Next.js)  +  Remote Command Center (coordinator relay / tunnel)
```

A single rule runs through the whole codebase: **the click path is sacred.**
Nothing async, networked, or ML-based runs inside a routine's step sequence.
Every integration is invoked between steps, at boundaries, so a network hiccup or
a model stall can never strand the mouse mid-action.

---

## The five things that make it a product, not a demo

### 1. You author by doing, not by diagramming

Every comparable tool (n8n, Zapier, Make) asks you to build a node graph.
Shepherd asks you to **just do the task once.** The agent works in the same
workspace you do, so the demonstration *is* the routine. Each spoken instruction
attaches to the step it was said at, so you stay opinionated where it matters
("when you reach the credential field, stop and ask me") with a fraction of the
effort of writing a detailed prompt.

```bash
python main.py --record ROUTINE_FORM_FILL
# Cmd+Shift+M marks each step boundary, Cmd+Shift+Q stops.
```

### 2. The oversight stack catches it before it costs you

Three layers, fastest first. A rule-based policy engine evaluates every
high-stakes screen in under a millisecond (credential fields, captchas, phishing,
outbound sends), enforces app and domain containment, and rate-limits the run. If
it only flags, an independent Claude verifier gives a second opinion and can
upgrade to halt or stand down. Anything still flagged blocks and waits for a
human, who can approve, halt, or steer with a natural-language override. Saying
"stop" out loud fires the same halt path. Two demo flows ship today: a job
application that halts at the credential field, and an email that halts before
sending to an external recipient with a secret in the body. Both are governance
moments, not form-fillers.

### 3. It learns: runs crystallize into reusable workflows

Most agent tools forget a run the second it ends. Shepherd remembers, at the
altitude a person thinks at. Off the hot path, a finished run is coalesced into a
**crystallized workflow**: hundreds of fine clicks collapse into the handful of
milestones a human would narrate (open, research, fill, submit), and the graph
thickens across runs, so the second time you ask, the agent already knows the
road. When you steer a run, you can **remember** it: the steer is baked in as a
conditional, and a finalize gate at run end lets you persist it, save it as a new
workflow, or discard it. The judgment calls future agents inherit are an explicit
decision, not a silent side effect.

### 4. Operate any machine, with no inbound ports

Shepherd runs an agent on a machine across the country as easily as on your own.
The operated agent dials out to a coordinator relay (one outbound connection, no
inbound ports, no VPN), and a remote Command Center watches its **live screen
over WebRTC peer-to-peer** beside the workflow graph it builds in real time. You
dispatch ad-hoc tasks and see exactly how the vector router resolved them, steer
or teach mid-run, and deploy the relay anywhere with a one-command Cloudflare
Tunnel. The full remote-operation and theoretical peering model is in
`docs/PEERING.md`.

### 5. Built on real agent infrastructure, not glue

The execution engine is Simular's Agent S planning against your demonstration.
The research digression is a genuine Agentspan (Orkes) agent that compiles into a
durable workflow on a self-hosted server, reasons, and calls a tool. Intent
routing, replay memory, and a semantic LLM cache all run on Redis. Observability
is real OpenTelemetry into Arize Phoenix. None of this is a screenshot of a logo.

---

## Integrations

| Sponsor | How Shepherd uses it |
|---|---|
| **Simular (Agent S)** | The execution engine. Agent S plans every LIVE action against the demonstration and actuates via pyautogui. The cursor moving on its own is Agent S. |
| **Arize Phoenix** | OpenTelemetry spans on every run, action, and workflow node. `routine.run -> action.N -> workflow.node` nests into one trace. Phoenix caught real coordinate and variable bugs during the build. |
| **Deepgram** | Voice three ways: speak the intent, narrate per-step instructions while recording a demonstration, and say "stop" to halt mid-run. Voice is the authoring tool and the oversight control surface. |
| **Orkes / Agentspan** | The research step is a real Agentspan agent. `shepherd-researcher` compiles into a durable workflow on the self-hosted Agentspan server, reasons, and calls a `fetch_page` tool (Browserbase under the hood). Every run leaves a queryable execution. Open-source, keyless locally, reuses the Anthropic key. |
| **Browserbase** | The agent's hands on the open web. Mid-task it opens a real cloud browser, reads a live page, and fills from what it found. Degrades to a deterministic local value offline. |
| **Redis** | Beyond caching: vector search for intent routing (Redis 8 vectorset, VADD/VSIM), agent replay memory, and a semantic LLM cache that skips repeat milestone segmentation by meaning. |
| **Sentry** | Error monitoring on a tool performing destructive OS actions. Auto-captures exceptions with full traces. |
| **Band** | Boundary-only multi-agent messaging: the monitor agent and the engine agent coordinate around the run, never inside the click path. |
| **Anthropic / Claude Code** | Built end to end with Claude Code. Claude is the verifier, the milestone segmenter, the routine planner, and the Agentspan research brain. |

Every integration is feature-flagged and degrades gracefully. With all flags off,
the core automation and Control Hub run fully offline.

---

## Governance policy

`data/policy.yaml` is the whole oversight contract, hot-reloaded on every
evaluation. No code change, no restart.

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
the offline demo floor), `AUTONOMOUS` (an LLM drafts a plan from the raw goal and
reads a per-goal memory graph, then Agent S executes it). Switch at runtime with
`POST /api/mode/<MODE>` or the Control Hub sidebar, no restart.

---

## Quick start

```bash
# 1. Install (Python via uv, Node for the Control Hub)
uv sync
cd frontend && npm install && cd ..

# 2. Configure: copy .env.example to .env. ANTHROPIC_API_KEY is the only
#    must-have. Every other key degrades gracefully if absent.
cp .env.example .env

# 3. Supporting services (all optional, all degrade gracefully)
redis-server                 # vector routing, memory, semantic cache
uv run phoenix serve         # developer traces at http://localhost:6006
agentspan server start       # durable research agent (open-source, keyless)
```

Then run it, either way:

```bash
# A) One command, everything (backend + agent + frontend)
./scripts/dev.sh

# B) Persistent backend, agents come and go (recommended for a long session)
uv run python -m dashboard.server                 # http://localhost:8765
cd frontend && npm run dev                         # http://localhost:3000
BACKEND_URL=http://localhost:8765 uv run python main.py
```

Open **http://localhost:3000** and speak or type an intent.

## Project layout

```
main.py            Entry loop: intent -> router -> engine -> telemetry + memory
config.py          Typed settings + feature flags (all from .env)
router/            Intent router: Redis vector search + deterministic keyword fallback
engine/            Execution core (the only code that actuates): recorder, task graph,
                   coalescer, milestones, routine planner, workflow executor/store/edit,
                   trace journal, Agent S adapter + grounding
services/          Boundary integrations: monitor, policy, verifier, deepgram,
                   browserbase, band, agentspan research agent, coordinator relay client
telemetry/         Arize Phoenix spans, hash-chain audit log, Redis replay memory
dashboard/         FastAPI: REST + WebSocket event stream
coordinator/       Remote relay + WebRTC signaling so a Command Center can watch + steer
frontend/          Next.js Control Hub
docs/              PEERING.md, PROTOCOL.md, workflow-engine.md
data/              routines.json, policy.yaml, workflows, demo target pages
```

---

<div align="center">

The cursor moving is the hook. The audit trail beside it is the product.

</div>
