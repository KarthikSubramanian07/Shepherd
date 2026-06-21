"use client";

/**
 * Live client for the Shepherd Coordinator (the remote relay).
 *
 * The Command Center talks to ONE coordinator over a single WebSocket. The
 * coordinator multiplexes every operated machine ("agent"): it pushes the live
 * agent roster, every agent's event stream, and the *watched* agent's screen
 * frames; we send commands (intent / approve / halt / override / mode) targeted
 * at a specific agent.
 *
 * Configure the coordinator location with NEXT_PUBLIC_COORDINATOR_URL
 * (ex. "http://localhost:8770" or "https://<ngrok-host>"). A token may be set
 * with NEXT_PUBLIC_COORDINATOR_TOKEN when the coordinator enforces one.
 */
import { useCallback, useEffect, useReducer, useRef, useState } from "react";

const CODE_STORAGE_KEY = "shepherd.sessionCode";

export type RemoteAgentStatus =
  | "idle"
  | "running"
  | "blocked"
  | "completed"
  | "failed";

export interface RemoteOption {
  key: string;
  label?: string;
  via?: string;
  when?: string | null;
}

export interface RemoteBlock {
  stepIndex: number | null;
  verdict?: string | null;
  trigger: string | null;
  reason: string | null;
  suggestions: { label: string; action: string }[];
  /** Set when the block is a workflow milestone awaiting an operator directive. */
  workflow?: boolean;
  nodeKey?: string | null;
  label?: string | null;
  options?: RemoteOption[];
}

export interface RemoteWorkflowConditional {
  when: string;
  do: string;
  goto: string | null;
}

export interface RemoteWorkflowNode {
  key: string;
  label?: string;
  kind?: string;
  instruction?: string;
  status?: "pending" | "running" | "done" | "blocked" | "awaiting";
  missing?: string[];
  conditionals?: RemoteWorkflowConditional[];
  options?: RemoteOption[];
  did?: string;
  branch?: string | null;
  next?: string | null;
  extracted?: string[];
  intervention?: {
    decision?: string;
    instruction?: string;
    scenario?: string;
    flag?: string;
  };
  /** Coordinator timestamp of the frame captured when this milestone completed. */
  frameTs?: number;
}

export interface RemoteWorkflowEdge {
  from: string;
  to: string;
  when?: string | null;
}

export interface WorkflowBakeOp {
  op?: string;
  node?: string;
  when?: string;
  do?: string;
  goto?: string | null;
}

export interface RemoteWorkflowFinalize {
  workflow_id: string | null;
  name: string | null;
  current_version: number | null;
  proposed_version: number | null;
  ops: WorkflowBakeOp[];
}

export interface RemoteWorkflowFinalized {
  action: "persisted" | "saved_as_new" | "discarded" | string;
  workflow_id: string | null;
  version: number | null;
}

export interface RemoteWorkflow {
  id: string | null;
  name: string | null;
  current: string | null;
  awaiting: boolean;
  nodes: RemoteWorkflowNode[];
  edges: RemoteWorkflowEdge[];
  status?: string;
  baked?: WorkflowBakeOp[] | null;
  /** Set at run end when baked judgment calls await the operator's persist choice. */
  finalize?: RemoteWorkflowFinalize | null;
  /** Set once the operator resolves the persist gate. */
  finalized?: RemoteWorkflowFinalized | null;
}

export interface RemoteTraceNode {
  index: number;
  action?: string;
  description?: string;
  thinking?: string;
  status?: "pending" | "running" | "completed" | "failed" | "error";
  durationMs?: number;
  error?: string;
  note?: string;
  /** Coordinator timestamp of the frame captured when this step completed. */
  frameTs?: number;
}

export interface RemoteTrace {
  runId: string | null;
  routineId: string | null;
  kind?: string | null;
  /** false → no prior task graph existed: a brand-new task being crystallized. */
  known: boolean | null;
  status?: string;
  current: number | null;
  nodes: RemoteTraceNode[];
  /** True once the coalescer saves the graph and it's eligible for promotion. */
  promoteReady?: boolean;
  /** Set after WorkflowStore.promote() succeeds — the created workflow. */
  promoted?: { workflow_id: string; name: string; description?: string; version?: number } | null;
}

export interface RemoteRouting {
  /** routing → resolving · matched → router hit · unmatched → no hit · autonomous → fresh fallback */
  state: "routing" | "matched" | "unmatched" | "autonomous";
  kind?: "WORKFLOW" | "ROUTINE" | "AUTONOMOUS" | null;
  target?: string | null;
  confidence?: number | null;
  source?: string | null;
  matched?: string[];
  text?: string | null;
}

export interface RemoteStepPeek {
  index: number | null;
  description: string;
}

