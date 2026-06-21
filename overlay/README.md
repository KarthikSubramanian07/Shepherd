# Shepherd Overlay HUD

A translucent, always-on-top, **screen-share-invisible** terminal that floats over
your desktop and streams Shepherd's live agent activity — inspired by the
FaceTimeOS overlay. The audience sees the Mac being driven; they do **not** see
this control panel (`setContentProtection(true)`).

## What it shows / does
- Live feed of the agent: goals, plan, each step, the agent's reasoning (`✦`),
  errors, and the final response — straight off the dashboard WebSocket (`/ws`).
- A goal input (`POST /api/intent`) and a **Stop** button (`POST /api/halt`).
- Reuses the running Shepherd backend; no separate server.

## Run
1. Start Shepherd as usual (its dashboard must be up on `:8765`):
   ```bash
   uv run python main.py            # or ENABLE_ORCHESTRATOR=true uv run python main.py
   ```
2. In another terminal, launch the overlay:
   ```bash
   cd overlay
   npm install
   npm start
   ```

Point it at a different backend with `SHEPHERD_URL=http://host:port npm start`.

## Hotkeys
- **⌘⇧Space** — summon the HUD: bring it to the front on the display under your cursor (the fix for "I can't find the window", even across Spaces)
- **⌘⇧H** — show / hide the overlay
- **⌘⇧C** — toggle click-through (so clicks pass through to the app underneath)

Runs as a macOS accessory app (no dock icon). Use the **menu-bar tray icon** (🐑 Shepherd) to Show/Toggle/Quit. Drag the title bar to move it; `click-through` and `✕` are in the header.

## Troubleshooting visibility
- **Can't see it?** Press **⌘⇧Space** or click the tray icon → Show / Summon.
- **Launch opaque** (rules out transparent-compositing issues): `OVERLAY_OPAQUE=1 npm start`
- **Debug window** (opaque, framed, centered, devtools): `OVERLAY_DEBUG=1 npm start`
- **Show in screen recordings too** (disable content protection): `OVERLAY_NO_PROTECT=1 npm start`

## Why content protection matters for the demo
`win.setContentProtection(true)` (in `main.js`) makes the window invisible to
QuickTime / Zoom / FaceTime / OS screen recording. So you can screen-share or
record the agent driving the machine while keeping the live "what is it thinking"
HUD just for you on stage.
