# Plan: Agent S multi-window grid (desktop analog of the Browserbase driver)

Run multiple Agent S agents at once, each bound to its own desktop window, with
the windows tiled into a non-overlapping grid so a human can watch them all. This
is the desktop counterpart to the Browserbase driver's parallel browser windows.

## Research findings (what already exists)

- **pyobjc is already a dependency** (full framework set incl.
  `pyobjc-framework-accessibility`), so macOS window enumeration + control needs
  no new dependency.
- **Grid tiling already exists for the local browser windows**:
  `services/browserbase_session.py` has `_alloc_slot` / `_tile_geometry` /
  `_free_slot` with `tests/test_browser_tiling.py`. The Playwright path tiles via
  Chromium `--window-position` / `--window-size` launch args. That grid math is
  reusable; Agent S can't use launch args (its windows are arbitrary macOS app
  windows), so it needs OS-level window control instead.
- **Agent S today** drives the full screen via pyautogui with app-level focus
  only (`activate_app` = `open -a`). It has no concept of a target window, no
  cropped capture, and no window management. See how it navigates in the engine
  notes (`agent_s_adapter.py`, `agent_s_grounding.py`, `routine_planner.py`).
- **The action queue already exists**: the `ActionArbiter`
  (`orchestrator/arbiter.py`) serializes the single `LOCAL_DESKTOP` surface, and
  the engine wraps every actuation (`_exec_agent_code` / `_dispatch`) in that
  lease. Grid mode reuses this, holding the lease around focus + actuate.

## The core constraint (and the insight that makes it work)

Agent S shares ONE physical cursor/keyboard, so unlike Browserbase it cannot
actuate two windows simultaneously. But a non-overlapping tiled grid unlocks a
key property:

- **Perception parallelizes.** In a non-overlapping grid, every window is fully
  visible at all times, even when not frontmost. So each agent can screenshot
  just its own cell and run its (slow) LLM planning concurrently. No focus is
  needed to SEE.
- **Only actuation serializes.** Keystrokes go to the frontmost window and clicks
  must land in the right place, so the brief focus-window then actuate-batch step
  runs under the existing `LOCAL_DESKTOP` lease, one agent at a time.

That is the closest desktop analog to Browserbase: the expensive part
(screenshot + plan) overlaps across agents; only the short actuation is
serialized.

**Honest framing:** this is NOT simultaneous actuation. It is parallel thinking
plus round-robin acting, visualized as a live grid of windows. Throughput on the
acting part stays serial; the win is parallel perception/planning and a visible
grid.

## Architecture

```
Orchestrator (grid mode)
  |- computes a K-cell grid, opens K windows, tiles them
  |- K AgentWorkers, each bound to a WindowCell{rect, window_id}
        |
        |- PERCEIVE (no lease): crop screenshot to cell.rect -> plan via LLM   [parallel]
        |- ACTUATE  (LOCAL lease): focus cell.window -> run batch              [serial]
              (coords + cell.origin, clamped to cell) -> release
```

## Components

| Piece | What it does | Build on |
|---|---|---|
| **`engine/window_grid.py`** | Grid math: K -> rows x cols -> per-slot `(x,y,w,h)`, with menu-bar/Dock insets. | Lift/generalize `_tile_geometry` / `_alloc_slot` from `browserbase_session.py` (share one impl). |
| **`engine/macos_windows.py`** | macOS window control via pyobjc: `list_windows()` (Quartz `CGWindowListCopyWindowInfo` -> id, owner pid, title, bounds); `place_window(id, rect)` + `focus_window(id)` + `window_bounds(id)` via Accessibility (`AXUIElementCreateApplication(pid)` -> `kAXPositionAttribute` / `kAXSizeAttribute` / `AXRaise`). AppleScript `osascript` fallback (matches existing `text_input.py` / `engine.py` patterns). | pyobjc (already present), existing `osascript` usage. |
| **`WindowCell`** | `{slot, rect, window_id, pid}` - one tile, one agent. | new dataclass |
| **`AgentSAdapter` (cell-aware)** | Optional `cell`. Capture crops to `cell.rect`; grounding `ScreenGeometry` becomes the cell's geometry; returned coords get offset by `cell.origin` and **clamped to the cell** (a mis-grounded click can never land in a neighbor's window). Per-turn focus targets the specific window (not `activate_app` app-level). | `agent_s_grounding.py` (`capture_observation`, `ground_pointer_code`, `normalize_agent_code`), `agent_s_adapter.py` |
| **Orchestrator grid mode** | `dispatch_grid([goals])`: compute K-cell grid (cap ~4-6 for readability), open+tile a window per goal, spawn a cell-bound local `AgentWorker` each, share the `LOCAL_DESKTOP` lease. | `orchestrator/orchestrator.py`, `worker.py` |
| **Fleet UI** | Show the grid layout; map each cell to an agent card; optional screencast of the whole grid. | existing `/fleet` page |

