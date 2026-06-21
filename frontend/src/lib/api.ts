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
  version: number;
  intent_patterns: string[];
  params: string[];
  nodes: number;
  updated_at: number;
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

export const api = {
  // Routines (the recorded "tools")
  listRoutines: () => http<RoutineSummary[]>("/routines"),
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

  // Task graph (REAL backend) — crystallized milestone workflow for a routine.
  // Returns null when the backend has no graph yet (404).
  getTaskGraph: async (routineId: string): Promise<TaskGraph | null> => {
    const res = await fetch(`${BACKEND}/api/task-graph/${routineId}`, {
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
};

export type Api = typeof api;
