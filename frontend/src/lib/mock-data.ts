/**
 * In-memory mock dataset. Mirrors the backend's routines.json plus synthetic
 * agents / runs / interventions so every view has something to render.
 * This is the only file holding fake data — the API route handlers read from
 * here, so swapping to a real backend means deleting this and rewiring `api`.
 */
import {
  placeholderShot,
} from "./utils";
import type {
  Agent,
  Detection,
  Intervention,
  NodeStats,
  Routine,
  RoutineEdge,
  RoutineStep,
  Run,
} from "./types";

const now = Date.now();
const iso = (offsetMs: number) => new Date(now + offsetMs).toISOString();

function statsFor(seed: number): NodeStats {
  const execs = 40 + (seed % 30);
  const fail = seed % 5;
  const halt = seed % 3 === 0 ? seed % 4 : 0;
  return {
    executionCount: execs,
    successCount: execs - fail - halt,
    failureCount: fail,
    haltCount: halt,
    deviationCount: seed % 4,
    approvalCount: halt,
    avgDurationMs: 400 + ((seed * 137) % 2600),
  };
}

/** Linear edges connecting steps in order. */
function linearEdges(steps: RoutineStep[]): RoutineEdge[] {
  const edges: RoutineEdge[] = [];
  for (let i = 0; i < steps.length - 1; i++) {
    edges.push({
      id: `${steps[i].id}->${steps[i + 1].id}`,
      source: steps[i].id,
      target: steps[i + 1].id,
    });
  }
  return edges;
}

function step(
  routineId: string,
  index: number,
  partial: Partial<RoutineStep> & { action: RoutineStep["action"]; title: string },
): RoutineStep {
  const tone = partial.monitorTrigger
    ? partial.monitorTrigger === "stuck"
      ? "flag"
      : "halt"
    : "neutral";
  return {
    id: `${routineId}:s${index}`,
    index,
    screenshotUrl: placeholderShot(partial.title, tone),
    stats: statsFor(index + routineId.length),
    ...partial,
  };
}

// ── Routine 1: Form fill (with planted credential trigger) ──────────────────
const formSteps: RoutineStep[] = [
  step("form_fill", 0, { action: "open_app", title: "Open Safari", target: "Safari", instruction: "Open the demo form in Safari — keyboard-first navigation throughout" }),
  step("form_fill", 1, { action: "wait", title: "Wait for browser", seconds: 2 }),
  step("form_fill", 2, { action: "hotkey", title: "Focus URL bar", keys: ["cmd", "l"] }),
  step("form_fill", 3, { action: "type", title: "Navigate to form", text: "http://localhost:8765/demo-form" }),
  step("form_fill", 4, { action: "hotkey", title: "Focus name field", keys: ["tab"], instruction: "Tab through fields; type from variables injected at runtime" }),
  step("form_fill", 5, { action: "type", title: "Enter name", text: "{APPLICANT_NAME}" }),
  step("form_fill", 6, { action: "type", title: "Enter email", text: "{APPLICANT_EMAIL}" }),
  step("form_fill", 7, { action: "type", title: "Enter phone", text: "{APPLICANT_PHONE}" }),
  step("form_fill", 8, { action: "hotkey", title: "Tab to credential field", keys: ["tab"], highStakes: true, monitorTrigger: "credential", instruction: "PLANTED TRIGGER: credential field — monitor halts here every time" }),
  step("form_fill", 9, { action: "hotkey", title: "Submit form", keys: ["cmd", "return"] }),
];

const formFill: Routine = {
  id: "form_fill",
  name: "Job Application Form Fill",
  description: "Fill a job application form with applicant details. Authored by demonstration. Monitor halts at the planted credential step.",
  mode: "LIVE",
  tags: ["forms", "recorded", "high-stakes"],
  version: 7,
  stepCount: formSteps.length,
  updatedAt: iso(-1000 * 60 * 42),
  createdAt: iso(-1000 * 60 * 60 * 24 * 9),
  reliability: 0.86,
  activeAgents: 2,
  variables: ["APPLICANT_NAME", "APPLICANT_EMAIL", "APPLICANT_PHONE"],
  steps: formSteps,
  edges: linearEdges(formSteps),
};

