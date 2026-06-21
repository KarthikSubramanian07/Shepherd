"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight, CheckCircle2, Clock, Cpu, Play, ShieldAlert, Square } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { useShepherd } from "@/lib/shepherd-ws";
import type { RunSummary } from "@/lib/types";
import { runStatusStyle } from "@/lib/status";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card, Stat, StatusDot } from "@/components/ui/primitives";
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
        subtitle="Live agent execution — monitor, intervene, replay."
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
                hex={isHalted ? "#ef4444" : isRunning ? "#3b82f6" : "#64748b"}
                pulse={isRunning || isHalted}
              />
              <div>
                <div className="font-semibold text-ink">
                  {isHalted
                    ? "Halted — human required"
                    : isRunning
                      ? "Agent running"
                      : "Idle"}
                </div>
                <div className="mt-0.5 text-xs text-muted">
                  {state.routineId
                    ? `Routine: ${state.routineId}${state.stepIndex !== null ? ` · step ${state.stepIndex}` : ""}`
                    : "No active routine"}
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

          {/* Monitor alert */}
          {state.monitorAlert && (
            <div className="mt-4 rounded-xl border border-halt/40 bg-halt/10 p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-halt">
                <ShieldAlert size={16} />
                Monitor flagged a dangerous step
              </div>
              <p className="mt-1 text-sm text-muted">
                {state.monitorAlert.reason}
              </p>

              {/* Verifier second opinion */}
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
                  <span className="text-muted">
                    {Math.round(state.verifierResult.confidence * 100)}% conf —{" "}
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

          {/* Vision stream */}
          {state.visionOnline && state.visionDescription && (
            <div className="mt-3 flex items-center gap-2 text-xs text-muted">
              <Cpu size={11} className="shrink-0" />
              <span className="italic">Vision: {state.visionDescription}</span>
            </div>
          )}
        </Card>

        {/* Stats */}
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat
            label="Status"
            value={isHalted ? "HALTED" : isRunning ? "RUNNING" : "IDLE"}
            hint={state.routineId ?? "—"}
          />
          <Stat label="Mode" value={state.mode} hint="change in sidebar" />
          <Stat
            label="Step"
            value={state.stepIndex !== null ? `${state.stepIndex + 1}` : "—"}
            hint="current"
          />
          <Stat
            label="Runs"
            value={runs?.length ?? "—"}
            hint="this session"
          />
        </div>

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
            <div className="rounded-xl border border-edge bg-panel/40 px-6 py-8 text-center text-sm text-muted">
              No runs yet — start an agent from the terminal to see replays here.
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
