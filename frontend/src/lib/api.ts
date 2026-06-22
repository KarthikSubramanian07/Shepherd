/**
 * Typed client for the dashboard API.
 *
 * When NEXT_PUBLIC_API_BASE is set (ex. "http://localhost:8765") all calls go
 * directly to the FastAPI backend. Without it, calls fall through to the
 * Next.js API route handlers (mock data for offline development).
 */
import type {
  Agent,
  Intervention,
  InterventionResolution,
  Routine,
  RoutineSummary,
  Run,
  RunSummary,
  TaskGraph,
} from "./types";
import type { RemoteTrace } from "./coordinator";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

// The real FastAPI backend (Control Hub). Used for live endpoints like the
// crystallized task graph; defaults to the local dashboard server.
const BACKEND = process.env.NEXT_PUBLIC_BACKEND_BASE ?? "http://localhost:8765";

function apiUrl(path: string): string {
  return `${BASE}/api${path}`;
}

async function http<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(apiUrl(path), {
    cache: "no-store",
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    throw new Error(`API ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export interface AuditEntry {
  seq: number;
  run_id: string;
  step_index: number;
  action: string;
  target: string | null;
  status: string;
  duration_ms: number;
  ts: number;
  prev_hash: string;
  hash: string;
}

export interface AuditVerification {
  valid: boolean;
  entries: number;
  tampered_at: number | null;
  reason: string;
}

export interface ModeResult {
  ok: boolean;
  mode: string;
}

export interface WorkflowSummary {
  id: string;
  name: string;
  description?: string;
  version: number;
  intent_patterns: string[];
  params: string[];
  nodes: number;
  updated_at: number;
}

export interface TaskGraphSummary {
  task_key: string;
  routine_id: string | null;
  run_count: number;
  node_count: number;
  edge_count: number;
  updated_at: number;
  intents: string[];
  labels: string[];
}

export interface WorkflowNodeRaw {
  key: string;
  kind: string;
  label: string;
  instruction: string;
  requires: string[];
  conditionals: { when: string; do: string; goto: string | null; source?: string }[];
  optional?: boolean;
  source?: string;
}

export interface WorkflowEdgeRaw {
  from: string;
  to: string;
  condition: string | null;
}

export interface WorkflowDetail {
  id: string;
  name: string;
  description?: string;
  intent_patterns: string[];
  params: string[];
  version: number;
  from_graph: string;
  start_key: string;
  created_at: number;
  updated_at: number;
  nodes: WorkflowNodeRaw[];
  edges: WorkflowEdgeRaw[];
}

export interface RedisStats {
  available: boolean;
  connection: "local" | "cloud";
  version: string | null;
  vector_routing: {
    available: boolean;
    indexed_routines?: number;
    dim?: number;
    threshold?: number;
    model?: string;
  };
  agent_memory: {
    available: boolean;
    runs_stored?: number;
    learned_variables?: number;
    runs_indexed?: number;
  };
  semantic_cache: {
    available: boolean;
    entries?: number;
    hits?: number;
    misses?: number;
    hit_rate?: number;
  };
  last_match: { routine_id: string | null; similarity: number | null } | null;
}

export interface Integration {
  name: string;
  category: string;
  status: "active" | "ready" | "off";
  detail: string;
}

export const api = {
  // Routines (the recorded "tools")
  listRoutines: () => http<RoutineSummary[]>("/routines"),
  // Redis — vector routing, agent memory, semantic cache
  getRedisStats: () => http<RedisStats>("/redis/stats"),
  // Live status of every integration (active / ready / off)
  getIntegrations: () => http<{ integrations: Integration[] }>("/integrations"),
  getRoutine: (id: string) => http<Routine>(`/routines/${id}`),

  // Agents (live instances)
  listAgents: () => http<Agent[]>("/agents"),
  getAgent: (id: string) => http<Agent>(`/agents/${id}`),

  // Runs (replays / traversals)
  listRuns: () => http<RunSummary[]>("/runs"),
  getRun: (id: string) => http<Run>(`/runs/${id}`),

  // Interventions (human-in-the-loop)
  listInterventions: () => http<Intervention[]>("/interventions"),
  resolveIntervention: (
    id: string,
    resolution: InterventionResolution,
    note?: string,
  ) =>
    http<Intervention>(`/interventions/${id}`, {
      method: "POST",
      body: JSON.stringify({ resolution, note }),
    }),

  // Every stored task graph (newest first) — including dynamically-generated
  // AUTONOMOUS::<goal> graphs. Lets the UI discover graph keys without hardcoding.
  listTaskGraphs: async (): Promise<TaskGraphSummary[]> => {
    const res = await fetch(`${BACKEND}/api/task-graphs`, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`task-graphs failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<TaskGraphSummary[]>;
  },

  // Task graph (REAL backend) — crystallized milestone workflow for a routine.
  // Returns null when the backend has no graph yet (404).
  getTaskGraph: async (routineId: string): Promise<TaskGraph | null> => {
    const res = await fetch(`${BACKEND}/api/task-graph/${encodeURIComponent(routineId)}`, {
      cache: "no-store",
    });
    if (res.status === 404) return null;
    if (!res.ok) {
      throw new Error(`task-graph ${routineId} failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<TaskGraph>;
  },

  // Workflows (REAL backend) — dispatchable, versioned snapshots of a task
  // graph; this is what the executor runs and what remember-bake increments.
  listWorkflows: async (): Promise<WorkflowSummary[]> => {
    const res = await fetch(`${BACKEND}/api/workflows`, { cache: "no-store" });
    if (!res.ok) {
      throw new Error(`workflows failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<WorkflowSummary[]>;
  },
  getWorkflow: async (id: string): Promise<WorkflowDetail | null> => {
    const res = await fetch(`${BACKEND}/api/workflows/${id}`, {
      cache: "no-store",
    });
    if (res.status === 404) return null;
    if (!res.ok) {
      throw new Error(`workflow ${id} failed: ${res.status} ${res.statusText}`);
    }
    return res.json() as Promise<WorkflowDetail>;
  },

  // Governance — audit + policy
  getAuditLog: () => http<AuditEntry[]>("/audit"),
  verifyAuditChain: () => http<AuditVerification>("/audit/verify"),
  getPolicy: () => http<Record<string, unknown>>("/policy"),

  // Control
  setMode: (mode: string) =>
    http<ModeResult>(`/mode/${mode}`, { method: "POST" }),
  approveStep: () =>
    http<{ ok: boolean }>("/control/approve", { method: "POST" }),
  haltExecution: () =>
    http<{ ok: boolean }>("/control/halt", { method: "POST" }),

  // Run a goal on the local in-process agent (POST /api/intent on the backend).
  // Hits BACKEND directly so it works whether or not NEXT_PUBLIC_API_BASE is set.
  runGoal: async (text: string): Promise<{ ok?: boolean; error?: string }> => {
    const res = await fetch(`${BACKEND}/api/intent`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    return res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  },

  // ── Fleet (multi-agent orchestration) ──────────────────────────────────────
  getFleet: async (): Promise<FleetSnapshot> => {
    const res = await fetch(`${BACKEND}/api/fleet`, { cache: "no-store" });
    return res.json();
  },
  dispatchAgent: async (
    goal: string,
    surfaceKind: "local" | "browserbase",
    name?: string,
  ): Promise<{ ok?: boolean; agent_id?: string; error?: string }> => {
    const res = await fetch(`${BACKEND}/api/fleet/dispatch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ goal, surface_kind: surfaceKind, name }),
    });
    return res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  },
  dispatchBatch: async (
    tasks: { goal: string; surface_kind: "local" | "browserbase" }[],
  ): Promise<{ ok?: boolean; agent_ids?: string[]; errors?: unknown[]; error?: string }> => {
    const res = await fetch(`${BACKEND}/api/fleet/dispatch_batch`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ tasks }),
    });
    return res.json().catch(() => ({ error: `HTTP ${res.status}` }));
  },
  haltAgent: async (agentId: string): Promise<{ ok?: boolean }> => {
    const res = await fetch(`${BACKEND}/api/fleet/halt/${agentId}`, { method: "POST" });
    return res.json().catch(() => ({ ok: false }));
  },
  haltAllAgents: async (): Promise<{ ok?: boolean; halted?: number }> => {
    const res = await fetch(`${BACKEND}/api/fleet/halt_all`, { method: "POST" });
    return res.json().catch(() => ({ ok: false }));
  },
  getAgentTrace: async (agentId: string): Promise<RemoteTrace | null> => {
    const res = await fetch(`${BACKEND}/api/fleet/${agentId}/trace`, { cache: "no-store" });
    const body = await res.json().catch(() => null);
    return body?.trace ?? null;
  },
};

export interface FleetAgent {
  agent_id: string;
  name: string;
  goal: string;
  surface_kind: string;
  surface: string | null;
  status: string;
  error: string | null;
  /** Medium NL summary of the finished run (empty while running). */
  response?: string;
  started_at: number;
  duration_ms: number;
}

export interface QueueSurface {
  surface: string;
  holder: string | null;
  held_ms: number;
  waiters: { agent_id: string; priority: number }[];
}

export interface FleetSnapshot {
  enabled: boolean;
  agents: FleetAgent[];
  backlog: { agent_id: string; goal: string; surface_kind: string }[];
  queue: QueueSurface[];
  max_agents?: number;
  active?: number;
}

export type Api = typeof api;