// ── Routine 2: Browser showpiece ────────────────────────────────────────────
const browserSteps: RoutineStep[] = [
  step("browser_showpiece", 0, { action: "browser", title: "Open cloud browser", instruction: "Uses Browserbase — a real remote browser, not a local tab" }),
  step("browser_showpiece", 1, { action: "navigate", title: "Go to google.com", target: "https://www.google.com" }),
  step("browser_showpiece", 2, { action: "type", title: "Type query", text: "{SEARCH_QUERY}" }),
  step("browser_showpiece", 3, { action: "click", title: "Open first result", highStakes: false }),
  step("browser_showpiece", 4, { action: "wait", title: "Wait for page", seconds: 1.5 }),
];

const browserShowpiece: Routine = {
  id: "browser_showpiece",
  name: "Web Research (Browserbase)",
  description: "Open a Browserbase cloud browser session and run a live web action. Falls back to a local stub if offline.",
  mode: "LIVE",
  tags: ["browser", "research"],
  version: 3,
  stepCount: browserSteps.length,
  updatedAt: iso(-1000 * 60 * 60 * 5),
  createdAt: iso(-1000 * 60 * 60 * 24 * 4),
  reliability: 0.93,
  activeAgents: 1,
  variables: ["SEARCH_QUERY"],
  steps: browserSteps,
  edges: linearEdges(browserSteps),
};

// ── Routine 3: Locked fallback ──────────────────────────────────────────────
const lockedSteps: RoutineStep[] = [
  step("locked_fallback", 0, { action: "open_app", title: "Open TextEdit", target: "TextEdit" }),
  step("locked_fallback", 1, { action: "hotkey", title: "New document", keys: ["cmd", "n"] }),
  step("locked_fallback", 2, { action: "type", title: "Type note", text: "{NOTE_TEXT}" }),
  step("locked_fallback", 3, { action: "hotkey", title: "Save", keys: ["cmd", "s"] }),
];

const lockedFallback: Routine = {
  id: "locked_fallback",
  name: "Deterministic Fallback",
  description: "Deterministic offline fallback — opens TextEdit and types a note. Verbatim replay, no live agent.",
  mode: "LOCKED",
  tags: ["fallback", "deterministic"],
  version: 1,
  stepCount: lockedSteps.length,
  updatedAt: iso(-1000 * 60 * 60 * 24 * 2),
  createdAt: iso(-1000 * 60 * 60 * 24 * 12),
  reliability: 1,
  activeAgents: 0,
  variables: ["NOTE_TEXT"],
  steps: lockedSteps,
  edges: linearEdges(lockedSteps),
};

export const routines: Routine[] = [formFill, browserShowpiece, lockedFallback];

// ── Detections ──────────────────────────────────────────────────────────────
const credentialBlock: Detection = {
  type: "credential",
  verdict: "halt",
  reason: "Credential / password field detected — halting to protect sensitive data.",
  stepId: "form_fill:s8",
  detectedAt: iso(-1000 * 30),
  requiresHuman: true,
};

const deviationFlag: Detection = {
  type: "deviation",
  verdict: "flag",
  reason: "Layout differs from the recorded demonstration — first result is an ad.",
  stepId: "browser_showpiece:s3",
  detectedAt: iso(-1000 * 12),
  requiresHuman: true,
};

