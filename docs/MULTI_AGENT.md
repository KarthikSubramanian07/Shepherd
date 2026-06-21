# Plan: Multi-Agent Orchestration

Run multiple agents performing tasks on the desktop through multiple windows —
primarily Browserbase cloud sessions, plus local Agent S — with an action queue
between agents to prevent data races and focus conflicts.

## Core idea & the one hard constraint

The whole design turns on **what is a shared resource vs. what is isolated**:

- **The LOCAL desktop is a single shared surface.** `pyautogui` drives one
  global cursor/keyboard. Multiple Agent S agents can *plan* (LLM calls,
  screenshots) concurrently, but **only one may actuate at a time** — and its
  screenshot + focus + actuate must be one atomic critical section, or agent B's
  window steals focus mid-batch and agent A types into the wrong place. This is
  the data race.
- **Each Browserbase session is its own isolated surface.** Cloud browser, own
  page, no contact with the local desktop. These run **truly in parallel**.

So the "action queue" is a **per-surface lease arbiter**: `LOCAL_DESKTOP` is one
serialization domain (one lease at a time, FIFO); every `BROWSERBASE:<session_id>`
is its own domain (parallel across sessions). That single abstraction gives
exactly what's wanted — serialized desktop, free-running browser windows.

## Target architecture

```
                       Orchestrator  (worker pool, task queue, global halt)
                       /      |        \
              AgentWorker  AgentWorker  AgentWorker      <- each its own thread + engine ctx + halt flag
                  |            |            |
   surface=LOCAL_DESKTOP   surface=LOCAL  surface=BROWSERBASE:s_abc
                  +-----+-----+            |
                        v                  v
               ActionArbiter  -- per-surface FIFO lease queue (THE action queue)
                        |                  |
               LOCAL lease (1 holder)   session lease (1 per session, N parallel)
                        v                  v
        AgentSAdapter->pyautogui   BrowserbaseDriver->Playwright/CDP
                        |                  |
        -- shared, serialized off-click-path side channels --
        audit_log (lock + agent_id) . policy/monitor/verifier (per agent) .
        telemetry (per-agent trace) . event_bus (tagged by agent_id)
                        v
        Control Hub /fleet  -- N live agent panels + a live Action-Queue panel
```

**Lease semantics (the heart of it):** a worker that wants to act calls
`arbiter.acquire(surface, agent_id, priority)` -> blocks in the surface's FIFO
until granted -> does `focus -> screenshot -> actuate batch` -> `release()`. For
LOCAL this serializes all desktop agents; for a Browserbase session it only
serializes that session. **Halt preempts** via a priority lane so "stop" never
waits behind a queued batch. Every acquire/grant/release emits an `arbiter.*`
event so the Control Hub can render the live queue.

## Key design decisions (locked)

1. **Surface model** — LOCAL_DESKTOP single serialized; Browserbase
   one-surface-per-session, parallel.
2. **Many local Agent S agents allowed**, serialized through the LOCAL lease
   (real parallelism only when batches genuinely don't overlap; honest
   expectation: local is mostly serial, Browserbase carries the fan-out).
3. **Single global audit chain**, appends serialized by a lock (off the click
   path), each entry tagged `agent_id`.
4. **Fleet UI in scope.**
5. Back-compat: gate everything behind `ENABLE_ORCHESTRATOR` so today's
   single-agent serial loop still runs untouched when off.

---

## Work split across agents (parallel streams)

**Stream 0 — Contracts & types (do first, ~1/2 day, blocks the rest only at
interface level).** Land the shared interfaces so the other streams parallelize
against stable signatures: `Surface`, `ActionArbiter` API
(`acquire/release/try_acquire/preempt`), `AgentSpec`/`AgentTask`, `AgentWorker`
lifecycle, the `agent_id`-tagged event schema (extend every `event_bus.emit`
payload with `agent_id`), and config flags. New package skeleton `orchestrator/`.
One small PR everyone branches from.

Then these run concurrently:

