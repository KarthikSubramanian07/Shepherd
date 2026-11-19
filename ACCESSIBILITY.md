# Accessibility

Shepherd’s web UI targets **WCAG 2.2 Level AA**.

## What’s covered

- Next.js renderer routes under `frontend/` (Command Center, Fleet, Remote, Routines, Task Graph, Workflows, Runs, Interventions, Audit, Policy, plus `?dev=true` Voice Lab and Components).
- Automated checks with axe-core tags `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa` at 1440×900, 390×844 and 320×800.
- Manual keyboard focus, skip link, landmarks, names/roles/states, live status for the watch indicator, contrast tokens, and `prefers-reduced-motion`.

## Known limitations

- Screens that need a live Shepherd backend (agent streams, run detail with real traces) were exercised in their empty/offline states in the browser; Electron-only chrome is reviewed in code.
- Canvas/React Flow graphs expose structure visually; milestone labels remain in the DOM, but a full non-visual graph walkthrough is not shipped yet.
- Remote live-view video is a visual feed; when frames are present the UI labels the agent and offers expand/close with Escape.

## Report a barrier

Open an issue in this repository describing the page, assistive technology, and what you expected to happen.
