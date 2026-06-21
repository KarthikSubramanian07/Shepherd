/**
 * Typed client for the dashboard API.
 *
 * When NEXT_PUBLIC_API_BASE is set (e.g. "http://localhost:8765") all calls go
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
};

export type Api = typeof api;
