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

export interface CouncilVote {
  handle: string;
  verdict: string;
  reason: string;
}

export interface VerifierResult {
  verdict: string;
  confidence: number;
  explanation: string;
  model: string;
  /** Per-agent breakdown when the verdict came from the Band oversight council. */
  votes: CouncilVote[];
}

export type LiveNodeStatus =
  | "pending"
  | "running"
  | "done"
  | "flagged"
  | "halted";

export interface LiveGraphNode {
  key: string;
  label: string;
  kind: string;
  status: LiveNodeStatus;
  /** True if this milestone was already known from prior runs (recalled vs new). */
  known: boolean;
}

export interface ExecutionState {
  status: "idle" | "running" | "halted";
  mode: string;
  routineId: string | null;
  runId: string | null;
  stepIndex: number | null;
  monitorAlert: MonitorAlert | null;
  verifierResult: VerifierResult | null;
  /** ArmorIQ pre-flight intent authorization for the active run (null until issued). */
  armoriqGate: { authorized: boolean; reason: string } | null;
  /** Live milestone graph that replays node-by-node as the agent runs. */
  graphNodes: LiveGraphNode[];
  /** Maps a fine step index → graph node key (built from task.graph.loaded). */
  stepToNode: Record<number, string>;
  /** Total step count for the active run (for progress when no graph seeded). */
  totalSteps: number;
}

const DEFAULT_STATE: ExecutionState = {
  status: "idle",
  mode: "LOCKED",
  routineId: null,
  runId: null,
  stepIndex: null,
  monitorAlert: null,
  verifierResult: null,
  armoriqGate: null,
  graphNodes: [],
  stepToNode: {},
  totalSteps: 0,
};