export interface RemoteAgent {
  id: string;
  name: string;
  host: string;
  code: string;
  online: boolean;
  status: RemoteAgentStatus;
  mode: string;
  routineId: string | null;
  runId: string | null;
  currentStepIndex: number | null;
  totalSteps: number | null;
  progress: number;
  block: RemoteBlock | null;
  lastActivityAt: string;
  hasFrame: boolean;
  workflow: RemoteWorkflow | null;
  routing: RemoteRouting | null;
  /** Async-generated short label of what this agent is working on this run. */
  title: string | null;
  /** Last 2-3 step descriptions from the current/last run. */
  recentSteps: RemoteStepPeek[];
  /** Live granular step trace for runs not following a saved workflow. */
  trace: RemoteTrace | null;
}

export interface RemoteEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface WorkflowIntervenePayload {
  instruction?: string;
  next_key?: string;
  scenario?: string;
  remember?: boolean;
  decision?: string;
  target_node?: string;
}

export interface WorkflowFinalizePayload {
  decision: "persist" | "save_as_new" | "discard";
  new_id?: string;
  name?: string;
}

export type RemoteCommand =
  | "intent"
  | "approve"
  | "halt"
  | "override"
  | "mode"
  | "workflow.pause"
  | "workflow.resume"
  | "workflow.intervene"
  | "workflow.finalize"
  | "promote";

export type ConnState = "connecting" | "open" | "closed";

const HTTP_BASE =
  process.env.NEXT_PUBLIC_COORDINATOR_URL ?? "http://localhost:8770";
const TOKEN = process.env.NEXT_PUBLIC_COORDINATOR_TOKEN ?? "";

export const coordinatorHttpBase = HTTP_BASE;

function wsUrl(path: string, code: string): string {
  let base = HTTP_BASE.replace(/\/$/, "");
  if (base.startsWith("https://")) base = "wss://" + base.slice("https://".length);
  else if (base.startsWith("http://")) base = "ws://" + base.slice("http://".length);
  const params = new URLSearchParams();
  if (TOKEN) params.set("token", TOKEN);
  if (code) params.set("code", code);
  const q = params.toString();
  return `${base}${path}${q ? `?${q}` : ""}`;
}

const MAX_EVENTS = 400;

export interface CoordinatorState {
  conn: ConnState;
  agents: RemoteAgent[];
  selectedId: string | null;
  /** Event log for the currently watched agent (most recent last). */
  events: RemoteEvent[];
  /** data: URL of the watched agent's latest screen frame. */
  frame: string | null;
  frameTs: number;
}

/** Callback for WebRTC signaling messages arriving from the coordinator. */
export type WebRTCSignalHandler = (type: string, agentId: string, data: unknown) => void;

export interface CoordinatorApi extends CoordinatorState {
  watch: (agentId: string) => void;
  sendCommand: (
    agentId: string,
    command: RemoteCommand,
    payload?: Record<string, unknown>,
  ) => void;
  /** Send a WebRTC signaling message to an agent through the coordinator. */
  sendSignal: (agentId: string, type: string, data: unknown) => void;
  /** Register a handler for incoming WebRTC signals (offer/answer/ice). */
  onWebRTCSignal: (handler: WebRTCSignalHandler | null) => void;
  selected: RemoteAgent | null;
  /** Per-milestone screenshots captured from the live frame stream, keyed by
   * workflow node key, for the currently watched agent. */
  nodeShots: Record<string, string>;
  /** Session/pairing code this Command Center is scoped to ("" = all). */
  code: string;
  setCode: (code: string) => void;
}