## The two changes that carry the design

**1. Scoped perception + actuation in the adapter (`agent_s_grounding.py`).**
- `capture_observation(cell=...)`: crop the full screenshot to `cell.rect` in
  physical pixels (x Retina scale), then resize to grounding dims. The model only
  ever sees that window.
- Coordinate translation: grounding returns cell-local logical coords -> add
  `cell.rect.origin` -> absolute screen coords for pyautogui -> **clamp to
  `cell.rect`** before actuating. Retina scale is already handled by
  `ScreenGeometry`; we add a crop + an offset.

**2. Lease granularity for windowed agents.**
Today the lease wraps each `_exec_agent_code`. For grid mode, the lease must wrap
**focus-window + the whole actuation batch** (so focus cannot be stolen
mid-batch), while the **screenshot + LLM plan run OUTSIDE the lease** (parallel).
Concretely: in the autonomous reactive loop, when a `cell` is bound, plan
unguarded, then `with arbiter.hold(LOCAL): focus_window(cell); exec_batch()`. This
is a small refactor of the turn body that the orchestrator already half-enables
(each worker has its own adapter).

## Window identification (the fiddly part)

To bind a freshly-opened window to an agent: open it (e.g. a new Chrome window via
`osascript ... make new window` or `open -na "Google Chrome" --args
--new-window`), then enumerate windows by owner pid and claim the newest
unassigned window number. Track `window_id -> agent`. First cut targets N Chrome
windows specifically (direct Browserbase analog, and gives logged-in real Chrome,
which also fixes the auth-wall problem for things like Gmail). Generic per-app
binding is a follow-up.

## Work breakdown (parallel streams)

- **Stream 1 - `window_grid.py`**: extract the grid math, add insets, unify with
  the browser tiler. (small)
- **Stream 2 - `macos_windows.py`**: Quartz enumerate + AX place/focus/bounds +
  AppleScript fallback; a CLI smoke (`python -m engine.macos_windows --tile 4`).
  (the riskiest; spike first)
- **Stream 3 - cell-aware grounding/adapter**: crop capture, coord offset +
  clamp, per-window focus. (depends on 1+2)
- **Stream 4 - orchestrator grid mode + lease-per-turn**: `dispatch_grid`,
  cell-bound workers, turn-granular lease. (depends on 3)
- **Stream 5 - Fleet UI grid view + endpoint** (`/api/fleet/dispatch_grid`).
  (depends on schema from 4)
- **Stream 6 - tests + docs**: grid non-overlap + insets, coord offset/clamp math
  (pure, no desktop), window-manager smoke (gated on macOS perms), `STRUCTURE.md`.

## Tests (the parts provable without a desktop)

- Grid: K cells never overlap, fit screen minus insets, wrap on overflow (extends
  `test_browser_tiling.py`).
- Coordinate transform: cell-local -> absolute offset is exact; out-of-cell coords
  clamp to the cell boundary (pure functions, deterministic).
- Adapter: with a `cell`, the capture path crops and the emitted code's coords
  fall inside `cell.rect` (fake screenshot + fake grounding).
- Window manager: a smoke test that opens 4 windows and tiles them - gated behind
  an env flag + macOS Accessibility permission (skipped in CI).

## Risks / honest caveats

- **Not true parallel actuation** - throughput on the acting part stays serial;
  the win is parallel perception/planning + a live grid. State this in the UI so
  it is not oversold vs. Browserbase.
- **macOS Accessibility permission** required to move/focus other apps' windows
  (System Settings -> Privacy -> Accessibility), on top of Screen Recording. First
  run will prompt; document it in preflight (`engine/permissions.py`).
- **Window-binding races** - matching a newly opened window to an agent by
  "newest pid window" is heuristic; mitigate by opening windows one at a time and
  confirming the new window id before the next.
- **Apps that resist scripted resize / full-screen / Stage Manager** - restrict v1
  to normal Chrome windows; detect-and-skip resistant windows.
- **Retina coordinate correctness** - the crop+offset+scale chain must be exact;
  cover it with the deterministic transform tests before any live run.
- **`activate_app` in generated code** - in grid mode the agent's batch must not
  app-activate (would front the wrong window); remap/suppress it to the
  bound-window focus.

## Suggested order

Spike **Stream 2** first (it is the unknown - does AX place/focus work reliably on
this machine with current perms?). If that is solid, **1 -> 3 -> 4** is
straightforward, then **5/6**. Target K=4 for the first demo.

## Relationship to the existing action queue

The `ActionArbiter` already guarantees one-mouse-at-a-time on `LOCAL_DESKTOP`
(see `docs/MULTI_AGENT.md`). Grid mode does not add a new surface - all windowed
agents still share the single `LOCAL_DESKTOP` lease, held around focus + actuate
per turn. The grid adds spatial isolation (each agent owns a screen region, so
coordinates never collide), per-turn deterministic focus of the right window, and
visibility - not a second parallel actuation channel.
