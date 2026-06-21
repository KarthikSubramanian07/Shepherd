# Peering: Remote Operation Between Machines

## What Is Peering?

Peering is the capability for one machine (the **operator**) to remotely monitor and operate another machine (the **operated agent**) through the Shepherd Coordinator relay. The operated machine runs an AI desktop agent under Shepherd's governance; the operator machine provides human oversight, intervention, and steering — from anywhere.

Neither machine needs to expose an inbound port. Both dial **out** to a shared coordinator, which brokers the connection.

---

## Use Case

### The Problem

You have an AI desktop agent (Agent S, or any GUI automation) running on a machine — a cloud VM, a colleague's workstation, a CI runner, a home lab box. You need to:

1. **Watch it work** — see its screen in real time, see which workflow milestone it's on, see what it decided at each step.
2. **Intervene when it's stuck** — the agent hits a fork it wasn't trained for, a CAPTCHA, a consent dialog, an ambiguous form. You need to steer it without walking over to the machine.
3. **Halt it instantly** — the agent is about to do something irreversible (submit a form, send an email, delete a file). You need a remote kill switch.
4. **Teach it** — when you steer it through an unfamiliar branch, you want that steer remembered so the agent handles it autonomously next time.

### Why Not Just VNC/RDP?

VNC and RDP give you a raw remote desktop. They don't give you:

- **Structured oversight** — which routine step is running, what the safety monitor flagged, the hash-chain audit trail.
- **Semantic commands** — "approve this step", "switch to the research branch", "halt and roll back" — as first-class operations, not mouse clicks on a distant screen.
- **Scoped multi-agent** — ten machines running ten different tasks, all visible in one dashboard, each in its own session, with one operator switching between them.
- **Bandwidth-efficient streaming** — downscaled JPEG frames at 1–3 fps (~100 Kbps–2 Mbps), not a full-fidelity video stream.
- **No inbound ports** — the operated machine dials out; it never listens. Works behind NAT, firewalls, corporate networks.

### Concrete Scenarios

| Scenario | Operator | Operated Agent | Why Peering? |
|----------|----------|----------------|--------------|
| QA engineer overseeing an automated form-fill | Laptop on home WiFi | Cloud VM running Shepherd + Agent S | Watch the agent fill forms, approve high-stakes steps, steer when it gets stuck on a new field |
| Developer testing a workflow | MacBook | Linux desktop in the office | Iterate on workflow milestones remotely — pause at each node, inspect, override, bake corrections |
| On-call monitoring | Phone/tablet (Command Center) | Production automation server | Get alerted when the agent blocks, approve or halt from anywhere |
| Pair-programming with an AI agent | Your machine | Colleague's machine running the agent | Watch the agent's decisions, send intents ("now open the email app"), override its routing |

---

## Architecture (Current Implementation)

```
┌─────────────────────────┐                           ┌─────────────────────────┐
│   OPERATED MACHINE      │                           │   OPERATOR MACHINE      │
│                         │                           │                         │
│  ┌───────────────────┐  │    ┌────────────────┐    │  ┌───────────────────┐  │
│  │ Shepherd Agent     │──┼───▶│  Coordinator   │◀───┼──│ Command Center    │  │
│  │ (relay_client.py)  │  │    │  (server.py)   │    │  │ (Next.js /remote) │  │
│  └───────────────────┘  │    └────────────────┘    │  └───────────────────┘  │
│         │                │     star topology         │                         │
│  ┌──────▼────────────┐  │     (can be on either     │                         │
│  │ Desktop / Browser  │  │      machine or a 3rd)    │                         │
│  │ (screen captured)  │  │                           │                         │
│  └───────────────────┘  │                           │                         │
└─────────────────────────┘                           └─────────────────────────┘
```

**Current flow:**

1. Operated machine runs `scripts/operate.py` or the full `main.py` with `COORDINATOR_URL` set.
2. The relay client (`services/relay_client.py`) opens an outbound WebSocket to `/agent` on the coordinator.
3. It sends a `hello`, then continuously pushes `event` messages (execution state) and `frame` messages (JPEG screenshots).
4. The operator opens the Command Center (`/remote` page), which connects to `/ui` on the coordinator.
5. The operator sees the live roster, watches an agent's screen, and sends commands (`intent`, `approve`, `halt`, `override`, `mode`, `workflow.*`).
6. Commands flow down through the coordinator to the operated machine's relay client, which applies them locally.

