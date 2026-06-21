# Shepherd Workflow Engine — Design

Status: **implemented on `main` — phases 1–5 shipped** (built on the `feat/high-level-routine` foundation)

This document describes how Shepherd moves from *replaying a recorded demonstration*
to *executing a reusable, conditional, self-improving workflow*. It is the shared
reference for the milestone-graph / workflow work so it stays consistent with the
router (PR #1) and observability work.

---

## Purpose — why crystallize & evolve workflows

**The problem.** A recorded demonstration is exact clicks against one specific screen.
But most real tasks are *not* action-by-action repeatable: a job application on
Greenhouse and on Workday share the same intent and milestones yet differ in exact
fields, layout, and order. Replaying fine clicks is brittle — it breaks the moment the
page differs.

**The idea.** Capture what the agent does at the level a human reasons about
(*milestones*) and treat that as the durable, reusable unit:

- **Crystallize** — after each run, segment the low-level trace into a high-level
  milestone graph (`open → fill → research → submit`). It's form-agnostic, so one
  workflow generalizes across sites; Agent S re-grounds the clicks per page.
- **Evolve** — the workflow improves *without re-recording*. When the agent hits
  something it can't resolve (an unknown field, a monitor trigger, an unexpected
  page) it **blocks and asks the human**. If the human flags the resolution
  `save_as_rule`, it's **baked back into the workflow as a conditional procedure**
  ("if you don't have the project info → research the user's GitHub and match it to
  the JD"). Next time, the agent just does it.

**The payoff.** An opinionated, self-improving workflow the router can **dispatch** on
a generic intent ("apply to this job"): form-agnostic, encoding the user's standard
procedures, getting smarter every run, and self-healing when sites change.

**Why crystallization is async.** It is the *slow* part (LLM segmentation, graph
merging, branch reconciliation), so it must never slow the agent — it runs off the hot
path against a durable journal (see §3).

---

## 0. Architectural principles — workflow execution (non-negotiable)

These govern how a *dispatched workflow* runs. They exist because a workflow run
must be **as cheap as a routine run**. The standalone milestone-graph executor
(`engine/workflow_executor.py`, §5) violated them — it ran as a separate layer and
re-grounded **every** milestone with Agent S vision, so a workflow run was much
slower than the same task as a plain routine (a routine fills a whole form with one
no-LLM `batch_fill`; the executor grounded Fill-name / Fill-email / Fill-projects as
three separate vision round-trips). These principles supersede that design.

1. **One execution loop — workflows piggyback on the routine hot path.**
   There is no separate execution engine for workflows. A dispatched workflow runs
   through the **same** synchronous step loop the routine path uses
   (`engine.engine._live_execute` / `_dispatch`), reusing every deterministic fast
   path: `batch_fill` (one JS injection for a whole form, no LLM), deterministic
   `type` with resolved text, `wait`, `hotkey`, `open_app`. Agent S vision grounding
   is used **only** for milestones that genuinely need to ground against the live
   screen — never unconditionally per node. If reusing the routine code directly
   risks tainting it, **clone** the routine step flow and inject the workflow's
   milestone context into it — but it must remain the routine-style loop, not a
   second executor layer that re-grounds and re-plans every node.

2. **Single-message advance — the node carries its own forward context; no reprompt
   to route.** At each high-level milestone the agent is handed, **in the same
   planning call it already makes for that node**, everything it needs to both act
   and decide where to go next:
     - the current node's instruction + resolved inputs,
     - the **next** node's instruction (so it knows what it is heading toward),
     - the outgoing edges and conditional branches as NL clauses
       (`if <when> → do <do> → goto <node>`).
   The agent does the node's action **and piggybacks its chosen next node / branch in
   the one returned message**. The executor applies that choice directly. There is
   **never** a second round-trip — no "finish the node, then go back to the graph and
   re-prompt for where to go next". Routing costs zero extra calls.

3. **Conditions are NL clauses read in-context** (see §6). A branch's `when` is
   evaluated against the live screen as part of the same node planning call — no
   predicate engine, no extra hot-path cost.

4. **Tracing and crystallization are strictly side-channel.** Neither the live
   execution-trace graph (Command Center) nor crystallization / coalescing is ever
   in the execution path. Both **passively consume the emitted event stream**
   (`workflow.*` / `step.*`) and the durable run-trace journal, off the hot path.
   They add **zero** steps, round-trips, or synchronous work to a run: the live
   trace graph is built coordinator-side from events the loop already emits;
   crystallization runs on the async coalescer worker (§3). If observing a run ever
   slows it down, that is a bug against this principle.

**Click path stays sacred** (the original rule) applies unchanged: nothing async,
networked, or ML-based runs inside the actuation loop; the only intentional block is
human intervention.

---

## 1. The three artifacts

These are **distinct layers**, not duplicates. Each is a different granularity /
lifecycle of "how to do a task".

| Artifact | Granularity | Lifecycle | Source | Today |
|---|---|---|---|---|
| **Routine** (`data/routines.json`) | Every click / type / hotkey | Authored once | Human demonstration | Exists |
| **Task Graph** (`data/task_graphs.json`) | High-level milestones | Auto-crystallized per run | Observed traces | Exists |
| **Workflow** (`data/workflows.json`) | Milestones + conditional procedures | Saved, curated, versioned | Promoted graph + human teaching | Exists |

- **Routine** = the cold-start *demonstration*. Form-specific, exact. Seeds everything.
- **Task Graph** = *passively observed* memory — what milestones a task performed,
  merged across runs into a DAG (nodes + edges with `times_seen`).
- **Workflow** = the *opinionated, dispatchable* artifact. Form-**agnostic** milestones,
  some carrying human-taught conditional procedures. **This is what the router dispatches.**

A single Workflow (ex. "apply to this job") serves Greenhouse *and* Workday because
milestones are form-agnostic; Agent S handles the per-site grounding.

---

## 2. Reworked end-to-end flow

```
Intent
 └► Router.resolve ──► Plan{ kind: WORKFLOW | ROUTINE | GENERIC, target, params }
        prefer a SAVED opinionated Workflow; else recorded Routine; else freeform agent
 └► Executor (HOT PATH — synchronous, sacred, no LLM/IO in the click loop):
        for each milestone node:
          1. resolve required inputs from the PROFILE/KB
          2. node has a saved `procedure` / conditional clauses? → hand them to Agent S
          3. inputs missing & no procedure → BLOCK → human intervention
                 └► human resolves with instruction R + a discretionary FLAG
                        one_off       → journal only
                        save_as_rule  → bake a conditional clause into the workflow
          4. Agent S grounds + actuates the milestone live (fine clicks under the hood)
          5. append a cheap TraceEvent to the in-memory RunTrace
 └► run end (boundary): write RunTrace to the durable JOURNAL + enqueue → return immediately
 └► COALESCER (COLD PATH — async worker, allowed to be slow):
        segment trace → milestones → reconcile branches → CREATE or EDIT the graph/workflow
        → persist → emit task.graph.saved
```

Two design rules are preserved and generalized:
- **The click path is sacred** — nothing async / networked / ML runs inside the step
  actuation loop. The only intentional block is human intervention.
- **Crystallization is allowed to be slow** — it runs off the hot path so it never
  slows the app.

---

## 3. Two-tier execution: instrument cheap, coalesce later

### Hot path (engine thread)
- Actuate steps; for each, append a lightweight `TraceEvent` to a per-run buffer (O(1)).
- Branch/detour signals (`cmd+t`, intervention, deviation) are *cheap markers* here —
  **not** resolved into graph edges yet.
- At run end (run already finished): cheap **synchronous journal write**, then enqueue
  the `RunTrace` to the coalescer and return. No LLM here.

### Cold path (coalescer worker)
- A single daemon **worker thread + `queue.Queue`** (ordered, single-flight, isolated
  failures).
- Consumes `RunTrace` → LLM segmentation → merge milestones → reconcile branches →
  insert conditional/taught nodes → persist → emit `task.graph.saved`.
- A failure here **never** affects the run; the durable journal allows retry / re-coalesce.

### Durable trace journal (`data/run_traces/`, gitignored)
The cheap synchronous write that makes everything robust. It enables:
- **Retry/replay** after a worker crash.
- **Re-crystallize** with a better model/prompt without re-running the agent.
- **Batch coalescing** later — coalesce many runs at once for better cross-run branch
  inference (the periodic refinement job; per-run async is the default).

---

## 4. Coalescing: CREATE vs EDIT

The coalescer runs in one of two modes depending on whether a workflow already exists.

- **CREATE** (no workflow yet): trace → milestones + edges. (Implemented:
  `engine/milestones.py` LLM segmenter + heuristic fallback.)
- **EDIT** (agent was tracing an existing workflow): the LLM receives **the base
  workflow + the new trace + each deviation/intervention tagged with the node it
  attaches to**, and emits a **patch** (ops referencing existing node keys) — it does
  **not** rebuild. This keeps node keys/labels stable so the graph doesn't churn.

EDIT-mode patch output (ops reference base node keys, or `NEW:` for additions):

```json
[
  {"op":"add_node","after":"fill::::Fill applicant details","kind":"research",
   "label":"Research GitHub for projects","condition":"you don't have the applicant's project info",
   "procedure":"open GitHub, match pinned repos to the JD, summarize into the field",
   "requires":["github_user"],"source":"taught"},
  {"op":"set_procedure","node":"fill::::Fill projects","procedure":"..."},
  {"op":"add_branch","from":"submit::::Sign in","to":"verify::::Sign-in rejected",
   "condition":"invalid password"},
  {"op":"noop","reason":"deviation was one_off"}
]
```

---

## 5. The teaching loop (block → flag → bake)

```
Agent tracing workflow W hits scenario S (block / unknown field / monitor / deviation)
 → intervention prompt → user resolves with instruction R + a DISCRETIONARY FLAG:
        one_off       → journal only; do NOT touch the workflow
        save_as_rule  → bake a conditional clause into W
 → hot path: record InterventionEvent{node_key, scenario S, resolution R, flag} (cheap)
 → coalescer (EDIT): if save_as_rule → add a conditional clause {when: S, do: R} at node_key
 → next run hits S → agent reads the clause and auto-resolves, no block
```

The human's **flag is the gate**: only `save_as_rule` bakes; `one_off` stays in the
journal. (Scope `this-site-only` vs `everywhere` can be inferred by the coalescer from
the annotation later — not a separate knob for now.)

**Deviations** are the second entry point: while tracing a known workflow, an
*unexpected* milestone (vs. the expected node/edge) is a deviation → prompt → annotate →
EDIT-mode coalesce. This is how a workflow **self-heals** when a new site differs.

---

## 6. Conditions = natural-language clauses the agent reads

Conditions/edges are **not** a separate predicate engine. A node carries its instruction
**plus conditional clauses**; a conditional edge is literally *"if &lt;NL when&gt; → do
&lt;that other node's action&gt;"*.

```json
{
  "key": "fill::::Fill projects",
  "label": "Fill the Projects field",
  "instruction": "Fill the Projects field on the form",
  "requires": ["projects_summary"],
  "conditionals": [
    {"when": "you don't have the applicant's project info",
     "do": "read the user's latest GitHub projects and summarize them to match the JD",
     "goto": "research::github::Research GitHub for projects"}
  ]
}
```

At runtime the engine hands Agent S the node instruction **plus** its `if … → do …`
clauses. The agent evaluates `when` against the live screen **as part of the planning
call it already makes for that node** — so condition evaluation costs **nothing extra on
the hot path** and needs no deterministic matcher. `goto` reuses an existing node's action.

---

## 7. Data model (planned additions)

- **`Profile` / KB** — the field-value source (`name, email, github_user, linkedin,
  resume_url …`) + provenance. **"Unknown field" = a `requires` key not resolvable here**;
  that is what triggers a block.
- **`WorkflowNode`** = milestone + `instruction`, `requires: [str]`, `conditionals:
  [{when, do, goto?}]`, `procedure?`, `optional`, `source: observed|taught`.
- **`WorkflowEdge`** = `from`, `to`, `condition?` (NL guard), `times_seen`.
- **`Workflow`** = `id, name, description, intent_patterns, params, nodes, edges, version, from_graph`.
- **`RunTrace`** carries `intent_text` — the raw NL intent from the user, threaded from
  the dispatch point through `engine.py` to the coalescer so `TaskGraphStore.save()`
  populates `graph.intents` with real language instead of empty strings.

Already implemented this branch: `TaskGraphNode`, `TaskGraphEdge`, `TaskGraph`
(`shepherd_types.py`), persisted by `engine/task_graph.py`.

---

## LLM layer (modular)

Crystallization's LLM calls go through a **provider-agnostic layer** (`engine/llm.py`)
so the segmenter/coalescer don't care which model runs:

- **Providers**: `gemini` (Google Generative Language API — Gemma/Gemini models) and
  `anthropic` (Messages API). Both over `httpx` (no SDK → no lockfile churn).
- **Selected by config** (`LLM_PROVIDER`); each provider has its own key
  (`GEMINI_API_KEY` / `ANTHROPIC_API_KEY`) and model.
- **Dev default**: `gemini` + `gemma-4-26b-a4b-it` — cheap/fast, conserves limited
  Anthropic tokens. Anthropic (`claude-haiku-4-5`) is a drop-in alternative.
- The layer normalizes provider quirks (Anthropic `system` + assistant prefill;
  Gemini `systemInstruction` + `contents` roles, **filtering Gemma's `thought`
  parts**) and returns plain completion text; callers parse JSON defensively via
  `parse_json_array` / `parse_json_object` (string-aware balanced scan, tolerates
  fences/prose).
- **Gotcha**: Gemma-4 always reasons before answering (~90s/call), so `LLM_TIMEOUT_S`
  defaults to 180s — fine on the cold path. Switch `GEMINI_MODEL` to a Flash-Lite
  (ex. `gemini-2.5-flash-lite`, ~5s) for fast dev iteration.

---

## 8. Build phases — all shipped on `main`

1. **Milestone graph (done)** — LLM segmenter + heuristic fallback (`engine/milestones.py`),
   nodes + edges (`engine/task_graph.py`), `/api/task-graph/{id}` + CORS, `/task-graph`
   frontend view.
2. **Async foundation (done)** — `RunTrace` + `InterventionEvent` schema, durable trace
   journal (`engine/trace_journal.py`), async coalescer worker (`engine/coalescer.py`)
   with CREATE/EDIT modes; `segment()` runs off the hot path.
3. **Teaching loop (done)** — intervention `flag` (`one_off | save_as_rule`) + node
   `procedure`/`conditionals`; EDIT-mode patch ops bake taught clauses without rebuilding
   (`engine/workflow_edit.py`); the executor injects saved clauses instead of re-blocking.
4. **Workflow + dispatch (done)** — promote a graph to a named, versioned Workflow
   (`engine/workflow_store.py`, `data/workflows.json`); `Router.resolve_plan` returns a
   `Plan{WORKFLOW | ROUTINE | GENERIC}` preferring a saved Workflow (`router/router.py`),
   indexed into the same vector search. **Auto-promotion trigger**: after a first-time
   ad-hoc autonomous run completes and the coalescer saves the task graph
   (`task.graph.saved` event with `known=false`), the Command Center's default-on
   "Bake out a new workflow" toggle fires a `promote` command via the relay. The agent
   calls the shared `promote_graph()` helper (`engine/workflow_promote.py`) which derives
   name + `intent_patterns` from the graph's real NL intents, creates the workflow
   IMMEDIATELY (instantly dispatchable), and fires an async background task
   (`engine/workflow_describe.py`) that calls the LLM to generate a human-readable title,
   description, and paraphrased intent_patterns — then re-indexes the vector store. If the
   LLM is unavailable, the NL-derived values are kept. Both `relay_client._promote_graph()`
   and `POST /api/task-graphs/{task_key}/promote` delegate to this shared helper.
   Endpoint: `POST /api/task-graphs/{task_key}/promote`.
5. **Milestone-graph executor (done; being superseded — see §0)** — traverses the
   workflow node-by-node with a single-message advance and pluggable workers (AgentS
   / LLM / Scripted) (`engine/workflow_executor.py`); Control Hub steer/teach gate
   (`engine/workflow_control.py`); wired into `engine/engine.py` on a WORKFLOW
   dispatch. **Note:** the standalone `AgentSWorker` re-grounds every milestone with
   vision and bypasses the routine fast paths (`batch_fill` etc.), making workflow
   runs much slower than routine runs. Per §0 this layer is being folded into the
   routine hot path — the milestone instruction + next node + conditionals are
   injected into the regular `_live_execute`/`_dispatch` loop (single-message
   advance, no separate executor). The Control Hub gate and teaching loop stay.

---

## 9. Module map (where each piece lives)

| Concern | Module |
|---|---|
| Intent → Plan/route | `router/router.py`, `router/vector_router.py`, `router/llm_filter.py`, `router/registry.py` |
| Fine-step execution (hot path) | `engine/engine.py` |
| Human intervention gate | `engine/approvals.py` |
| Milestone segmentation (LLM + heuristic) | `engine/milestones.py` |
| Task-graph store (nodes + edges) | `engine/task_graph.py` |
| Run trace schema | `shepherd_types.py` |
| Durable trace journal | `engine/trace_journal.py` |
| Async coalescer worker | `engine/coalescer.py` |
| API (task-graph, control, status) | `dashboard/server.py` |
| Frontend graph view | `frontend/src/app/task-graph/`, `frontend/src/components/graph/TaskGraphView.tsx` |
| Modular LLM layer | `engine/llm.py` |
| Workflow store (promotion + versioning) | `engine/workflow_store.py` → `data/workflows.json` |
| Shared promote helper | `engine/workflow_promote.py` |
| Async workflow description (LLM) | `engine/workflow_describe.py` |
| Teaching loop (EDIT-mode bake) | `engine/workflow_edit.py` |
| Milestone-graph executor (traversal) | `engine/workflow_executor.py` |
| Workflow dispatch + executor wiring | `router/router.py`, `engine/engine.py` |
| Control Hub steer/teach gate | `engine/workflow_control.py` |

---

## 9.1 Routing precision: retrieve → LLM filter

The router's `resolve_plan()` uses a two-stage pipeline to prevent false-positive
routing (e.g. a research intent incorrectly matching `ROUTINE_JOB_APPLICATION` at
cosine 0.79):

```
Intent
 └► Vector search (CANDIDATE GENERATION — recall)
      • top-K (5) from workflows vectorset + top-K from routines vectorset
      • low recall threshold (0.25) — favor recall, not precision
 └► LLM filter (PRECISION — always runs when ≥1 candidate)
      • NO score-based bypass — even a 0.92 cosine can be a false positive
      • tiny prompt to fast model (Haiku via engine/llm.py):
        "Which candidate, if any, satisfies the request? Reply id or NONE."
      • returns chosen id → route; or NONE → GENERIC (autonomous)
 └► Graceful degradation (LLM unavailable / transient error)
      • fall back to conservative top-1 threshold (0.40)
      • offline substring fallback unchanged (Redis down)
```

Key properties:
- **Cold path only** — never on the hot per-step execution loop.
- **LLM is the authoritative precision gate** — always called when candidates
  exist; one short Haiku call (~64 max tokens, 15s timeout) per routing decision.
- **Deterministic tests**: `tests/test_llm_filter.py` mocks the LLM to validate
  both false-positive rejection and false-negative acceptance.

---

## Decisions settled (log)

- **Workflow execution piggybacks on the routine hot path** (§0): one loop, not a
  separate executor; reuse the routine fast paths (`batch_fill`, deterministic
  type/wait/hotkey/open_app) and ground with vision only when a milestone needs it.
  Clone the routine flow rather than re-grounding every node.
- **Single-message advance** (§0): a node is handed its instruction + the next
  node + conditional branches in the one planning call it already makes, and returns
  its action *and* its chosen next node together — never a second round-trip to route.
- **Tracing & crystallization are side-channel** (§0): both consume the emitted
  event stream / journal off the hot path; observing a run must never slow it.
- **Three artifacts**, not duplicates: Routine (demo) → Task Graph (observed) →
  Workflow (saved, opinionated, dispatchable).
- **Dispatch** on the generic intent: Router → `Plan{Workflow | Routine | Generic}`,
  preferring a saved opinionated Workflow.
- **Click path stays sacred**: no LLM/network in the actuation loop; the only block is
  human intervention.
- **Two-tier execution**: cheap hot-path instrumentation → **per-run async coalescing**
  off a **durable trace journal** (enables retry / re-coalesce / batch later).
- **Coalescing modes**: CREATE (build) vs EDIT (patch an existing workflow with ops
  referencing node keys — never rebuild → stable keys).
- **Teaching loop**: block/deviation → human resolves + discretionary flag;
  `save_as_rule` bakes, `one_off` journals only. Site-scoping inferred later, not a knob.
- **Conditions are NL clauses** the agent reads in-context (`if <when> → do <action>`,
  optional `goto` reuses another node) — no separate predicate engine, zero extra
  hot-path cost.
- **Deviations** during workflow execution are the second teaching entry point
  (self-healing).
- **"Unknown field"** is defined by the Profile/KB: a required key it can't resolve.
- **Build strategy**: design for the milestone-graph executor (B), ship via A→B
  migration (teaching loop + data model under today's executor first).
- **LLM layer is modular**: Gemini/Gemma default for dev (token thrift), Anthropic alt.
- **Per-run async coalescing + journal** chosen over batch for responsiveness; batch
  re-coalescing remains available from the journal.
