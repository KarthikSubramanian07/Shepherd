# Shepherd — Sponsor Pitch Scripts

Four curated ~5-minute pitch scripts (≈700 spoken words each, demo included).
Swap `[your name]` / `[teammate]` for real names — memorable intros matter most.

Tracks:
1. [Arize AI](#track-1--arize-ai)
2. [Sentry](#track-2--sentry)
3. [Simular (Agent S)](#track-3--simular-agent-s)
4. [Anthropic](#track-4--anthropic)

---

## Track 1 — Arize AI

### 1. Intro + Problem
"Hi, we're **[your name]** and **[teammate]**, and we build things we're afraid to let run
unsupervised — so we built the safety net first. We're **Team Shepherd**.

Here's the problem: everyone's racing to ship AI *agents* that click around your computer and
do real work. But a computer-use agent is the scariest thing in AI right now — it can misclick,
delete a file, send the wrong email, and you'd never know *why*. The industry keeps saying
'you can't ship what you can't observe.' We took that literally.

Shepherd is an autonomous desktop agent that **learns workflows by watching itself** — and the
thing watching is **Arize Phoenix**. Phoenix isn't a dashboard we bolted on at the end. It's the
feedback loop the agent uses to decide what's actually safe to automate. That's the difference
between a demo and something a real person would trust with their machine."

### 2. Features & why they matter
"Three things, mapping to Arize's three pillars.

**Tracing.** Every run is a single `routine.run` trace — plan, then execute, then a span per
physical action — all using **OpenInference semantic conventions**, the standard Arize built
Phoenix around. We adopted your schema, not a custom hack. And computer-use agents are normally
opaque pixels and `pyautogui` code; we enriched every action span with **semantic intent and
grounding reasoning**, so a trace reads like a story instead of coordinates.

**Evals — our strongest piece.** We run a live **LLM-as-judge** on every action, plus a novel
**plan-adherence eval** that compares what the agent *actually did* against the plan it *promised*.
We also wrote an **official Phoenix batch eval job** that exports spans, runs classifiers, and
writes scores back into the Evals tab. We even ran an **offline experiment** showing a v1→v2
rubric precision lift — that's eval-driven development, exactly what Arize preaches.

**The kicker:** evals **gate the agent's learning.** Shepherd promotes good runs into reusable
workflows — and we block that promotion when eval scores are low, so a bad run never gets baked
in. Evals in the control loop, not just on a screen."

### 3. Demo script
"Watch. I type a goal: *'take my selfie in Photo Booth.'* The planner drafts six steps — you see
them stream in. Now Agent S executes, and as it goes, switch to Phoenix.

Here's the **single unified trace** — plan and execute under one roof. Click this action span:
see the intent, the grounding model's reasoning, the exact click coordinate. Now the **Evals tab**
— every step has an oversight score, and the run has a plan-adherence score of 0.9.

Now the bad case: I run a sloppy goal that misclicks. Plan-adherence scores 0.2 — and watch,
Shepherd **refuses to promote it** into a workflow. Finally, this Phoenix trace ID is deep-linked
into Sentry, so a production error jumps straight to the trace. That's observability that closes
the loop. Thank you."

---

## Track 2 — Sentry

### 1. Intro + Problem
"Hi, we're **[your name]** and **[teammate]** — **Team Shepherd**. Our motto: agents will break,
so build for the break.

The problem: AI agents that control your computer fail in ways traditional monitoring can't
explain. A stack trace tells you *what* threw — but for an agent, the real question is *what was
it trying to do, on which step, and why did it go off the rails?* A naked exception is useless.
Engineers waste hours reconstructing context that the agent had and threw away.

Shepherd treats reliability as a first-class feature using **Sentry** — not just catching crashes,
but capturing the *full intent and execution context* of every failure, and linking it to the
agent's reasoning trace. That's the difference between 'something broke' and 'here's exactly what
the agent was doing.'"

### 2. Features & why they matter
"We use Sentry in three deliberate ways.

**Context-rich capture.** We wrote a `capture` helper that attaches structured tags and context —
the goal, the current step number, the action, the target — to every event. So a Sentry issue
tells you the agent failed on *step 4, clicking the capture button*, not just `KeyError`.

**Runs as transactions, halts as issues.** A whole agent run is a Sentry transaction; a
human-intervention halt or a failed run is surfaced as an issue — even when nothing *raised*.
We added `capture_message` for runs that end in `status=failed` silently, so soft failures don't
disappear.

**Cross-linking to the reasoning trace.** This is the part we're proud of: every Sentry event
carries the **Phoenix trace ID**, so one click takes you from the error to the agent's full
step-by-step reasoning. We also tag environment and release, so you can tell a regression from a
one-off.

Decision-wise: we kept PII off and sampling controlled via env, because an agent that screenshots
your desktop needs responsible defaults."

### 3. Demo script
"Let me trigger a real failure. I run a goal, and I've revoked macOS Accessibility permission so
the keystroke injection fails mid-run.

Switch to Sentry. Here's the issue — but look at the **context**: goal is *'send an email,'* it
failed on *step 3, type action*, with the target field attached. The tags show environment and
release. Now this field — **phoenix_trace_id** — I click it, and it takes me straight to the full
agent trace in Phoenix where I can replay every decision.

And here — a run that 'completed' but actually stalled shows up as a `capture_message` event, so
silent failures aren't invisible. That's Sentry doing for agents what it does for apps: turning a
mystery into a fix. Thank you."

---

## Track 3 — Simular (Agent S)

### 1. Intro + Problem
"Hi, we're **[your name]** and **[teammate]** — **Team Shepherd**. We're the people who think the
future of computing is an agent that uses your *actual* apps, not another API.

The problem: most 'automation' is brittle. RPA scripts break the moment a button moves; API
integrations only work for apps that *have* APIs. Real people live in Photo Booth, Chrome, Mail,
native macOS apps. To help them, an agent has to *see the screen and act like a human.* That's
what **Simular's Agent S** does — and it's the muscle of our entire system.

Shepherd wraps Agent S in a planner and a memory layer so it doesn't just do a task once — it
**learns the task and gets reliable over time.** The impact: anyone, regardless of technical
skill, can hand their computer a goal in plain English and have it done."

### 2. Features & why they matter
"Agent S is central, and we built carefully around its grounding.

**Plan-then-ground architecture.** Our planner (Claude Haiku) drafts high-level steps; **Agent S**
handles the hard part — looking at a screenshot and grounding a natural-language intent like
*'click the capture button'* into a real pixel coordinate. We keep these models separate so each
does what it's best at.

**We respected the grounding model's constraints.** This was our key technical decision: Agent S's
vision grounding is sensitive to image scaling. We tuned a megapixel budget so screenshots stay
within the no-downscale window — otherwise coordinates land in the wrong space and the agent
misclicks. Getting logical-vs-physical pixel mapping right is *the* unlock for reliability on
Retina displays.

**Memory and replay.** Successful Agent S runs become reusable workflows, so the second time you
ask, it replays a proven sequence instead of re-reasoning from scratch — faster, cheaper,
deterministic.

**Observability on top.** Every Agent S step emits its grounding reasoning and coordinate into a
trace, so we can actually *see* why it clicked where it did."

### 3. Demo script
"I'll give it a goal that has no API — pure GUI: *'take my selfie in Photo Booth.'*

The planner drafts the steps. Now watch Agent S work. Step 2 — it needs to select single-photo
mode. Look at the terminal: the **raw grounding response** — *'the leftmost icon in the mode
selector is at (413, 637)'* — and it clicks exactly there. Step 4, the capture button: it reasons
*'the large red circular button at bottom center,'* grounds to (665, 637), clicks — and the
photo's taken.

No selectors, no API, no hardcoded coordinates — it *saw* the screen and acted. And now this run
is saved as a workflow, so next time it replays instantly. That's Agent S turning 'describe what
you want' into real actions on real apps. Thank you, Simular."

---

## Track 4 — Anthropic

### 1. Intro + Problem
"Hi, we're **[your name]** and **[teammate]** — **Team Shepherd**. We build agents we'd actually
trust, and trust starts with the model doing the *reasoning.*

The problem: autonomous agents are only as good as their judgment. A weak model gives you confident
nonsense — it'll plan a vague task, misread a screen, and never flag its own mistakes. For an agent
that *controls your computer*, bad judgment isn't a typo, it's a deleted file or a wrong email. The
bottleneck isn't actions; it's **reasoning, planning, and self-critique.**

Shepherd runs on **Claude** end to end — Claude plans the task, Claude grounds the vision, and
Claude judges its own work. We use Claude not as a chatbot, but as the reasoning engine *and* the
safety reviewer of an autonomous system."

### 2. Features & why they matter
"We use Claude in three distinct roles, by design.

**Claude as planner.** Claude Haiku takes a plain-English goal and emits a structured JSON routine
— steps plus a **variables map** it extracts itself. This replaced brittle regex: instead of us
hardcoding verbs like 'play' or 'run,' Claude reads *'run despacito on youtube'* and correctly
pulls `SEARCH_QUERY: despacito`. We trust the model to interpret intent, which made the whole
planner simpler and more general.

**Claude Sonnet for vision grounding.** Claude looks at a screenshot and turns *'click the capture
button'* into a precise coordinate with explainable reasoning — the multimodal capability is what
makes computer-use possible at all.

**Claude as judge.** This is the safety layer: a separate Claude call scores every action and
judges plan-adherence — did the agent actually do what it planned? We ran an experiment proving a
better rubric raised judge precision. Claude grading Claude, with measurable quality."

### 3. Demo script
"I'll show all three Claude roles in one run. I type: *'run despacito on youtube'* — note 'run,'
an unusual verb.

**Planner:** Claude Haiku drafts the steps and — here in the logs — extracts `SEARCH_QUERY:
despacito` on its own. No regex, it just understood.

**Grounding:** as it executes, Claude Sonnet reads the YouTube results screen and reasons about
which element to click, grounding to a real coordinate.

**Judge:** the run finishes, and a separate Claude call scores it — here's the plan-adherence
verdict with a written justification of *why* it passed or failed. Three roles — planner, eyes,
and conscience — all Claude. That's how we turn a language model into a trustworthy autonomous
agent. Thank you, Anthropic."
