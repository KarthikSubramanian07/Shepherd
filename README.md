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
- **Task Graph**: the crystallized milestone DAG a task has learned across runs,
  with the modal execution path, branch entropy, and transition odds drawn on it.
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
   -> Intent router       (USE_ROUTER=true) Redis vector search (BAAI/bge, local)
                          + keyword fallback; else skipped
   -> Execution engine    autonomous (default): an LLM drafts a plan, then Agent S
                                      executes it screenshot-by-screenshot
                          routed + ROUTINE_REPLAY=vision: Agent S plans against the demo
                          routed + ROUTINE_REPLAY=deterministic: verbatim replay (offline floor)
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

### 1. Two ways to hand it a task: show it once, or just say it

For a task you repeat, **demonstrate it once** and the demonstration becomes the
routine. No node graph, no Zapier-style flowchart. The agent works in the same
workspace you do, so doing the task once *is* the authoring step, and each spoken
instruction attaches to the step it was said at ("when you reach the credential
field, stop and ask me").

```bash
python main.py --record ROUTINE_FORM_FILL   # Cmd+Shift+M marks a step, Cmd+Shift+Q stops
```

For anything else, switch to **AUTONOMOUS and just say the goal.** There is no
recorded routine and no script: the planner drafts a plan from your words and
Agent S drives whatever app the goal needs. "Take my selfie from Photo Booth."
"Play lo-fi on YouTube." "Find the cheapest flight to NYC and start the booking."
It opens the app, reads the screen, and goes, recording a per-goal milestone graph
of the run (which it can recall to plan faster next time when `AUTONOMOUS_USE_MEMORY`
is on). This is the difference between a macro
recorder and an actual agent: it generalizes to tasks nobody scripted. And the
part that matters most, **the same oversight stack watches an autonomous run
exactly as it watches a recorded one.** Open-ended capability, still on a leash.

### 2. The oversight stack catches it before it costs you

Defense in depth, fastest first, each layer independent so a miss in one is caught
by the next.

**Pre-flight, before the first click: cryptographic intent authorization.** When
ArmorIQ is on, the resolved plan is captured at the run boundary and ArmorIQ
issues a **cryptographically-signed intent token** gated by an allow/deny policy
derived from the same containment rules. The plan is authorized as a whole before
a single action runs, and the signed token is intent-level proof that sits beside
the audit chain. A tenant-policy denial halts the run before it starts.

**Per high-stakes screen, under a millisecond: the rule-based policy engine.** It
evaluates credential fields, captchas, phishing, and outbound sends, enforces app
and domain containment, and rate-limits the run, all from a hot-reloaded
`data/policy.yaml`.

**On a flag, a genuinely independent second opinion.** A separate Claude verifier
re-examines the screen and can upgrade a flag to a halt or stand down. This is not
just an in-process call: when Band is on, the second opinion is a **real
two-agent collaboration over Band's agentic mesh**: the engine posts the flagged
action into a shared room, an independent `shepherd-verifier` agent reasons and
replies with its verdict, and the engine reads it back (live round-trip verified).
If Band is offline it degrades to the identical in-process check.

**Anything still flagged waits for a human**, who can approve, halt, or steer with
a natural-language override. Saying "stop" out loud fires the same halt path. Two
demo flows ship today: a job application that halts at the credential field, and
an email that halts before sending to an external recipient with a secret in the
body. Both are governance moments, not form-fillers.

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

That crystallized graph is not a doodle, it is a measured object. We treat the
runs as a **Markov process** over milestones: every edge carries its transition
probability from how often the agent took it, and **Viterbi** (max-product
dynamic programming) recovers the single most-likely path through the whole
workflow, which the UI highlights so you can see at a glance what "normally"
happens. Each decision point reports its **Shannon entropy** in bits, the honest
measure of how branchy a choice really is (0 bits is deterministic, 1 bit is a
true coin flip), and branch and merge points fall out of the in/out degree. The
layout itself is the **Sugiyama** algorithm: nodes are assigned to layers by
longest-path rank, then ordered with iterated **median / barycenter** sweeps to
minimize edge crossings, so a graph with real structure reads as one. The whole
analysis is in `frontend/src/lib/graph-analysis.ts`. The point is not the math
for its own sake: it is that you can look at a learned task and immediately tell
the rote part from the part where judgment lives.

The **oversight learns too.** Every step accumulates success / failure / halt /
deviation stats across runs, surfaced as per-step confidence in the UI, and a
step that keeps proving risky is **auto-promoted into the monitored set**, so the
safety net tightens exactly where reality showed it should. And in LIVE mode,
**deviation detection** compares what Agent S actually did against the
demonstration and flags drift the moment the agent goes off-script, before it
compounds.

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
durable workflow on a self-hosted server, reasons, and calls a tool, leaving a
queryable execution behind. The oversight verifier can be a separate Claude agent
on Band's mesh. Run authorization is a real ArmorIQ intent token. Three things run
on Redis: vector intent routing (BGE embeddings over a Redis 8 vector set), agent
replay memory, and a **semantic LLM cache that hits by meaning, not by key** so a
paraphrased goal reuses a prior milestone segmentation. Observability is real
OpenTelemetry into Arize Phoenix. Every one of these was exercised live during the
build, not stubbed: none of it is a screenshot of a logo.

---

## Integrations

Ordered by how load-bearing each one actually is in the code. **Status** says what
it takes to light up: _Core_ (always on, the product needs it), _On by default_,
_Key-gated_ (needs a credential, else a graceful fallback runs), _Off by default_,
or _Build-time_ (used to write the code, nothing runs at runtime).

| Sponsor | Status | How Shepherd actually uses it |
|---|---|---|
| **Simular (Agent S)** | Core | The execution engine — the only code that actuates. Real `gui-agents` AgentS3 (`engine/agent_s_adapter.py`): it plans each LIVE and autonomous action from a screenshot and drives the desktop via pyautogui. The cursor moving on its own is Agent S. Nothing else here clicks. |
| **Anthropic / Claude** | Core | The cognitive layer. Claude is the independent **verifier** (`services/verifier.py`) and the autonomous **routine planner** (`engine/routine_planner.py`), and the model behind the **Agentspan researcher**. It can also drive milestone segmentation and the Agent S planner, but those are provider-configurable (Gemini is the default segmenter to conserve budget; the Agent S provider is set per-config). The deployability thesis — agents in health, public services, finance — rests on this oversight. |
| **Arize Phoenix** | On by default | Real OpenTelemetry: spans on every run, plan, action, and workflow node (`routine.run → agent_s.plan → action.N`) with OpenInference I/O on LLM/TOOL spans. Pure observability, off the click path; degrades to no-op spans if Phoenix is down. `./scripts/serve_phoenix.sh` → http://localhost:6006 |
| **Redis** | On if running | An accelerator, not a dependency. Vector search for intent routing (Redis 8 vectorset, `VADD`/`VSIM`), agent replay memory, and a semantic LLM cache for milestone segmentation — all off the click path. Routing falls back to keyword matching and the cache to heuristics when Redis is absent, so the system runs fine without it. |
| **Deepgram** | Key-gated | Real `deepgram-sdk`, three live voice paths: speak the intent, narrate per-step instructions while recording a demonstration, and say "stop" to halt mid-run. Falls back to typed input when `DEEPGRAM_API_KEY` is unset. |
| **Browserbase** | Key-gated | The agent's hands on the open web. With a key it opens a real cloud browser (CDP + Playwright), reads a live page, and fills from what it found. Degrades to a deterministic local value when offline or unconfigured. |
| **Orkes / Agentspan** | Server-gated | The research digression is a real Agentspan agent: `shepherd-researcher` compiles into a durable workflow on a self-hosted Agentspan server, reasons, and calls a `fetch_page` tool (Browserbase under the hood). Runs only for routines that request a research step; reuses the Anthropic key. Degrades to a direct page read when the server is unreachable. (Orkes Conductor workflow-wrapping is scaffolded but not yet wired.) |
| **ArmorIQ** | Key-gated | Intent authorization in front of the click path. At the run boundary Shepherd captures the resolved plan and ArmorIQ issues a cryptographically-signed intent token gated by an allow/deny policy derived from `data/policy.yaml`; a denial halts the run **before the first action**. The SDK is installed and the boundary gate is wired; it no-ops cleanly when `ARMORIQ_API_KEY` is unset. |
| **Sentry** | DSN-gated | Error monitoring with real Phoenix cross-links: failed runs and exceptions attach a clickable `phoenix.trace_url` to the event context. Post-mortem only, never on the click path. Set `SENTRY_DSN` in `.env`. |
| **Band (band.ai)** | Off by default | Designed as a two-agent oversight handoff: on an uncertain high-stakes flag, the engine peer posts the action into a Band room and an independent `shepherd-verifier` peer (Claude) replies with a verdict the human gate consumes — Band's Drafter/Reviewer pattern on Shepherd's Monitor/Verifier. Boundary-only. The Band SDK is an optional extra; with it uninstalled or unconfigured (the default today), the same verdict comes from the in-process Claude verifier instead. |
| **Cognition / Devin** | Build-time | A coding agent used during development, not a runtime dependency — no Devin code runs in production. `devin-ai-integration[bot]` authored or co-authored several merged feature branches (WebRTC P2P remote, the live execution-trace graph, fleet session summaries), reviewed via Devin Review and resolved in-branch before merge. |

Every runtime integration is feature-flagged and degrades gracefully. With all
flags off, the core automation and Control Hub run fully offline — the only thing
you can't remove is Agent S (the engine) and a planner/verifier model.

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

Two un-bundled knobs decide how an intent is handled (`USE_ROUTER` / `ROUTINE_REPLAY`):

- **`USE_ROUTER=false`** (default) — skip routing; every intent runs as a free-form
  **autonomous** Agent S goal (an LLM drafts a plan from the raw words, then Agent S
  executes it screenshot-by-screenshot). Prior memory is **not** consulted unless
  `AUTONOMOUS_USE_MEMORY=true` — off by default, so each run plans fresh.
- **`USE_ROUTER=true`** — match a saved workflow/routine first, falling back to
  autonomous on no match. `ROUTINE_REPLAY` picks how a matched routine is driven:
  `vision` (Agent S plans against the demonstration, was *LIVE*) or `deterministic`
  (verbatim coordinate replay, the offline floor, was *LOCKED*).

These derive the legacy `LIVE`/`LOCKED`/`AUTONOMOUS` enum internally. Switch at
runtime with `POST /api/mode/<MODE>` or the Control Hub sidebar (no restart) — a
runtime override wins for the live process until changed.

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
./scripts/serve_phoenix.sh   # developer traces at http://localhost:6006
agentspan server start       # durable research agent (open-source, keyless)
# Optional: SENTRY_DSN in .env → errors cross-linked to Phoenix traces
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

## Observability (Phoenix + Sentry)

Both are optional and off the click path.

```bash
# Phoenix: live OTel traces (no API key for local)
./scripts/serve_phoenix.sh          # Terminal 1 → http://localhost:6006
uv run python main.py               # Terminal 2

# Sentry: add to .env, then failed runs link back to Phoenix
SENTRY_DSN=https://xxx@oXXX.ingest.sentry.io/XXX
```

Sentry events include **Contexts → phoenix → trace_url** (clickable) and tag
`phoenix.trace_id`. The project slug in trace URLs is resolved automatically via
Phoenix GraphQL (`getProjectByName`).

Implementation: `telemetry/telemetry.py`, `telemetry/agent_trace.py`,
`telemetry/phoenix_client.py`, `telemetry/sentry_init.py`.

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
telemetry/         Phoenix OTel spans, Sentry capture, hash-chain audit, Redis replay
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
