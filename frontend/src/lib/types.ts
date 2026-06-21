/**
 * Domain contract for the Shepherd dashboard.
 *
 * This is the SINGLE source of truth the UI codes against. It is intentionally
 * decoupled from the (currently in-flux) Python backend. When the real API
 * solidifies, only `lib/api.ts` and the `/api/*` route handlers need to change
 * to map backend payloads onto these shapes — components stay untouched.
 *
 * Concept map (product → types):
 *   "A tool you recorded"            → Routine (a task graph)
 *   "A discrete step w/ screenshot"  → RoutineStep (a graph node)
 *   "An agent doing the task"        → Agent (+ its current Run)
 *   "This run's exact traversal"     → Run + StepTrace[] (replay)
 *   "Proactive trust/safety layer"   → Detection (verdict per step)
 *   "Human takes over remotely"      → Intervention
 *   "Routine learns over time"       → NodeStats (per-node aggregates)
 */

// ── Primitives ────────────────────────────────────────────────────────────

export type ActionType =
  | "move"
  | "click"
  | "double_click"
  | "type"
  | "hotkey"
  | "open_app"
  | "wait"
  | "browser"
  | "scroll"
  | "navigate"
  | "batch_fill";

/** Failure modes the proactive detection layer watches for. */
export type MonitorTrigger = "credential" | "captcha" | "phishing" | "stuck";

/** Monitor outcome at a step boundary. */
export type Verdict = "ok" | "flag" | "halt";

export type RoutineMode = "LIVE" | "LOCKED";

// ── Routine (the recorded "tool", as a task graph) ──────────────────────────

export interface NodeStats {
  executionCount: number;
  successCount: number;
  failureCount: number;
  haltCount: number;
  deviationCount: number;
  approvalCount: number;
  avgDurationMs: number;
}

export interface RoutineStep {
  id: string;
  /** Ordinal position; also used for default linear layout. */
  index: number;
  action: ActionType;
  /** Short node label, e.g. "Enter email". */
  title: string;
  /** Custom human instruction attached to this step (authored or learned). */
  instruction?: string;
  target?: string;
  text?: string;
  keys?: string[];
  seconds?: number;
  /** Screenshot captured at this step boundary during recording. */
  screenshotUrl?: string;
  /** Flagged as needing extra scrutiny by the monitor. */
  highStakes?: boolean;
  /** A planted/known failure mode the detection layer fires on here. */
  monitorTrigger?: MonitorTrigger;
  /** Manual graph position; if absent the UI auto-lays-out vertically. */
  position?: { x: number; y: number };
  /** Aggregate learning signal across all past runs of this node. */
  stats?: NodeStats;
}

export interface RoutineEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
  /** Optional branch condition, e.g. "if captcha solved". */
  condition?: string;
}

export interface RoutineSummary {
  id: string;
  name: string;
  description: string;
  mode: RoutineMode;
  tags: string[];
  version: number;
  stepCount: number;
  updatedAt: string;
  /** Rolling success rate across runs, 0..1. */
  reliability: number;
  activeAgents: number;
}

export interface Routine extends RoutineSummary {
  variables: string[];
  steps: RoutineStep[];
  edges: RoutineEdge[];
  createdAt: string;
}

// ── Detection (proactive trust/safety layer) ────────────────────────────────

export interface Detection {
  type: MonitorTrigger | "deviation" | "error";
  verdict: Verdict;
  reason: string;
  stepId: string;
  detectedAt: string;
  /** True when a human should resolve this before the agent proceeds. */
  requiresHuman: boolean;
}

// ── Agents (live instances traversing a routine) ────────────────────────────

export type AgentStatus =
  | "idle"
  | "running"
  | "blocked"
  | "completed"
  | "failed";

export interface Agent {
  id: string;
  name: string;
  routineId: string;
  routineName: string;
  runId?: string;
  status: AgentStatus;
  /** The node the agent currently sits on. */
  currentStepId?: string;
  currentStepIndex?: number;
  /** Populated when status === "blocked". */
  block?: Detection;
  /** 0..1 traversal progress. */
  progress: number;
  /** Where the agent runs (machine, browser session, region). */
  host: string;
  startedAt?: string;
  lastActivityAt?: string;
}

// ── Runs (a specific agent's traversal = a replay) ──────────────────────────

export type StepStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "halted"
  | "flagged"
  | "deviated"
  | "awaiting_human";

export interface StepTrace {
  stepId: string;
  index: number;
  status: StepStatus;
  startedAt?: string;
  durationMs?: number;
  error?: string;
  monitorVerdict?: Verdict;
  detection?: Detection;
  /** Description of how the agent diverged from the recorded demonstration. */
  deviation?: string;
  /** Screenshot the agent actually saw at runtime (vs the recorded one). */
  screenshotUrl?: string;
}

export type RunStatus =
  | "running"
  | "completed"
  | "failed"
  | "aborted"
  | "blocked";

export interface RunSummary {
  id: string;
  routineId: string;
  routineName: string;
  agentId: string;
  agentName: string;
  status: RunStatus;
  startedAt: string;
  endedAt?: string;
  confidence: number;
}

export interface Run extends RunSummary {
  variables: Record<string, string>;
  steps: StepTrace[];
}

// ── Interventions (human-in-the-loop resolution) ────────────────────────────

export type InterventionStatus = "pending" | "resolved" | "dismissed";
export type InterventionResolution =
  | "approved"
  | "rejected"
  | "provided_input";

export interface Intervention {
  id: string;
  runId: string;
  agentId: string;
  routineId: string;
  stepId: string;
  detection: Detection;
  status: InterventionStatus;
  resolution?: InterventionResolution;
  resolvedBy?: string;
  resolvedAt?: string;
  note?: string;
  createdAt: string;
}

// ── Task Graph (crystallized milestone workflow from the real backend) ──────
// Mirrors engine/task_graph.py `_serialize` (snake_case, as the backend emits).

export type MilestoneKind =
  | "open"
  | "navigate"
  | "search"
  | "research"
  | "scan"
  | "fill"
  | "submit"
  | "verify"
  | "interact";

export interface Conditional {
  when: string;
  do: string;
  goto: string | null;
  source: string;
}

export interface TaskGraphNode {
  key: string;
  kind: MilestoneKind | string;
  label: string;
  value: string | null;
  times_seen: number;
  last_status: string | null;
  fine_steps: number;
  first_run_id: string;
  last_run_id: string;
  // taught / workflow layer
  instruction?: string;
  requires?: string[];
  conditionals?: Conditional[];
  procedure?: string | null;
  optional?: boolean;
  source?: "observed" | "taught" | string;
}

export interface TaskGraphEdge {
  from: string;
  to: string;
  times_seen: number;
  last_run_id: string;
  condition?: string | null;
}

export interface TaskGraph {
  task_key: string;
  routine_id: string;
  run_count: number;
  intents: string[];
  variables: Record<string, string>;
  created_at: number;
  updated_at: number;
  last_run_id: string;
  nodes: TaskGraphNode[];
  edges: TaskGraphEdge[];
}
