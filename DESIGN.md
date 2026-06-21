# Design

## Theme

**Daybreak Watch** — a light, pastoral-but-credible oversight console. The mood
is a shepherd's morning watch: a wool off-white ground, earthy browns for
structure, a warm orange "lantern" that lights up exactly where a human is
needed, and a meadow green for what's safe. Light, calm, grounded — warmth comes
from the accents and type, never from a beige page.

## Color

Light theme. Off-white wool ground kept near-neutral (a whisper of warmth, not
parchment); warmth carried by the orange lantern + earthy browns.

| Role | Hex | Use |
|---|---|---|
| `ground` | `#f7f5f1` | App background — wool off-white, near-neutral |
| `surface` | `#fffefb` | Raised cards / panels (slightly lifted off ground) |
| `surface-2` | `#f0ece4` | Inset / secondary fills, rails |
| `edge` | `#e2dcd0` | Hairlines, borders |
| `ink` | `#2a231d` | Primary text — warm charcoal |
| `muted` | `#7c7064` | Secondary text — warm taupe (≥4.5:1 on ground) |
| `accent` (lantern) | `#dd6a1f` | Identity + the attention/flag moment — orange |
| `accent-ink` | `#b4520f` | Orange text on light (contrast-safe) |
| `bark` | `#7a5c44` | Earthy brown — structure, brand glyph, secondary |
| `ok` | `#1f8a5b` | Safe / completed — meadow green |
| `flag` | `#dd6a1f` | Needs attention — same lantern orange (unified) |
| `halt` | `#cf3b34` | Stopped / dangerous — clay red |
| `idle` | `#9a8f81` | Pending / inactive — warm gray |

Accent is the **only** saturated color used for identity, and it is reserved for
the one thing that matters: the milestone that's running and the step that needs
a human. Green/red are functional status; brown is structural warmth.

## Typography

One-family-in-weights for the UI (no two-similar-sans pairing), a monospace for
data/IDs/traces (true to an oversight console).

- **UI / display**: a humanist grotesque system stack — `ui-sans-serif, system-ui,
  "Segoe UI", Roboto, "Helvetica Neue", Arial`. Headings use weight 600–700,
  letter-spacing −0.01em on large sizes (never tighter than −0.04em).
- **Data / mono**: `ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas`
  for run IDs, timings, hashes, similarity scores, step counts.
- **Eyebrows used sparingly** — a deliberate brand cadence, not above every
  section. `text-wrap: balance` on headings; body capped ~70ch.

## Brand glyph

A minimal **shepherd's-crook + waypoint** mark (a hooked stroke ending in a
filled dot), bark-brown with an orange dot — the lantern. Lives in the sidebar
lockup and favicons. No literal sheep.

## Components

- **Cards/panels**: `surface` on `ground`, 1px `edge` border, generous radius
  (`0.75rem`), soft low shadow for lift (no glassmorphism, no side-stripes).
- **Buttons**: primary = solid orange lantern, white text; outline = `edge`
  border on surface; danger = clay red. Visible focus ring (orange, 2px).
- **Status**: always hue + icon/label (never color alone). Running = orange +
  pulse; done = green + check; flagged = orange + alert; halted = red + octagon.
- **Live execution graph**: waypoints along a path — the flock motif. Active
  waypoint carries the lantern glow; recalled-from-memory nodes a small mark.
- **Data**: monospace, tabular-nums, right-aligned in tables.

## Layout

- App shell: left rail nav + main. Rail carries the brand lockup, waypoint-style
  nav, and a live "watch" footer (agent heartbeat + mode).
- Generous, varied spacing (rhythm, not a uniform grid). Flex for 1D, grid for
  2D. Responsive grids `repeat(auto-fit, minmax(280px, 1fr))`.
- Semantic z-scale: dropdown → sticky → modal → toast → tooltip.

## Motion

- Ease-out (quart/expo); no bounce. The active waypoint pulses (the watch); the
  path connector fills as progress advances; the intervention banner crossfades
  in. Stagger lists where it fits the content.
- Full `prefers-reduced-motion` fallbacks (crossfade/instant). Reveals enhance
  already-visible content; never gate visibility on a transition.