| Stream | Owner agent | Scope | Depends on | Key files |
|---|---|---|---|---|
| **A — Arbiter + Orchestrator core** | Agent 1 | Per-surface FIFO lease queue, fairness, halt-preempt lane, `arbiter.*` events; worker pool, task queue (generalize `main.py`'s single intent queue), per-worker + global halt, lifecycle/teardown | 0 | `orchestrator/arbiter.py`, `orchestrator/orchestrator.py`, `orchestrator/worker.py` |
| **B — Concurrency-safe local engine** | Agent 2 | Make `ShepherdExecutionEngine` + `AgentSAdapter` **instance-per-worker** (no shared singletons; audit the module-global `pyautogui.PAUSE`/`_agent_s`); wrap `_exec_agent_code`/`_dispatch` in a LOCAL lease; move `activate_app` focus **inside** the lease right before screenshot+actuate; thread `agent_id` through events/spans | 0, A (arbiter iface) | `engine/engine.py`, `engine/agent_s_adapter.py` |
| **C — Browserbase multi-session driver** | Agent 3 | `BrowserbaseSessionManager` (persistent sessions, pool, quota guard, keep-alive, teardown — current code creates+destroys per action; needs to persist); `BrowserbaseDriver` = the AgentSAdapter role on a Playwright `page` (page screenshot -> Claude vision batch plan -> Playwright actuate -> loop); surface = its own session => parallel | 0, A (arbiter iface) | `services/browserbase_session.py`, `engine/browserbase_driver.py`, refactor `services/browserbase_routine.py` |
| **D — Oversight/audit/telemetry under concurrency** | Agent 4 | `audit_log.append` lock + `agent_id` field (preserve single chain & `/api/audit/verify`); make containment rate-limits per-agent-or-global (config); per-agent monitor/verifier wiring; telemetry per-agent trace root + `agent.id` attribute | 0 | `telemetry/audit_log.py`, `services/policy_engine.py`, `services/monitor_agent.py`, `telemetry/telemetry.py` |
| **E — Control Hub fleet view** | Agent 5 | Event-bus multiplex by `agent_id` + WS subscription filter; `/fleet` page with N live agent panels (reuse `LiveExecutionGraph`; per-agent live screen = local screencast / Browserbase live-view URL); a live **Action-Queue panel** (who holds/awaits each surface); dispatch control (spawn agent + pick surface kind) | 0 (event schema), A (`arbiter.*` events) | `frontend/src/app/fleet/`, `frontend/src/lib/shepherd-ws.tsx`, `dashboard/server.py` (WS) |
| **F — Integration, entry rewire, tests, docs** | Agent 6 (integrator, lands last) | Rewire `main.py` to the Orchestrator behind `ENABLE_ORCHESTRATOR`; config (`MAX_CONCURRENT_AGENTS`, `MAX_BROWSERBASE_SESSIONS`, default surface, rate-limit scope); concurrency tests; docs | A–E | `main.py`, `config.py`, `tests/`, `STRUCTURE.md`, `README.md`, `DESIGN.md` |

**Dependency order:** `0` -> then `{A, B, C, D, E}` in parallel (B/C consume A's
arbiter interface; E consumes A's events + 0's schema) -> `F` integrates.

## Invariants the tests must prove (Stream F)

- **Mutual exclusion:** no two LOCAL_DESKTOP leases ever overlap in time
  (instrument the arbiter; assert on a recorded lease timeline).
- **Parallelism:** two Browserbase sessions actuate concurrently (overlapping
  lease windows on distinct surfaces).
- **No focus race:** focus (`activate_app`) is always inside the same lease as
  the screenshot+actuate that follows it.
- **Audit integrity under concurrency:** N agents appending simultaneously ->
  `/api/audit/verify` still returns `valid: true`, entries carry correct
  `agent_id`, chain order is total.
- **Halt semantics:** `halt(agent_id)` stops one agent and frees its lease
  without touching others; `halt_all` preempts every queue.
- **Back-compat:** with `ENABLE_ORCHESTRATOR=false`, the existing single-agent
  path is byte-for-byte unchanged.

## Suggested milestones

1. **M0** — Stream 0 contracts merged.
2. **M1** — Arbiter + Orchestrator (A) running two *mock* local workers, proven
   mutually exclusive (no real engine yet).
3. **M2** — Real local Agent S under the arbiter (B) + audit/telemetry
   concurrency (D): two real desktop agents, serialized, clean audit.
4. **M3** — Browserbase multi-session driver (C): 3+ parallel cloud agents.
5. **M4** — Fleet UI (E) live over M2/M3.
6. **M5** — Integration, full concurrency test suite, docs (F).

## Open risks / things to watch

- **Local "parallelism" is mostly serial** by physics — be clear in the demo
  that the fan-out story is Browserbase; local multi-agent is correctness/safety
  (no races), not speed.
- **Browserbase quota/cost** with many persistent sessions — the session manager
  needs a hard cap + idle teardown (current code already complete-sessions
  eagerly for this reason).
- **`AgentSAdapter` hidden globals** — confirm nothing besides `pyautogui`
  process-state is shared once it's instance-per-worker (Stream B's first task is
  an audit).
- **Halt latency** — preempt lane must bypass the FIFO or a queued long batch
  delays "stop."
