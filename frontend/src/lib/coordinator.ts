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
 * (e.g. "http://localhost:8770" or "https://<ngrok-host>"). A token may be set
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

export interface RemoteBlock {
  stepIndex: number | null;
  verdict: string | null;
  trigger: string | null;
  reason: string | null;
  suggestions: { label: string; action: string }[];
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
}

export interface RemoteEvent {
  type: string;
  data: Record<string, unknown>;
}

export type RemoteCommand = "intent" | "approve" | "halt" | "override" | "mode";

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

export interface CoordinatorApi extends CoordinatorState {
  watch: (agentId: string) => void;
  sendCommand: (
    agentId: string,
    command: RemoteCommand,
    payload?: Record<string, unknown>,
  ) => void;
  selected: RemoteAgent | null;
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
  const [, bump] = useReducer((n: number) => n + 1, 0);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
        if (msg.agent_id === selectedRef.current) bump();
      } else if (msg.type === "frame" && msg.agent_id === selectedRef.current && msg.data) {
        setFrame(`data:image/jpeg;base64,${msg.data}`);
        setFrameTs(msg.ts ?? Date.now());
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
      selectedRef.current = null;
      setSelectedId(null);
      setAgents([]);
      setFrame(null);
      if (retryRef.current) clearTimeout(retryRef.current);
      wsRef.current?.close(); // onclose schedules reconnect with the new code
    },
    [],
  );

  const watch = useCallback((agentId: string) => {
    selectedRef.current = agentId;
    setSelectedId(agentId);
    setFrame(null);
    // The coordinator replays this agent's history on watch, so drop anything
    // we accumulated from the live broadcast to avoid duplicate log lines.
    eventsRef.current.delete(agentId);
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

  const events = selectedId ? eventsRef.current.get(selectedId) ?? [] : [];
  const selected = agents.find((a) => a.id === selectedId) ?? null;

  return {
    conn,
    agents,
    selectedId,
    events,
    frame,
    frameTs,
    watch,
    sendCommand,
    selected,
    code,
    setCode,
  };
}
