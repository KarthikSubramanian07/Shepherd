# Sponsor Integration Audit

A code-verified assessment of every sponsor named in `README.md` — how deeply each
is actually wired into the codebase, and how much the project genuinely depends on
it. Scored against the **code**, not the README's own claims.

Two independent axes, 1–10:

- **Integration** — how real and deeply wired the code is (real SDK? on a live
  path? or a stub?).
- **Matters** — how much the project's value actually depends on it.

## Scoreboard

| Sponsor | Integration | Matters | One-line verdict |
|---|---|---|---|
| **Simular / Agent S** | 10 | 10 | The engine. Everything else is scaffolding around this. |
| **Anthropic / Claude** | 9 | 9 | The brain across verifier, planner, research, Agent S — but not exclusive. |
| **Arize Phoenix** | 9 | 6 | Real OTel everywhere, but it's observability, not function. |
| **Deepgram** | 8 | 6 | Real SDK, three live voice paths; degrades to typing. |
| **Redis** | 8 | 5 | Real VADD/VSIM vectorsets, but optional — keyword fallback masks it. |
| **ArmorIQ** | 7 | 5 | Real SDK + real run-boundary gate, but inert (no key set). |
| **Browserbase** | 7 | 5 | Real cloud-browser SDK, but conditional and currently disabled. |
| **Sentry** | 7 | 4 | Real SDK + genuine Phoenix cross-link; post-mortem only. |
| **Orkes / Agentspan** | 6 | 4 | Real Agentspan agent in one demo routine; the *Orkes* file is dead. |
| **Band (band.ai)** | 4 | 3 | Architecturally real, but SDK uninstalled + blank creds = aspirational. |
| **Cognition / Devin** | n/a | — | Build-time authorship credit, not a runtime integration. |

## Tier 1 — load-bearing

**Simular / Agent S — 10 / 10.** The only thing that actuates. Real `gui-agents`
AgentS3 (`pyproject.toml:28`, core dep), real `predict()` calls in
`engine/agent_s_adapter.py:251`, driving LIVE, AUTONOMOUS, and workflow execution.
"The cursor moving on its own is Agent S" is literally true. Remove it and there is
no product.

**Anthropic / Claude — 9 / 9.** The cognitive layer: verifier
(`services/verifier.py:88`, real `Anthropic().messages.create()`), routine planner
(`engine/routine_planner.py:197`), Agentspan research model
(`anthropic/claude-haiku-4-5`), and the Agent S planner provider. Caveat: the README
says Claude is "**the** milestone segmenter… and Agent S planner" — code shows it's
"**a**" option. Gemini is the *default* segmenter (`engine/llm.py:14`, to conserve
Anthropic budget), and the Agent S provider is config-driven. Also note: the
`anthropic` SDK isn't a declared dependency — most calls go over raw `httpx`.

## Tier 2 — real and wired, but supporting

**Arize Phoenix — 9 / 6.** Excellent integration: real `phoenix.otel.register`,
OpenInference span kinds, spans on every run/plan/action (`telemetry/telemetry.py`,
`engine/engine.py`), Noop-span degradation. But pure observability — turn it off and
the agent still works.

**Deepgram — 8 / 6.** Real `deepgram-sdk` v3, three live paths (speak intent,
narrate while recording, "stop" daemon thread), all wired into `main.py`. Matters
less because every path degrades to keyboard.

**Redis — 8 / 5.** The most technically impressive supporting integration: real
Redis 8 `VADD`/`VSIM` vectorsets for routing and a semantic cache, plus replay
memory. But deliberately off the click path, and every call has a keyword/heuristic
fallback, so it's optional in practice. "Vector search for intent routing" is real
but overstated — keyword routing masks its absence.

## Tier 3 — real code, currently inert

**ArmorIQ — 7 / 5.** Genuinely real: `armoriq-sdk` is a *core* installed dep, makes
real `capture_plan()` / `get_intent_token()` HTTP calls, and gates the **run
boundary** — a denial sets the halt flag before step one (`engine/engine.py:573`).
But `ARMORIQ_API_KEY` is blank, so it's a real gun with no ammo. The only "security
as a core layer" claim that's actually wired to block.

**Browserbase — 7 / 5.** Real `browserbase` SDK + Playwright CDP session
(`services/browserbase_routine.py:34`), wired into `browser` steps and the Agentspan
`fetch_page` tool, with three-tier graceful fallback. Key is empty, so it runs on
local stubs in this repo.

**Sentry — 7 / 4.** Real `sentry_sdk.init`, captures engine failures, and the
Phoenix `trace_url` cross-link is genuinely implemented (`telemetry/sentry_init.py:17`).
Gated cleanly on `SENTRY_DSN`. Solid but post-mortem — never touches execution.

**Orkes / Agentspan — 6 / 4.** Two things wear this badge; only one is real. The
genuine `agentspan` SDK builds a real `shepherd-researcher` agent on a self-hosted
server with a Browserbase-backed `fetch_page` tool (`services/agentspan_research.py`)
— but it fires only for one demo routine's research digression, never in autonomous
mode. Meanwhile `services/orkes_workflow.py` (the file actually named "Orkes") is a
**dead no-op stub** marked "VERIFY Saturday," called from nowhere.

## Tier 4 — aspirational / non-runtime

**Band (band.ai) — 4 / 3.** The code implements a plausible Drafter/Reviewer pattern
over REST (`services/band_collab.py`), but the `band-sdk` is an *uninstalled
optional* extra, all four required creds are blank, and the feature flag is therefore
`False`. It never makes a network call in this repo and silently degrades to the
in-process Claude verifier. A well-structured intention, not a working integration.

**Cognition / Devin — n/a.** Correctly *not* a runtime integration — zero
`devin`/`cognition` imports; the README presents it as an authorship credit. Git
shows `devin-ai-integration[bot]` as author/co-author on real merged branches (WebRTC
P2P, execution-trace, fleet summaries — all present in code), alongside human
authors. The *features* are real; the *degree* of Devin's authorship is unverifiable
from git.

## Bottom line

Three sponsors are genuinely load-bearing: **Agent S** (the engine), **Claude** (the
brain), and **Phoenix** (the eyes). Everything else is real-but-optional supporting
infrastructure that degrades gracefully — exactly what the README's "every
integration is feature-flagged and degrades gracefully" promises, and that part is
true.

Two places the README oversells:

- **Band** — described as "a genuine two-agent collaboration" when it's an
  uninstalled, unconfigured stub.
- **Claude's "the"** — several roles are shared ("a"): Gemini is the default
  segmenter, and Agent S / planner providers are config-driven.

**ArmorIQ** and **Browserbase** are real but dormant for lack of keys.

> Method: each sponsor was audited by reading the actual implementation files,
> checking `pyproject.toml`/lockfile for real dependencies, tracing call sites in
> `engine/`, `services/`, `router/`, and `telemetry/`, and inspecting `.env` /
> feature flags for activation state. Line references point at the verified code.
