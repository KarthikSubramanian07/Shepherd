"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, Play, ShieldAlert, Square } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { useShepherd } from "@/lib/shepherd-ws";
import type { RunSummary } from "@/lib/types";
import { runStatusStyle } from "@/lib/status";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card, Stat, StatusDot } from "@/components/ui/primitives";
import { LiveExecutionGraph } from "@/components/LiveExecutionGraph";
import { RedisPanel } from "@/components/RedisPanel";
import { formatDuration, timeAgo } from "@/lib/utils";

export default function CommandCenterPage() {
  const { state, connected } = useShepherd();
  const { data: runs, loading: runsLoading } = useAsync<RunSummary[]>(
    () => api.listRuns(),
    [],
  );

  const isRunning = state.status === "running";
  const isHalted = state.status === "halted";

  return (
    <div>
      <PageHeader
        title="Command Center"
        subtitle="Live agent execution · monitor, intervene, replay."
        actions={
          <span
            className={`flex items-center gap-1.5 text-xs ${connected ? "text-ok" : "text-muted"}`}
          >
            <span
              className={`h-1.5 w-1.5 rounded-full ${connected ? "bg-ok" : "bg-muted"}`}
            />
            {connected ? "backend connected" : "connecting…"}
          </span>
        }
      />

      <div className="space-y-6 p-6">
        {/* Live execution card */}
        <Card className={`p-5 ${isHalted ? "border-halt/50 bg-halt/5" : isRunning ? "border-accent/40" : ""}`}>
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-3">
              <StatusDot
                hex={isHalted ? "#c0463c" : isRunning ? "#cf6a43" : "#9a8b7a"}
                pulse={isRunning || isHalted}
              />
              <div>
                <div className="font-semibold text-ink">
                  {isHalted
                    ? "Halted · the agent is waiting on you"
                    : isRunning
                      ? "Agent at work · watching every step"
                      : "On watch"}
                </div>
                <div className="mt-0.5 text-xs text-muted">
                  {state.routineId
                    ? `Routine: ${state.routineId}${state.stepIndex !== null ? ` · step ${state.stepIndex}` : ""}`
                    : "Nothing running. Speak or type an intent to send the agent off."}
                  {state.runId && (
                    <span className="ml-2 font-mono text-[10px] text-muted/70">
                      {state.runId}
                    </span>
                  )}
                </div>
              </div>
            </div>
            <Badge
              tone={isHalted ? "halt" : isRunning ? "accent" : "neutral"}
            >
              {state.mode}
            </Badge>
          </div>

          {/* Run a goal · sends the local agent off via POST /api/intent */}
          <RunGoalForm disabled={isRunning} />

          {/* Monitor alert · the signature moment: the lantern catches the danger */}
          {state.monitorAlert && (
            <div className="mt-4 animate-riseIn rounded-xl border border-halt/40 bg-halt/[0.06] p-4 shadow-halt">
              <div className="flex items-center gap-2 text-sm font-semibold text-halt">
                <ShieldAlert size={16} className="animate-pulseRing" />
                Caught it · a step needs you before the agent goes on
              </div>
              <p className="mt-1 text-sm text-muted">
                {state.monitorAlert.reason}
              </p>

              {/* Verifier second opinion · names its source (Band peer vs local) */}
              {state.verifierResult && (
                <div className="mt-3 rounded-lg border border-edge bg-panel2/60 p-3 text-xs">
                  <span className="font-semibold text-ink">AI verifier: </span>
                  <Badge
                    tone={
                      state.verifierResult.verdict === "halt"
                        ? "halt"
                        : state.verifierResult.verdict === "ok"
                          ? "ok"
                          : "flag"
                    }
                    className="mr-1"
                  >
                    {state.verifierResult.verdict}
                  </Badge>
                  {state.verifierResult.model.startsWith("band:") && (
                    <span
                      className="mr-1 rounded px-1.5 py-0.5 text-[10px] font-semibold text-accent-ink"
                      style={{ background: "var(--accent-soft, #cf6a4322)" }}
                      title="Second opinion came from an independent shepherd-verifier agent over Band's mesh"
                    >
                      via Band peer
                    </span>
                  )}
                  <span className="text-muted">
                    {Math.round(state.verifierResult.confidence * 100)}% conf ·{" "}
                    {state.verifierResult.explanation}
                  </span>
                </div>
              )}

              <div className="mt-3 flex gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => api.approveStep()}
                >
                  <CheckCircle2 size={13} /> Approve
                </Button>
                <Button
                  size="sm"
                  variant="danger"
                  onClick={() => api.haltExecution()}
                >
                  <Square size={12} /> Halt
                </Button>
              </div>
            </div>
          )}

          {/* ArmorIQ pre-flight intent authorization · cryptographic gate before step 1 */}
          {state.armoriqGate && (
            <div
              className={`mt-4 flex items-start gap-2 rounded-xl border p-3 text-xs ${
                state.armoriqGate.authorized
                  ? "border-ok/40 bg-ok/[0.06]"
                  : "border-halt/40 bg-halt/[0.06]"
              }`}
            >
              {state.armoriqGate.authorized ? (
                <CheckCircle2 size={14} className="mt-0.5 shrink-0 text-ok" />
              ) : (
                <ShieldAlert size={14} className="mt-0.5 shrink-0 text-halt" />
              )}
              <div>
                <span className="font-semibold text-ink">
                  {state.armoriqGate.authorized
                    ? "Intent authorized by ArmorIQ"
                    : "ArmorIQ blocked this plan"}
                </span>
                <span className="ml-1.5 text-muted">
                  {state.armoriqGate.authorized
                    ? "signed intent token issued before the first action"
                    : state.armoriqGate.reason}
                </span>
              </div>
            </div>
          )}

          {/* Live execution path · replays milestone-by-milestone as the run streams */}
          {state.graphNodes.length > 0 && (
            <div className="mt-4 rounded-xl border border-edge bg-canvas/50 p-4">
              <div className="mb-3 flex items-center justify-between">
                <span className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
                  {isRunning && (
                    <span className="h-1.5 w-1.5 animate-pulseRing rounded-full bg-accent" />
                  )}
                  Live execution path
                </span>
                {isRunning && (
                  <Button size="sm" variant="danger" onClick={() => api.haltExecution()}>
                    <Square size={12} /> Stop agent
                  </Button>
                )}
              </div>
              <LiveExecutionGraph nodes={state.graphNodes} />
            </div>
          )}

        </Card>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat
            label="Status"
            value={isHalted ? "HALTED" : isRunning ? "RUNNING" : "IDLE"}
            hint={state.routineId ?? "·"}
          />
          <Stat label="Mode" value={state.mode} hint="change in sidebar" />
          <Stat
            label="Step"
            value={state.stepIndex !== null ? `${state.stepIndex + 1}` : "·"}
            hint="current"
          />
          <Stat
            label="Runs"
            value={runs?.length ?? "·"}
            hint="this session"
          />
        </div>

        {/* Redis as the substrate · vector routing, agent memory, semantic cache */}
        <RedisPanel />

        {/* Past runs */}
        <div>
          <h2 className="mb-3 text-sm font-semibold text-muted">
            Recent runs
          </h2>
          {runsLoading ? (
            <div className="space-y-2">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-14 animate-pulse rounded-xl border border-edge bg-panel/50"
                />
              ))}
            </div>
          ) : !runs || runs.length === 0 ? (
            <div className="rounded-xl border border-dashed border-edge bg-panel/40 px-6 py-10 text-center">
              <div className="text-sm font-medium text-ink">No runs to replay yet</div>
              <p className="mx-auto mt-1 max-w-xs text-xs text-muted">
                Once the agent runs a task, every step it took shows up here · fully
                scrubbable, so you can see exactly what happened while you were away.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {runs.map((r) => (
                <RunRow key={r.id} run={r} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function RunGoalForm({ disabled }: { disabled: boolean }) {
  const [goal, setGoal] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [sending, setSending] = useState(false);

  const send = async () => {
    const text = goal.trim();
    if (!text || sending) return;
    setSending(true);
    setMsg(null);
    const res = await api.runGoal(text).catch(() => ({ error: "backend unreachable" }));
    setSending(false);
    if (res.error) {
      setMsg(res.error);
    } else {
      setMsg(`Sent: "${text}"`);
      setGoal("");
    }
  };

  return (
    <div className="mt-4 rounded-xl border border-edge bg-canvas/50 p-3">
      <div className="flex items-center gap-2">
        <input
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder='Run a goal… (ex. "draft an email")'
          className="flex-1 rounded-lg border border-edge bg-panel px-3 py-2 text-sm text-ink outline-none placeholder:text-muted focus:border-accent/50"
        />
        <Button onClick={send} disabled={!goal.trim() || sending}>
          <Play size={14} className="mr-1" />
          {disabled ? "Run (queued)" : "Run"}
        </Button>
      </div>
      {msg && <div className="mt-2 text-xs text-muted">{msg}</div>}
    </div>
  );
}

function RunRow({ run }: { run: RunSummary }) {
  const s = runStatusStyle[run.status] ?? runStatusStyle.completed;
  return (
    <Link href={`/runs/${run.id}`}>
      <div className="flex items-center justify-between rounded-xl border border-edge bg-panel/80 px-4 py-3 transition-colors hover:border-accent/40">
        <div className="flex items-center gap-3">
          <StatusDot hex={s.hex} />
          <div>
            <span className="text-sm font-medium text-ink">
              {run.routineName}
            </span>
            <span className="ml-2 font-mono text-[10px] text-muted">
              {run.id}
            </span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <Badge
            tone={
              run.status === "completed"
                ? "ok"
                : run.status === "aborted" || run.status === "failed"
                  ? "halt"
                  : "accent"
            }
          >
            {s.label}
          </Badge>
          <span className="flex items-center gap-1 text-xs text-muted">
            <Clock size={11} />
            {timeAgo(run.startedAt)}
          </span>
          <ArrowRight size={14} className="text-muted" />
        </div>
      </div>
    </Link>
  );
}
