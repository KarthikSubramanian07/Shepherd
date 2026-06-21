# Shepherd Coordinator Wire Protocol

**Protocol Version: `1`**

The Shepherd Coordinator is a **generic, app-agnostic relay** that brokers real-time events, screen frames, and commands between *operated machines* (agents) and *operator UIs* (Command Centers). It holds no automation logic and actuates nothing itself — it is pure plumbing.

Any client that implements the handshake and message schemas below can participate, regardless of what software the operated machine runs or what UI the operator uses.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Connection Endpoints](#connection-endpoints)
3. [Authentication](#authentication)
4. [Session Scoping (Pairing Code)](#session-scoping-pairing-code)
5. [Agent Endpoint (`/agent`)](#agent-endpoint-agent)
6. [UI Endpoint (`/ui`)](#ui-endpoint-ui)
7. [HTTP API](#http-api)
8. [Message Schemas](#message-schemas)
9. [Bandwidth & Tuning](#bandwidth--tuning)
10. [Protocol Version Negotiation](#protocol-version-negotiation)

---

## Architecture

```
┌────────────────────┐         ┌──────────────────────┐         ┌──────────────────────┐
│  Operated Machine  │────────▶│     Coordinator      │◀────────│   Operator UI        │
│  (agent)           │  WS /agent  │  (relay only)    │  WS /ui │  (Command Center)    │
│                    │◀────────│                      │────────▶│                      │
└────────────────────┘ commands└──────────────────────┘  roster │                      │
                                                        events  └──────────────────────┘
                                                        frames
```

- **Star topology**: all agents dial OUT to the coordinator. The coordinator is the only component that needs a public/reachable URL.
- **Stateless relay**: the coordinator maintains in-memory session state only (agent roster, last frame, event ring buffer). A restart clears state; agents reconnect automatically.

### Coordinator as Sidecar — NOT the Dispatch Layer

The coordinator is a **video/event relay only**. It does NOT dispatch intents or
execute workflows. The Shepherd agent's existing backend (`main.py`) is the
canonical dispatch layer:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Operated Machine                                                        │
│                                                                          │
│  ┌────────────┐   remote_intents    ┌─────────────────────────────────┐ │
│  │relay_client│──── queue.put() ───▶│ main.py --listen (main loop)    │ │
│  │ (sidecar)  │                     │  ├─ ShepherdIntentRouter         │ │
│  │            │                     │  ├─ WorkflowStore / Engine       │ │
│  │  • frames  │◀── event_bus ──────│  └─ Agent S (GUI automation)     │ │
│  │  • events  │                     └─────────────────────────────────┘ │
│  └─────┬──────┘                                                          │
│        │ WS /agent                                                       │
└────────┼─────────────────────────────────────────────────────────────────┘
         │
    ┌────▼────────────────┐
    │    Coordinator       │  ← relay only (frames, events, commands)
    │  (public URL via     │
    │   Cloudflare Tunnel) │
    └────┬────────────────┘
         │ WS /ui
    ┌────▼────────────────┐
    │  Command Center UI   │  ← sends intents via coordinator → relay_client
    │  (Next.js frontend)  │     → remote_intents queue → main.py router
    └─────────────────────┘
```

**Intent flow (Command Center → agent execution):**

1. Operator types an intent in the Command Center UI (e.g. "navigate to example.com")
2. UI sends `{"type": "command", "command": "intent", "text": "..."}` over WS to coordinator
3. Coordinator relays it to the agent's WS connection
4. `relay_client.py` receives the command and puts `text` into the `remote_intents` queue
5. `main.py`'s main loop picks it up and routes through `ShepherdIntentRouter`
6. Router resolves to a workflow/routine/autonomous goal → engine executes it
7. Engine drives the browser (Agent S / Playwright) — screen changes are captured
8. `relay_client.py` streams updated frames back through the coordinator to the UI

**Key principle**: `operate.py` is a lightweight demo launcher that does NOT dispatch
intents through the engine. For full E2E with intent execution, always use
`main.py --listen` with `COORDINATOR_URL` set. Both share the same `relay_client`
sidecar for video streaming.

---

## Connection Endpoints

| Role | WebSocket Path | Direction | Purpose |
|------|---------------|-----------|---------|
| Agent (operated machine) | `/agent` | Agent → Coordinator | Stream events + frames UP; receive commands DOWN |
| UI (operator) | `/ui` | UI → Coordinator | Receive roster/events/frames; send commands |

Both endpoints use standard WebSocket (`ws://` or `wss://`).

---

## Authentication

Authentication is via a shared **bearer token** passed as the `token` query parameter on the WebSocket URL.

| Parameter | Required | Description |
|-----------|----------|-------------|
| `token` | Conditional | Must match the coordinator's `COORDINATOR_TOKEN` env var. If the coordinator has no token configured (empty string), auth is disabled and this param is optional. |

**Rejection**: If the token is invalid, the coordinator closes the WebSocket immediately with close code `4401`.

```
ws://coordinator.example.com/agent?token=my-secret-token&...
```

---

## Session Scoping (Pairing Code)

The `code` query parameter scopes connections into logical **sessions** (pairings). Multiple agents can share a code, and multiple UIs can observe the same code.

| Behavior | Agent | UI |
|----------|-------|-----|
| Parameter | `code` (query param on `/agent`) | `code` (query param on `/ui`) |
| Default | Falls back to `agent_id` if empty | Empty = unscoped (sees ALL agents) |
| Semantics | Identifies which session this agent belongs to | Filters which agents this UI can see and command |

A UI scoped with `code=ABC` only receives roster entries, events, and frames from agents whose `code` is also `ABC`. An unscoped UI (no `code` or empty) sees all agents — useful for fleet/dev overview.

A UI can only send commands to agents it can "see" (same code or unscoped).

---

## Agent Endpoint (`/agent`)

### Connection

```
GET /agent?agent_id=<id>&name=<label>&host=<hostname>&code=<session>&token=<secret>
Upgrade: websocket
```

**Query parameters:**

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `agent_id` | No | `agent-<unix_ts>` | Stable identifier for this machine |
| `name` | No | Same as `agent_id` | Human-readable label in the roster |
| `host` | No | Same as `agent_id` | Hostname/description of where the agent runs |
| `code` | No | Same as `agent_id` | Session/pairing code |
| `token` | Conditional | — | Auth token (see [Authentication](#authentication)) |

### Handshake: Agent → Coordinator (`hello`)

Immediately after the WebSocket is established, the agent SHOULD send a `hello` message to confirm/update its identity:

```json
{
  "type": "hello",
  "name": "My Machine",
  "host": "vm-desktop",
  "mode": "LIVE",
  "protocol_version": 1
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `type` | string | Yes | Always `"hello"` |
| `name` | string | No | Updates the agent's display name |
| `host` | string | No | Updates the agent's host label |
| `mode` | string | No | Current execution mode (`LIVE`, `LOCKED`, `AUTONOMOUS`) |
| `protocol_version` | integer | No | Protocol version the client implements (currently `1`). Older clients that omit this field are treated as version `1`. |

The coordinator acknowledges by pushing an updated roster to all connected UIs.

### Upstream Messages: Agent → Coordinator

#### `event` — Forward an execution event

```json
{
  "type": "event",
  "event": {
    "type": "<event_type>",
    "data": { ... }
  }
}
```

The `event` object is opaque to the coordinator — it is forwarded verbatim to scoped UIs. The coordinator derives agent status (idle/running/blocked/completed/failed) from the event type.

**Known event types** (non-exhaustive; the relay forwards any type):

| Event Type | Description |
|-----------|-------------|
| `execution.start` | A routine/task execution begins |
| `step.start` | A step within an execution starts |
| `monitor.alert` | Safety monitor has flagged/blocked a step |
| `monitor.decision` | Safety decision resolved (approve/halt) |
| `execution.complete` | Execution finished |
| `execution.halted` | Execution was halted by operator/policy |
| `mode.changed` | Agent switched execution modes |
| `workflow.start` | A workflow traversal begins |
| `workflow.node.enter` | Entered a workflow milestone node |
| `workflow.step` | Completed a workflow milestone |
| `workflow.awaiting` | Workflow paused, awaiting operator input |
| `workflow.intervention` | Operator intervention was applied |
| `workflow.baked` | A steer was crystallized into the workflow |
| `workflow.done` | Workflow traversal completed |

#### `frame` — Push a screen frame

```json
{
  "type": "frame",
  "data": "<base64-encoded-JPEG>"
}
```

The `data` field is a base64-encoded JPEG image of the agent's screen, downscaled per the agent's `RELAY_FRAME_WIDTH` and `RELAY_FRAME_QUALITY` settings. Frames are pushed at `RELAY_FPS` (default 3 fps).

The coordinator stores only the latest frame per agent. It forwards frames only to UIs that are actively **watching** this agent (see `/ui` `watch` message).

### Downstream Messages: Coordinator → Agent

#### `command` — Operator command

```json
{
  "type": "command",
  "command": "<command_name>",
  "payload": { ... }
}
```

**Command types:**

| Command | Payload | Description |
|---------|---------|-------------|
| `intent` | `{ "text": "..." }` | Submit a natural-language intent for the agent to execute |
| `approve` | `{}` | Approve a pending safety gate (resume blocked execution) |
| `halt` | `{}` | Halt execution (resolves pending gate + arms halt flag) |
| `override` | `{ "instruction": "..." }` | Override a safety gate with a custom instruction |
| `mode` | `{ "mode": "LIVE" \| "LOCKED" }` | Switch the agent's execution mode |
| `workflow.pause` | `{}` | Pause the current workflow traversal |
| `workflow.resume` | `{}` | Resume a paused workflow |
| `workflow.intervene` | See below | Steer the workflow at a milestone |

**`workflow.intervene` payload:**

```json
{
  "instruction": "Research the projects page first",
  "next_key": "navigate::projects::Research projects page",
  "scenario": "projects_unknown",
  "remember": true,
  "decision": "override",
  "target_node": "fill::projects::Fill projects field"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `instruction` | string | No | Free-text instruction for the agent |
| `next_key` | string | No | Node key to branch to |
| `scenario` | string | No | Named scenario for conditional crystallization |
| `remember` | boolean | No | Whether to bake this steer into the workflow |
| `decision` | string | No | Decision type (default `"override"`) |
| `target_node` | string | No | Which node this intervention targets |

---

## UI Endpoint (`/ui`)

### Connection

```
GET /ui?code=<session>&token=<secret>
Upgrade: websocket
```

**Query parameters:**

| Param | Required | Default | Description |
|-------|----------|---------|-------------|
| `code` | No | Empty (unscoped) | Session/pairing code to filter agents |
| `token` | Conditional | — | Auth token (see [Authentication](#authentication)) |

### On Connection

Immediately upon connection, the coordinator pushes the current agent roster (filtered by `code`):

```json
{
  "type": "agents",
  "agents": [ <AgentSnapshot>, ... ]
}
```

### Downstream Messages: Coordinator → UI

#### `agents` — Roster update

Pushed whenever an agent joins, disconnects, or changes status.

```json
{
  "type": "agents",
  "agents": [
    {
      "id": "operated-box",
      "name": "Operated Machine",
      "host": "vm-desktop",
      "code": "DEMO",
      "online": true,
      "status": "running",
      "mode": "LIVE",
      "routineId": "WF_LIVE_JOB_APPLICATION",
      "runId": null,
      "currentStepIndex": 2,
      "totalSteps": 5,
      "progress": 0.6,
      "block": null,
      "lastActivityAt": "2025-01-15T10:30:00Z",
      "hasFrame": true,
      "workflow": { ... }
    }
  ]
}
```

#### `event` — Agent event (broadcast to scoped UIs)

```json
{
  "type": "event",
  "agent_id": "operated-box",
  "event": {
    "type": "workflow.node.enter",
    "data": { ... }
  }
}
```

#### `frame` — Screen frame (only to UIs watching this agent)

```json
{
  "type": "frame",
  "agent_id": "operated-box",
  "data": "<base64-JPEG>",
  "ts": 1705312200.123
}
```

### Upstream Messages: UI → Coordinator

#### `watch` — Subscribe to an agent's frame stream + event replay

```json
{
  "type": "watch",
  "agent_id": "operated-box"
}
```

When a UI sends `watch`, the coordinator replays that agent's recent event history (up to 200 events) and the last frame, so the UI is immediately populated.

#### `unwatch` — Stop watching

```json
{
  "type": "unwatch"
}
```

#### `command` — Send a command to an agent

```json
{
  "type": "command",
  "agent_id": "operated-box",
  "command": "intent",
  "payload": { "text": "Fill out the form" }
}
```

The coordinator validates that the UI can "see" the target agent (same session code or unscoped), then forwards the command to the agent's WebSocket.

---

## HTTP API

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Human-readable status page |
| `GET` | `/api/health` | Health check: `{"ok": true, "agents": <count>}` |
| `GET` | `/api/agents?code=<optional>` | List all agents (optionally filtered by code) |
| `GET` | `/api/agents/{agent_id}/screen` | Last frame for a specific agent: `{"data": "<b64>", "ts": <float>}` |

---

## Message Schemas

### Agent Snapshot (in roster)

```json
{
  "id":               "string — agent_id",
  "name":             "string — display name",
  "host":             "string — hostname",
  "code":             "string — session/pairing code",
  "online":           "boolean",
  "status":           "idle | running | blocked | completed | failed",
  "mode":             "LIVE | LOCKED | AUTONOMOUS",
  "routineId":        "string | null",
  "runId":            "string | null",
  "currentStepIndex": "integer | null",
  "totalSteps":       "integer | null",
  "progress":         "float (0.0–1.0)",
  "block":            "object | null (see RemoteBlock)",
  "lastActivityAt":   "ISO 8601 timestamp",
  "hasFrame":         "boolean",
  "workflow":         "object | null (see WorkflowView)"
}
```

### Block Object

```json
{
  "stepIndex": 2,
  "verdict": "HALT",
  "trigger": "planted_trigger",
  "reason": "Credential keyword detected",
  "suggestions": [{"label": "Skip", "action": "approve"}],
  "workflow": false,
  "nodeKey": null,
  "label": null,
  "options": []
}
```

### Workflow View (in roster)

```json
{
  "id": "WF_LIVE_JOB_APPLICATION",
  "name": "Acme job application (live)",
  "current": "fill::email::Fill email",
  "awaiting": false,
  "nodes": [
    {
      "key": "fill::name::Fill full name",
      "label": "Fill full name",
      "kind": "fill",
      "status": "done",
      "instruction": "Type the applicant's full name.",
      "did": "Typed 'Alex Johnson'",
      "branch": null,
      "next": "fill::email::Fill email"
    }
  ],
  "edges": [
    { "from": "fill::name::Fill full name", "to": "fill::email::Fill email", "when": null }
  ]
}
```

---

## Bandwidth & Tuning

The live screen stream is the primary bandwidth consumer. At default settings:

| Setting | Default | Effect |
|---------|---------|--------|
| `RELAY_FPS` | 3.0 | Frames per second pushed by the agent |
| `RELAY_FRAME_WIDTH` | 1024 px | Downscale target width |
| `RELAY_FRAME_QUALITY` | 55 | JPEG quality (1–95) |

**Estimated bandwidth at defaults**: ~1–2 Mbps per agent (depending on screen complexity).

**To reduce bandwidth**:

| Change | Approx. savings |
|--------|----------------|
| `RELAY_FPS=1` | ~66% reduction |
| `RELAY_FRAME_WIDTH=640` | ~60% reduction |
| `RELAY_FRAME_QUALITY=30` | ~40% reduction |
| All three combined | ~90% reduction (~100–200 Kbps) |

These are configured as environment variables on the **agent** side. The coordinator is unaware of frame encoding — it just relays the binary payload.

---

## Protocol Version Negotiation

The current protocol version is **`1`**.

### How It Works

1. The agent includes `"protocol_version": 1` in its `hello` message.
2. The coordinator echoes `"protocol_version": 1` in the roster snapshot's top-level metadata (future enhancement) and logs a version mismatch warning if the client's version is newer than what it supports.
3. Older clients that omit `protocol_version` are treated as version `1` (backward-compatible).

### Version Guarantees

- **Additive changes** (new optional message fields, new event types, new optional query params) do NOT bump the protocol version.
- **Breaking changes** (removed fields, changed semantics, required new fields) WILL bump the version.
- The coordinator will support at least one prior version for graceful migration.

---

## WebRTC Signaling (Optional P2P Streaming)

The coordinator supports relaying WebRTC signaling messages to enable **direct peer-to-peer video streaming** between agents and UIs. This is optional — the default base64 frame relay works without it. WebRTC is useful when you want lower latency or when the coordinator is on a bandwidth-constrained host.

### Message Types

| Type | Direction | Payload |
|------|-----------|---------|
| `webrtc.offer` | Agent → Coordinator → UI | SDP offer (`RTCSessionDescriptionInit`) |
| `webrtc.answer` | UI → Coordinator → Agent | SDP answer (`RTCSessionDescriptionInit`) |
| `webrtc.ice` | Both directions | `{ "candidate": RTCIceCandidateInit }` |

### Flow

1. Agent creates an `RTCPeerConnection`, adds a screen-capture video track, and generates an SDP offer.
2. Agent sends `{"type": "webrtc.offer", "data": <SDP offer>}` to the coordinator.
3. Coordinator relays it to the watching UI(s) for that agent.
4. UI creates its own `RTCPeerConnection`, sets the remote description, creates an answer.
5. UI sends `{"type": "webrtc.answer", "agent_id": "<id>", "data": <SDP answer>}` back through the coordinator.
6. ICE candidates trickle via `webrtc.ice` messages in both directions through the coordinator.
7. Once the P2P connection is established, video flows directly between agent and UI — the coordinator is no longer in the data path.

### Bandwidth Impact

With WebRTC active, the coordinator only relays signaling messages (~5-10 KB per connection setup) plus the regular event/command JSON. Frame bandwidth drops to zero on the coordinator since video flows peer-to-peer.

### Fallback

If WebRTC negotiation fails (ex., both peers behind symmetric NAT with no TURN server), the system falls back to the standard base64 frame relay transparently.

---

## Implementing a Third-Party Client

### Minimal Agent Client

1. Open a WebSocket to `ws(s)://<coordinator>/agent?agent_id=<id>&code=<session>&token=<secret>`
2. Send a `hello` message: `{"type": "hello", "name": "My Agent", "host": "my-machine", "protocol_version": 1}`
3. Periodically send `frame` messages with base64 JPEG screen captures
4. Forward execution events as `event` messages
5. Listen for `command` messages and dispatch them

### Minimal UI Client

1. Open a WebSocket to `ws(s)://<coordinator>/ui?code=<session>&token=<secret>`
2. Receive the initial `agents` roster
3. Send `watch` to subscribe to a specific agent's frame stream
4. Send `command` messages to control agents
5. Handle incoming `event` and `frame` messages for live updates
