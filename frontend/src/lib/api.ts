/**
 * Typed client for the dashboard API.
 *
 * Today these hit the local Next.js route handlers in `app/api/*`, which serve
 * mock data. To switch to the real backend later, set NEXT_PUBLIC_API_BASE
 * (e.g. "http://localhost:8000") — the route shapes are designed to match these
 * function signatures, so component code never changes.
 */
import type {
  Agent,
  Intervention,
  InterventionResolution,
  Routine,
  RoutineSummary,
  Run,
  RunSummary,
} from "./types";

const BASE = process.env.NEXT_PUBLIC_API_BASE ?? "";

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
  resolveIntervention: (id: string, resolution: InterventionResolution, note?: string) =>
    http<Intervention>(`/interventions/${id}`, {
      method: "POST",
      body: JSON.stringify({ resolution, note }),
    }),
};

export type Api = typeof api;