See `docs/PROTOCOL.md` for the full wire protocol specification.

---

## Theoretical Peering System (Future Design)

The current implementation uses a **hub-and-spoke** model: all agents and UIs connect to a single coordinator. This section describes a theoretical **peer-to-peer extension** that would allow direct machine-to-machine peering without a central relay.

### Design Goals

1. **Zero infrastructure** — two machines should be able to peer with no coordinator, no server, no public IP. Just a shared secret.
2. **Discovery** — peers find each other automatically on the same network (mDNS/Bonjour), or via an optional rendezvous hint (a coordinator URL, a Tailscale hostname, a manual IP).
3. **Symmetric roles** — either peer can be the operator OR the operated, or both simultaneously (bidirectional observation).
4. **Graceful upgrade** — if a coordinator is available, use it for NAT traversal and multi-agent routing; if not, fall back to direct WebSocket.
5. **Same protocol** — reuse the existing wire protocol (`hello`, `event`, `frame`, `command`) so existing clients work unchanged.

### Peer Discovery

```
┌───────────────────────────────────────────────────────────────┐
│                        DISCOVERY LAYER                         │
├───────────────┬───────────────────┬───────────────────────────┤
│  Local LAN    │  Tailscale mesh   │  Coordinator-assisted     │
│  (mDNS)      │  (MagicDNS)       │  (rendezvous)             │
├───────────────┼───────────────────┼───────────────────────────┤
│ Broadcast     │ shepherd-agent.   │ POST /api/peers/announce   │
│ _shepherd.    │ tailnet-name.ts.  │ { id, name, tailscale_ip, │
│ _tcp.local    │ net               │   capabilities }           │
└───────────────┴───────────────────┴───────────────────────────┘
```

**Three discovery modes (checked in order):**

1. **mDNS (LAN)** — the agent advertises `_shepherd._tcp.local` with its `agent_id`, `code`, and port. A peer on the same subnet discovers it without configuration.
2. **Tailscale MagicDNS** — if both machines are on a tailnet, the agent registers a stable hostname (ex. `shepherd-<agent_id>.<tailnet>.ts.net`). The operator resolves it directly.
3. **Coordinator rendezvous** — if a coordinator is available, the agent announces itself at `POST /api/peers/announce`. The operator queries `GET /api/peers?code=<session>` to get the agent's reachable address, then connects directly (the coordinator is only used for discovery, not relay).

### Connection Establishment (Handshake)

```
Operator                                          Operated Agent
   │                                                    │
   │  1. Discover peer (mDNS / MagicDNS / coordinator) │
   │◀───────────────────────────────────────────────────│
   │                                                    │
   │  2. WebSocket CONNECT to ws://peer:8770/agent      │
   │───────────────────────────────────────────────────▶│
   │                                                    │
   │  3. Mutual hello (both send protocol_version,      │
   │     capabilities, role)                            │
   │◀──────────────────────────────────────────────────▶│
   │                                                    │
   │  4. Token validation (shared secret or             │
   │     Tailscale identity)                            │
   │◀──────────────────────────────────────────────────▶│
   │                                                    │
   │  5. Session established — events/frames/commands   │
   │     flow bidirectionally                           │
   │◀══════════════════════════════════════════════════▶│
```

**Extended `hello` for peering:**

```json
{
  "type": "hello",
  "protocol_version": 1,
  "name": "My Machine",
  "host": "vm-desktop",
  "mode": "LIVE",
  "peer": true,
  "capabilities": ["observe", "command", "workflow"],
  "role": "operated"
}
```

New fields (all optional, backward-compatible):

| Field | Type | Description |
|-------|------|-------------|
| `peer` | boolean | `true` if this is a direct peer connection (no coordinator) |
| `capabilities` | string[] | What this peer can do: `observe` (send frames/events), `command` (accept commands), `workflow` (run workflow executor) |
| `role` | string | `"operated"` (being watched), `"operator"` (watching/commanding), or `"both"` |

### Authentication in Peer Mode

| Method | When | How |
|--------|------|-----|
| **Shared token** | Manual pairing | Same `token` query param as coordinator mode |
| **Tailscale identity** | Both on a tailnet | Verify the peer's Tailscale identity via the local API (`/localapi/v0/whois`); no shared secret needed |
| **mTLS** | Enterprise / high-security | Both peers present client certs signed by a shared CA |