/** Subscribe to the coordinator and expose live fleet state + command sender. */
export function useCoordinator(): CoordinatorApi {
  const [conn, setConn] = useState<ConnState>("connecting");
  const [agents, setAgents] = useState<RemoteAgent[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [frame, setFrame] = useState<string | null>(null);
  const [frameTs, setFrameTs] = useState(0);

  const [code, setCodeState] = useState<string>("");
  const wsRef = useRef<WebSocket | null>(null);
  const selectedRef = useRef<string | null>(null);
  const codeRef = useRef<string>("");
  const eventsRef = useRef<Map<string, RemoteEvent[]>>(new Map());
  const frameRef = useRef<string | null>(null);
  // Per-agent, per-node screenshots snapshotted from the live frame stream at
  // each workflow.step so the graph can show what the agent did at each milestone.
  const nodeShotsRef = useRef<Map<string, Map<string, string>>>(new Map());
  const [, bump] = useReducer((n: number) => n + 1, 0);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const webrtcHandlerRef = useRef<WebRTCSignalHandler | null>(null);

  // Restore the last-used code from the browser before the first connect.
  useEffect(() => {
    const saved = window.localStorage.getItem(CODE_STORAGE_KEY) ?? "";
    codeRef.current = saved;
    setCodeState(saved);
  }, []);

  const connect = useCallback(() => {
    let ws: WebSocket;
    try {
      ws = new WebSocket(wsUrl("/ui", codeRef.current));
    } catch {
      setConn("closed");
      retryRef.current = setTimeout(connect, 2000);
      return;
    }
    wsRef.current = ws;
    setConn("connecting");

    ws.onopen = () => {
      setConn("open");
      // Re-watch the previously selected agent after a reconnect.
      if (selectedRef.current) {
        ws.send(JSON.stringify({ type: "watch", agent_id: selectedRef.current }));
      }
    };

    ws.onmessage = (ev) => {
      let msg: {
        type: string;
        agents?: RemoteAgent[];
        agent_id?: string;
        event?: RemoteEvent;
        data?: string;
        ts?: number;
      };
      try {
        msg = JSON.parse(ev.data as string);
      } catch {
        return;
      }
      if (msg.type === "agents" && msg.agents) {
        setAgents(msg.agents);
      } else if (msg.type === "event" && msg.agent_id && msg.event) {
        const list = eventsRef.current.get(msg.agent_id) ?? [];
        list.push(msg.event);
        if (list.length > MAX_EVENTS) list.splice(0, list.length - MAX_EVENTS);
        eventsRef.current.set(msg.agent_id, list);
        // Pin the latest live frame to the milestone the agent just finished, so
        // the graph node shows what the agent did there.
        if (msg.event.type === "workflow.step") {
          const nodeKey = msg.event.data?.node_key as string | undefined;
          const shot = frameRef.current;
          if (nodeKey && shot) {
            const shots = nodeShotsRef.current.get(msg.agent_id) ?? new Map();
            shots.set(nodeKey, shot);
            nodeShotsRef.current.set(msg.agent_id, shots);
          }
        } else if (msg.event.type === "workflow.start") {
          nodeShotsRef.current.delete(msg.agent_id);
        } else if (msg.event.type === "step.complete") {
          // Pin the live frame to the execution-trace step that just finished.
          const idx = msg.event.data?.index as number | undefined;
          const shot = frameRef.current;
          if (idx !== undefined && shot) {
            const shots = nodeShotsRef.current.get(msg.agent_id) ?? new Map();
            shots.set(`trace:${idx}`, shot);
            nodeShotsRef.current.set(msg.agent_id, shots);
          }
        } else if (msg.event.type === "execution.start") {
          nodeShotsRef.current.delete(msg.agent_id);
        }
        if (msg.agent_id === selectedRef.current) bump();
      } else if (msg.type === "frame" && msg.agent_id === selectedRef.current && msg.data) {
        const url = `data:image/jpeg;base64,${msg.data}`;
        frameRef.current = url;
        setFrame(url);
        setFrameTs(msg.ts ?? Date.now());
      } else if (
        (msg.type === "webrtc.offer" || msg.type === "webrtc.answer" || msg.type === "webrtc.ice") &&
        msg.agent_id
      ) {
        webrtcHandlerRef.current?.(msg.type, msg.agent_id, msg.data);
      }
    };

    ws.onclose = () => {
      setConn("closed");
      retryRef.current = setTimeout(connect, 2000);
    };
    ws.onerror = () => ws.close();
  }, []);

  useEffect(() => {
    connect();
    return () => {
      if (retryRef.current) clearTimeout(retryRef.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const setCode = useCallback(
    (next: string) => {
      const c = next.trim();
      codeRef.current = c;
      setCodeState(c);
      window.localStorage.setItem(CODE_STORAGE_KEY, c);
      // Reset scoped state and reconnect under the new code.
      eventsRef.current.clear();
      nodeShotsRef.current.clear();
      selectedRef.current = null;
      setSelectedId(null);
      setAgents([]);
      setFrame(null);
      frameRef.current = null;
      if (retryRef.current) clearTimeout(retryRef.current);
      wsRef.current?.close(); // onclose schedules reconnect with the new code
    },
    [],
  );

  const watch = useCallback((agentId: string) => {
    selectedRef.current = agentId;
    setSelectedId(agentId);
    setFrame(null);
    frameRef.current = null;
    // The coordinator replays this agent's history on watch, so drop anything
    // we accumulated from the live broadcast to avoid duplicate log lines.
    eventsRef.current.delete(agentId);
    nodeShotsRef.current.delete(agentId);
    bump();
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "watch", agent_id: agentId }));
    }
  }, []);

  const sendCommand = useCallback(
    (agentId: string, command: RemoteCommand, payload: Record<string, unknown> = {}) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "command", agent_id: agentId, command, payload }));
      }
    },
    [],
  );

  const sendSignal = useCallback(
    (agentId: string, type: string, data: unknown) => {
      const ws = wsRef.current;
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type, agent_id: agentId, data }));
      }
    },
    [],
  );

  const onWebRTCSignal = useCallback((handler: WebRTCSignalHandler | null) => {
    webrtcHandlerRef.current = handler;
  }, []);

  const events = selectedId ? eventsRef.current.get(selectedId) ?? [] : [];
  const selected = agents.find((a) => a.id === selectedId) ?? null;
  const nodeShots: Record<string, string> = selectedId
    ? Object.fromEntries(nodeShotsRef.current.get(selectedId) ?? new Map())
    : {};

  return {
    conn,
    agents,
    selectedId,
    events,
    frame,
    frameTs,
    watch,
    sendCommand,
    sendSignal,
    onWebRTCSignal,
    selected,
    nodeShots,
    code,
    setCode,
  };
}