// ── Agents ───────────────────────────────────────────────────────────────────
export const agents: Agent[] = [
  {
    id: "agent_aurora",
    name: "Aurora",
    routineId: "form_fill",
    routineName: formFill.name,
    runId: "run_1001",
    status: "blocked",
    currentStepId: "form_fill:s8",
    currentStepIndex: 8,
    block: credentialBlock,
    progress: 0.8,
    host: "mac-mini-01 · Safari",
    startedAt: iso(-1000 * 60 * 2),
    lastActivityAt: iso(-1000 * 30),
  },
  {
    id: "agent_borealis",
    name: "Borealis",
    routineId: "browser_showpiece",
    routineName: browserShowpiece.name,
    runId: "run_1002",
    status: "blocked",
    currentStepId: "browser_showpiece:s3",
    currentStepIndex: 3,
    block: deviationFlag,
    progress: 0.6,
    host: "browserbase · us-east-1",
    startedAt: iso(-1000 * 60),
    lastActivityAt: iso(-1000 * 12),
  },
  {
    id: "agent_cirrus",
    name: "Cirrus",
    routineId: "form_fill",
    routineName: formFill.name,
    runId: "run_1003",
    status: "running",
    currentStepId: "form_fill:s5",
    currentStepIndex: 5,
    progress: 0.5,
    host: "mac-mini-02 · Safari",
    startedAt: iso(-1000 * 40),
    lastActivityAt: iso(-1000 * 3),
  },
  {
    id: "agent_dust",
    name: "Dust",
    routineId: "browser_showpiece",
    routineName: browserShowpiece.name,
    runId: "run_1004",
    status: "completed",
    currentStepId: "browser_showpiece:s4",
    currentStepIndex: 4,
    progress: 1,
    host: "browserbase · eu-west-1",
    startedAt: iso(-1000 * 60 * 6),
    lastActivityAt: iso(-1000 * 60 * 5),
  },
];

// ── Runs (per-step traversal traces) ─────────────────────────────────────────
export const runs: Run[] = [
  {
    id: "run_1001",
    routineId: "form_fill",
    routineName: formFill.name,
    agentId: "agent_aurora",
    agentName: "Aurora",
    status: "blocked",
    startedAt: iso(-1000 * 60 * 2),
    confidence: 0.91,
    variables: { APPLICANT_NAME: "Alex Johnson", APPLICANT_EMAIL: "alex@example.com", APPLICANT_PHONE: "555-0100" },
    steps: formSteps.map((s, i) => ({
      stepId: s.id,
      index: i,
      status: i < 8 ? "completed" : i === 8 ? "awaiting_human" : "pending",
      startedAt: i <= 8 ? iso(-1000 * (120 - i * 12)) : undefined,
      durationMs: i < 8 ? 500 + i * 120 : undefined,
      monitorVerdict: i === 8 ? "halt" : i < 8 ? "ok" : undefined,
      detection: i === 8 ? credentialBlock : undefined,
      screenshotUrl: s.screenshotUrl,
    })),
  },
  {
    id: "run_1004",
    routineId: "browser_showpiece",
    routineName: browserShowpiece.name,
    agentId: "agent_dust",
    agentName: "Dust",
    status: "completed",
    startedAt: iso(-1000 * 60 * 6),
    endedAt: iso(-1000 * 60 * 5),
    confidence: 0.97,
    variables: { SEARCH_QUERY: "AI agent safety" },
    steps: browserSteps.map((s, i) => ({
      stepId: s.id,
      index: i,
      status: i === 3 ? "deviated" : "completed",
      startedAt: iso(-1000 * (360 - i * 10)),
      durationMs: 600 + i * 200,
      monitorVerdict: i === 3 ? "flag" : "ok",
      deviation: i === 3 ? "Clicked the organic result after skipping a sponsored ad not present during recording." : undefined,
      screenshotUrl: s.screenshotUrl,
    })),
  },
];

// ── Interventions (human-in-the-loop queue) ──────────────────────────────────
export const interventions: Intervention[] = [
  {
    id: "int_001",
    runId: "run_1001",
    agentId: "agent_aurora",
    routineId: "form_fill",
    stepId: "form_fill:s8",
    detection: credentialBlock,
    status: "pending",
    createdAt: iso(-1000 * 30),
  },
  {
    id: "int_002",
    runId: "run_1002",
    agentId: "agent_borealis",
    routineId: "browser_showpiece",
    stepId: "browser_showpiece:s3",
    detection: deviationFlag,
    status: "pending",
    createdAt: iso(-1000 * 12),
  },
];
