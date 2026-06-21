/** Centralized visual semantics for statuses/verdicts shared across views. */
import type { AgentStatus, RunStatus, StepStatus, Verdict } from "./types";

export interface StatusStyle {
  label: string;
  /** Tailwind text color class. */
  text: string;
  /** Tailwind bg color class (subtle). */
  bg: string;
  /** Raw hex for non-Tailwind contexts (ex. React Flow rings). */
  hex: string;
}

export const agentStatusStyle: Record<AgentStatus, StatusStyle> = {
  idle: { label: "Idle", text: "text-idle", bg: "bg-idle/10", hex: "#64748b" },
  running: { label: "Running", text: "text-running", bg: "bg-running/10", hex: "#3b82f6" },
  blocked: { label: "Blocked", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
  completed: { label: "Completed", text: "text-ok", bg: "bg-ok/10", hex: "#22c55e" },
  failed: { label: "Failed", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
  suspended: { label: "Suspended", text: "text-flag", bg: "bg-flag/10", hex: "#f59e0b" },
};

export const runStatusStyle: Record<RunStatus, StatusStyle> = {
  running: { label: "Running", text: "text-running", bg: "bg-running/10", hex: "#3b82f6" },
  blocked: { label: "Blocked", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
  completed: { label: "Completed", text: "text-ok", bg: "bg-ok/10", hex: "#22c55e" },
  failed: { label: "Failed", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
  aborted: { label: "Aborted", text: "text-muted", bg: "bg-idle/10", hex: "#64748b" },
};

export const stepStatusStyle: Record<StepStatus, StatusStyle> = {
  pending: { label: "Pending", text: "text-muted", bg: "bg-idle/10", hex: "#64748b" },
  running: { label: "Running", text: "text-running", bg: "bg-running/10", hex: "#3b82f6" },
  completed: { label: "Completed", text: "text-ok", bg: "bg-ok/10", hex: "#22c55e" },
  failed: { label: "Failed", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
  halted: { label: "Halted", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
  flagged: { label: "Flagged", text: "text-flag", bg: "bg-flag/10", hex: "#f59e0b" },
  deviated: { label: "Deviated", text: "text-flag", bg: "bg-flag/10", hex: "#f59e0b" },
  awaiting_human: { label: "Awaiting human", text: "text-flag", bg: "bg-flag/10", hex: "#f59e0b" },
};

export const verdictStyle: Record<Verdict, StatusStyle> = {
  ok: { label: "OK", text: "text-ok", bg: "bg-ok/10", hex: "#22c55e" },
  flag: { label: "Flag", text: "text-flag", bg: "bg-flag/10", hex: "#f59e0b" },
  halt: { label: "Halt", text: "text-halt", bg: "bg-halt/10", hex: "#ef4444" },
};