/** Mark every node before `key` done, `key` running, the rest untouched. */
function advanceTo(
  nodes: LiveGraphNode[],
  key: string,
  status: LiveNodeStatus,
): LiveGraphNode[] {
  const idx = nodes.findIndex((n) => n.key === key);
  if (idx === -1) return nodes;
  return nodes.map((n, i) => {
    if (i < idx) return n.status === "pending" ? { ...n, status: "done" } : n;
    if (i === idx) return { ...n, status };
    return n;
  });
}

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
        armoriqGate: null,
        // Reset the live graph; task.graph.loaded (if any) re-seeds it below.
        graphNodes: [],
        stepToNode: {},
        totalSteps: 0,
      };
    case "task.graph.loaded": {
      // Seed the live milestone graph from the run's plan, in execution order.
      const milestones = (d.milestones as
        | { label: string; kind: string; known?: boolean }[]
        | undefined) ?? [];
      const steps = (d.steps as
        | { index: number; milestone: string }[]
        | undefined) ?? [];
      const seen = new Map<string, number>();
      const nodes: LiveGraphNode[] = [];
      for (const m of milestones) {
        const key = `${m.kind}:${m.label}:${seen.get(m.label) ?? 0}`;
        seen.set(m.label, (seen.get(m.label) ?? 0) + 1);
        nodes.push({
          key,
          label: m.label,
          kind: m.kind,
          status: "pending",
          known: !!m.known,
        });
      }
      // Map each fine step index → the matching milestone node (by label).
      const stepToNode: Record<number, string> = {};
      for (const s of steps) {
        const node = nodes.find((n) => n.label === s.milestone);
        if (node) stepToNode[s.index] = node.key;
      }
      return { ...prev, graphNodes: nodes, stepToNode };
    }
    case "step.start": {
      const index = (d.index as number) ?? prev.stepIndex ?? 0;
      const total = (d.total as number) ?? prev.totalSteps;
      let nodes = prev.graphNodes;
      const key = prev.stepToNode[index];
      if (key) {
        nodes = advanceTo(nodes, key, "running");
      } else if (prev.graphNodes.length === 0) {
        // No milestone plan (ex. AUTONOMOUS) · build a linear graph live.
        const k = `step:${index}`;
        if (!prev.graphNodes.some((n) => n.key === k)) {
          nodes = [
            ...prev.graphNodes.map((n) =>
              n.status === "running" ? { ...n, status: "done" as LiveNodeStatus } : n,
            ),
            {
              key: k,
              label: (d.description as string) ?? `Step ${index + 1}`,
              kind: (d.action as string) ?? "interact",
              status: "running" as LiveNodeStatus,
              known: false,
            },
          ];
        }
      }
      return {
        ...prev,
        status: "running",
        stepIndex: index,
        totalSteps: total,
        graphNodes: nodes,
      };
    }
    case "step.complete": {
      const index = (d.index as number) ?? prev.stepIndex ?? 0;
      const failed = (d.status as string) === "failed";
      const key = prev.stepToNode[index] ?? `step:${index}`;
      const nodes = prev.graphNodes.map((n) =>
        n.key === key
          ? { ...n, status: (failed ? "flagged" : "done") as LiveNodeStatus }
          : n,
      );
      return { ...prev, graphNodes: nodes };
    }
    case "execution.complete":
      return {
        ...prev,
        status: "idle",
        stepIndex: null,
        monitorAlert: null,
        verifierResult: null,
        graphNodes: prev.graphNodes.map((n) =>
          n.status === "running" || n.status === "pending"
            ? { ...n, status: "done" }
            : n,
        ),
      };
    case "execution.halted":
      return {
        ...prev,
        status: "halted",
        graphNodes: prev.graphNodes.map((n) =>
          n.status === "running" ? { ...n, status: "halted" } : n,
        ),
      };
    case "monitor.alert":
      return {
        ...prev,
        status: "halted",
        monitorAlert: {
          reason: (d.reason as string) ?? "Unknown",
          verdict: (d.verdict as string) ?? "flag",
          stepIndex: (d.step_index as number) ?? prev.stepIndex ?? 0,
        },
        graphNodes: prev.graphNodes.map((n) =>
          n.status === "running" ? { ...n, status: "flagged" } : n,
        ),
      };
    case "verifier.result":
      return {
        ...prev,
        verifierResult: {
          verdict: (d.verdict as string) ?? "flag",
          confidence: (d.confidence as number) ?? 0.5,
          explanation: (d.explanation as string) ?? "",
          model: (d.model as string) ?? "",
          votes: (d.votes as CouncilVote[]) ?? [],
        },
      };
    case "monitor.decision":
      return {
        ...prev,
        status: d.decision === "halt" ? "halted" : "running",
        monitorAlert: d.decision === "halt" ? prev.monitorAlert : null,
        verifierResult: null,
      };
    // ── Workflow traversal (the dispatched-workflow path) ───────────────────
    // Same live graph, fed by the workflow.* stream instead of step.* · so the
    // command-center replays both routine runs and free workflow traversals.
    case "workflow.start":
      return {
        ...prev,
        status: "running",
        routineId: (d.name as string) ?? (d.workflow_id as string) ?? prev.routineId,
        runId: (d.workflow_id as string) ?? prev.runId,
        monitorAlert: null,
        verifierResult: null,
        graphNodes: [],
        stepToNode: {},
        totalSteps: 0,
      };
    case "workflow.node.enter": {
      const key = d.node_key as string;
      if (!key) return prev;
      const exists = prev.graphNodes.some((n) => n.key === key);
      let nodes = prev.graphNodes.map((n) =>
        n.status === "running" ? { ...n, status: "done" as LiveNodeStatus } : n,
      );
      if (exists) {
        nodes = nodes.map((n) =>
          n.key === key ? { ...n, status: "running" as LiveNodeStatus } : n,
        );
      } else {
        nodes = [
          ...nodes,
          {
            key,
            label: (d.label as string) ?? key,
            kind: (d.kind as string) ?? "interact",
            status: "running" as LiveNodeStatus,
            known: false,
          },
        ];
      }
      return { ...prev, status: "running", graphNodes: nodes };
    }
    case "workflow.step": {
      const key = d.node_key as string;
      const st = d.status as string;
      const ns: LiveNodeStatus =
        st === "blocked" || st === "failed" ? "flagged" : "done";
      return {
        ...prev,
        graphNodes: prev.graphNodes.map((n) =>
          n.key === key ? { ...n, status: ns } : n,
        ),
      };
    }
    case "workflow.intervention":
      return {
        ...prev,
        graphNodes: prev.graphNodes.map((n) =>
          n.status === "running" ? { ...n, status: "flagged" } : n,
        ),
      };
    case "workflow.done":
      return {
        ...prev,
        status: (d.status as string) === "blocked" ? "halted" : "idle",
        graphNodes: prev.graphNodes.map((n) =>
          n.status === "running" || n.status === "pending"
            ? { ...n, status: "done" }
            : n,
        ),
      };
    // ── ArmorIQ pre-flight intent authorization ─────────────────────────────
    case "armoriq.authorized":
      return {
        ...prev,
        armoriqGate: {
          authorized: true,
          reason: (d.reason as string) ?? "Intent token issued",
        },
      };
    case "armoriq.denied":
      return {
        ...prev,
        status: "halted",
        armoriqGate: {
          authorized: false,
          reason: (d.reason as string) ?? "ArmorIQ denied the plan",
        },
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
