"use client";

import React, {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

export interface ShepherdEvent {
  type: string;
  data: Record<string, unknown>;
}

export interface MonitorAlert {
  reason: string;
  verdict: string;
  stepIndex: number;
}

export interface VerifierResult {
  verdict: string;
  confidence: number;
  explanation: string;
  model: string;
}

export interface ExecutionState {
  status: "idle" | "running" | "halted";
  mode: string;
  routineId: string | null;
  runId: string | null;
  stepIndex: number | null;
  monitorAlert: MonitorAlert | null;
  verifierResult: VerifierResult | null;
}

const DEFAULT_STATE: ExecutionState = {
  status: "idle",
  mode: "LOCKED",
  routineId: null,
  runId: null,
  stepIndex: null,
  monitorAlert: null,
  verifierResult: null,
};

interface ShepherdContextValue {
  state: ExecutionState;
  events: ShepherdEvent[];
  connected: boolean;
}

const ShepherdContext = createContext<ShepherdContextValue>({
  state: DEFAULT_STATE,
  events: [],
  connected: false,
});

const WS_URL =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8765/ws")
    : "";

function applyEvent(
  prev: ExecutionState,
  ev: ShepherdEvent,
): ExecutionState {
  const d = ev.data;
  switch (ev.type) {
    case "execution.start":
      return {
        ...prev,
        status: "running",
        routineId: (d.routine_id as string) ?? null,
        runId: (d.run_id as string) ?? null,
        stepIndex: 0,
        monitorAlert: null,
        verifierResult: null,
      };
    case "step.start":
      return {
        ...prev,
        status: "running",
        stepIndex: (d.index as number) ?? prev.stepIndex,
      };
    case "execution.complete":
      return {
        ...prev,
        status: "idle",
        stepIndex: null,
        monitorAlert: null,
        verifierResult: null,
      };
    case "execution.halted":
      return { ...prev, status: "halted" };
    case "monitor.alert":
      return {
        ...prev,
        status: "halted",
        monitorAlert: {
          reason: (d.reason as string) ?? "Unknown",
          verdict: (d.verdict as string) ?? "flag",
          stepIndex: (d.step_index as number) ?? prev.stepIndex ?? 0,
        },
      };
    case "verifier.result":
      return {
        ...prev,
        verifierResult: {
          verdict: (d.verdict as string) ?? "flag",
          confidence: (d.confidence as number) ?? 0.5,
          explanation: (d.explanation as string) ?? "",
          model: (d.model as string) ?? "",
        },
      };
    case "monitor.decision":
      return {
        ...prev,
        status: d.decision === "halt" ? "halted" : "running",
        monitorAlert: d.decision === "halt" ? prev.monitorAlert : null,
        verifierResult: null,
      };
    case "mode.changed":
      return { ...prev, mode: (d.mode as string) ?? prev.mode };
    default:
      return prev;
  }
}

export function ShepherdProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [state, setState] = useState<ExecutionState>(DEFAULT_STATE);
  const [events, setEvents] = useState<ShepherdEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mountedRef = useRef(true);

  const connect = () => {
    if (!WS_URL || !mountedRef.current) return;
    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (mountedRef.current) setConnected(true);
      };
      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        reconnectRef.current = setTimeout(connect, 3000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        if (!mountedRef.current) return;
        try {
          const msg = JSON.parse(e.data as string) as ShepherdEvent;
          setEvents((prev) => [msg, ...prev].slice(0, 200));
          setState((prev) => applyEvent(prev, msg));
        } catch {
          /* ignore malformed */
        }
      };
    } catch {
      /* ignore bad URL */
    }
  };

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      reconnectRef.current && clearTimeout(reconnectRef.current);
      wsRef.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <ShepherdContext.Provider value={{ state, events, connected }}>
      {children}
    </ShepherdContext.Provider>
  );
}

export function useShepherd() {
  return useContext(ShepherdContext);
}
