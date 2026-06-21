# Product

## Register

product

## Users

Developers and small engineering teams who have built or are evaluating an **AI
desktop agent** and don't yet trust it enough to run unattended in production.
Their context: the agent already works, but it's a black box — when it misclicks
or does something unexpected there's no rewind, no audit trail, no way to prove
what it did. Secondary user: anyone who stepped away while the agent ran and
needs to know exactly what happened. In the hackathon demo, the immediate
audience is judges absorbing the trust thesis in one 5-minute sitting.

The job to be done: **author a task by demonstrating it once, watch the agent run
it live, catch it when it strays, and replay exactly what it did.** On any given
screen the primary task is oversight — see the current state, decide on a flagged
step, or review a past run.

## Product Purpose

The Shepherd is a **local oversight and governance layer for AI desktop agents**.
The agent is the part you can't trust; the Shepherd is the layer that makes it
trustworthy — a configurable monitor, a tamper-evident audit trail, and a human
decision gate between the agent's intent and the machine. Success is a developer
deploying an agent they previously would have babysat, because they can now see
every action, halt it on command, and prove what it did.

## Brand Personality

Watchful, calm, trustworthy — a shepherd keeping a night-and-day watch over a
flock. Three words: **vigilant, grounded, reassuring.** The voice is plain and
direct, never alarmist; it states what happened and what to do. The emotional
goal is *earned calm* — the feeling of handing something risky to a steady pair
of hands. The metaphor is expressive (a guiding lantern, waypoints along a path,
a wool-and-earth warmth) but always credible and enterprise-legible.

## Anti-references

- **Generic SaaS dashboard** — no Linear/Vercel/Stripe-clone purple gradients,
  endless identical cards, or default-Inter-plus-blue.
- **Cream/beige editorial** — no 2026 warm-neutral "parchment" body background
  with a serif display and terracotta. Warmth is carried by accent + type, not a
  beige page.
- **Toy / cartoon pastoral** — no literal cartoon sheep, clip-art crooks, or
  childish farm whimsy. The metaphor stays grown-up.
- **Loud / neon** — no high-saturation neon, heavy glow, or dark-mode-gamer
  energy. This is calm oversight.

## Design Principles

- **The audit trail is the product, the cursor is the hook.** Live automation
  earns attention; what wins trust is being able to see, halt, and prove. Design
  the oversight surfaces (live graph, intervention, replay) as first-class, not
  as chrome around the automation.
- **The lantern lights the danger.** A single warm accent is reserved for the
  moment that needs a human — the flagged step, the active node. Calm everywhere
  else so that one signal reads instantly.
- **Show the watch, don't claim it.** Surface real state (which milestone is
  running, what was recalled from memory, the hash-chain verifying) rather than
  reassuring copy. Trust is demonstrated, not asserted.
- **Legible under pressure.** A judge or an on-call dev reads this in seconds.
  High contrast, plain labels, one obvious next action per screen.
- **Grounded warmth.** The shepherd metaphor lives in palette, one brand glyph,
  and the waypoint motion language — never in decoration that doesn't serve the
  task.

## Accessibility & Inclusion

WCAG 2.1 AA: body text ≥4.5:1, large/UI text ≥3:1, verified on the light ground.
Status is never encoded by color alone — pair every status hue with an icon or
label (the monitor halt, the node states). Full `prefers-reduced-motion`
fallbacks for the live-graph and intervention motion (crossfade / instant).
Keyboard-operable controls with visible focus.