### Capability Negotiation

After the mutual hello, peers negotiate what flows between them:

```json
{
  "type": "session.negotiate",
  "observe": true,
  "command": true,
  "frame_fps": 3,
  "frame_width": 1024,
  "frame_quality": 55
}
```

This lets the operator request a lower frame rate on a slow connection, or the operated machine advertise that it only supports observation (no remote commands).

### Topology Modes

| Mode | Coordinator | Use case |
|------|-------------|----------|
| **Direct peer** | None | Two machines on same LAN or tailnet. Lowest latency, zero infrastructure. |
| **Coordinator-relayed** (current) | Required | Machines on different networks, NAT traversal needed, multi-agent. |
| **Coordinator-assisted direct** | Discovery only | Coordinator helps peers find each other, then they connect directly (WebRTC-style). |
| **Hybrid** | Optional | Agent connects to coordinator AND accepts direct peers. Operator chooses lowest-latency path. |

### Multi-Agent Peering (Fleet)

In a fleet scenario, the operator peers with multiple operated machines:

```
                    ┌──── Operated A (peer, direct)
                    │
Operator ───────────┼──── Operated B (peer, direct)
                    │
                    └──── Operated C (via coordinator, NAT'd)
```

The Command Center maintains multiple WebSocket connections — some direct, some through the coordinator — and presents them all in a unified roster. The `code` session scoping still applies: only peers sharing a code see each other.

### State Synchronization

In direct-peer mode, there's no coordinator to maintain the canonical roster. Instead:

- Each peer maintains its own state and pushes `snapshot` updates.
- The operator's Command Center is the "view" — it aggregates snapshots from all connected peers.
- If a coordinator is also connected (hybrid mode), the coordinator's roster is authoritative.

### Failure Modes & Fallback

| Failure | Behavior |
|---------|----------|
| Direct peer unreachable | Fall back to coordinator relay (if available) |
| Coordinator down | Direct peers continue operating; coordinator-only peers disconnect and retry |
| Network partition | Operated machine continues locally (Shepherd is never on the critical path); reconnects when network returns |
| Peer sends unknown protocol_version | Log warning, continue with shared subset (backward-compatible) |

---

## Implementation Roadmap

| Phase | What | Effort |
|-------|------|--------|
| **0 (done)** | Coordinator relay — hub-and-spoke, full protocol | Shipped (PR #8) |
| **1** | Direct peer mode — agent listens on local port, operator connects directly (skip coordinator). Reuses existing protocol. | Small — add a `--listen` flag to `operate.py` |
| **2** | mDNS discovery — auto-find peers on LAN | Medium — add `zeroconf` dep, advertise/browse |
| **3** | Coordinator-assisted rendezvous — announce/discover via HTTP, then connect direct | Medium — new `/api/peers/*` endpoints |
| **4** | Mutual hello + capability negotiation | Small — extend hello schema |
| **5** | Tailscale identity auth — verify peer without shared token | Small — call local Tailscale API |
| **6** | Hybrid mode — simultaneous coordinator + direct connections | Medium — connection manager in Command Center |

---

## Relation to Existing Components

| Component | Role in Peering |
|-----------|----------------|
| `coordinator/server.py` | Hub relay (phase 0); rendezvous server (phase 3) |
| `services/relay_client.py` | Outbound connection to coordinator; would also handle direct peer connections |
| `scripts/operate.py` | Operated-machine launcher; would gain `--listen` for direct peer mode |
| `frontend/src/lib/coordinator.ts` | UI WebSocket client; would manage multiple connections in fleet mode |
| `config.py` / `PROTOCOL_VERSION` | Shared version constant for negotiation |
| `docs/PROTOCOL.md` | Wire protocol spec (applies to both coordinator and direct modes) |

---

## Summary

Peering extends Shepherd's remote orchestration from a hub-and-spoke coordinator model to support direct machine-to-machine connections. The key insight is that the **wire protocol doesn't change** — the same `hello`/`event`/`frame`/`command` messages work whether they pass through a coordinator or flow directly between peers. The coordinator becomes optional infrastructure for NAT traversal and multi-network routing, not a hard dependency.

The immediate, shipped system (coordinator relay) already enables the core use case: one machine monitoring and operating another. The theoretical peering extensions would reduce infrastructure requirements to zero for same-network deployments while maintaining the same operational semantics.
