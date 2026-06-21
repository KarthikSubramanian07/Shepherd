"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import {
  Play,
  Pause,
  SkipBack,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { Routine, Run, StepStatus } from "@/lib/types";
import { runStatusStyle, stepStatusStyle } from "@/lib/status";
import { PageHeader } from "@/components/layout/PageHeader";
import { RoutineGraph } from "@/components/graph/RoutineGraph";
import { Badge, Button } from "@/components/ui/primitives";
import { cn, formatDuration, pct } from "@/lib/utils";

const SPEEDS = [0.25, 0.5, 1, 2, 5] as const;
type Speed = (typeof SPEEDS)[number];

export default function RunReplayPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data, loading } = useAsync<{ run: Run; routine: Routine }>(async () => {
    const run = await api.getRun(id);
    const routine = await api.getRoutine(run.routineId);
    return { run, routine };
  }, [id]);

  // ── Playback state ────────────────────────────────────────────────────────
  const totalSteps = data?.run.steps.length ?? 0;
  // scrubIndex: 0 = before any steps, i = i steps have been completed
  const [scrubIndex, setScrubIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<Speed>(1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reset scrub when data loads
  useEffect(() => {
    if (data) setScrubIndex(0);
  }, [data]);

  // Auto-advance timer — uses actual step durationMs for realistic replay
  useEffect(() => {
    if (!playing || !data) return;
    if (scrubIndex >= totalSteps) {
      setPlaying(false);
      return;
    }
    const stepMs = data.run.steps[scrubIndex]?.durationMs ?? 800;
    const delay = Math.max(120, stepMs / speed);
    timerRef.current = setTimeout(() => setScrubIndex((i) => i + 1), delay);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [playing, scrubIndex, data, speed, totalSteps]);

  function togglePlay() {
    if (scrubIndex >= totalSteps) {
      setScrubIndex(0);
      setPlaying(true);
    } else {
      setPlaying((p) => !p);
    }
  }

  function reset() {
    setPlaying(false);
    setScrubIndex(0);
  }

  function stepBack() {
    setPlaying(false);
    setScrubIndex((i) => Math.max(0, i - 1));
  }

  function stepForward() {
    setPlaying(false);
    setScrubIndex((i) => Math.min(totalSteps, i + 1));
  }

  // ── Scrubbed run — slice statuses up to scrubIndex ─────────────────────────
  const scrubRun = useMemo<Run | undefined>(() => {
    if (!data?.run) return undefined;
    return {
      ...data.run,
      steps: data.run.steps.map((s, i) => {
        if (i < scrubIndex) return s; // actual recorded status
        if (i === scrubIndex && scrubIndex < totalSteps)
          return { ...s, status: "running" as StepStatus };
        return { ...s, status: "pending" as StepStatus };
      }),
    };
  }, [data, scrubIndex, totalSteps]);

  // ── Elapsed time at current scrub position ─────────────────────────────────
  const elapsedMs = useMemo(() => {
    if (!data?.run) return 0;
    return data.run.steps
      .slice(0, scrubIndex)
      .reduce((sum, s) => sum + (s.durationMs ?? 0), 0);
  }, [data, scrubIndex]);

  // ── Current step detail ────────────────────────────────────────────────────
  const currentTrace = data?.run.steps[Math.min(scrubIndex, totalSteps - 1)];
  const titleByStep = useMemo(() => {
    const m = new Map<string, string>();
    data?.routine.steps.forEach((s) => m.set(s.id, s.title));
    return m;
  }, [data]);

  if (loading || !data) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        Loading replay…
      </div>
    );
  }

  const { run, routine } = data;
  const s = runStatusStyle[run.status];
  const isDone = scrubIndex >= totalSteps;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={`${run.routineName} · replay`}
        subtitle={`${run.agentName} · ${run.id}`}
        actions={
          <>
            <Badge tone={run.status === "completed" ? "ok" : run.status === "blocked" ? "halt" : "accent"}>
              {s.label}
            </Badge>
            <Badge tone="neutral">conf {pct(run.confidence)}</Badge>
          </>
        }
      />

      <div className="flex min-h-0 flex-1 overflow-hidden">
        {/* ── Graph + controls ── */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Graph */}
          <div className="min-h-0 flex-1">
            <RoutineGraph routine={routine} run={scrubRun} />
          </div>

          {/* Playback controls bar */}
          <div className="border-t border-edge bg-panel/80 px-4 py-3">
            <div className="flex items-center gap-3">
              {/* Transport buttons */}
              <button
                onClick={reset}
                className="rounded p-1 text-muted transition-colors hover:bg-panel2 hover:text-ink"
                title="Reset"
              >
                <SkipBack size={15} />
              </button>
              <button
                onClick={stepBack}
                disabled={scrubIndex === 0}
                className="rounded p-1 text-muted transition-colors hover:bg-panel2 hover:text-ink disabled:opacity-30"
                title="Step back"
              >
                <ChevronLeft size={15} />
              </button>
              <button
                onClick={togglePlay}
                className={cn(
                  "flex h-7 w-7 items-center justify-center rounded-full transition-colors",
                  isDone
                    ? "bg-muted/20 text-muted hover:bg-panel2"
                    : "bg-accent text-white hover:bg-accent/90",
                )}
                title={playing ? "Pause" : "Play"}
              >
                {playing ? <Pause size={14} /> : <Play size={14} />}
              </button>
              <button
                onClick={stepForward}
                disabled={isDone}
                className="rounded p-1 text-muted transition-colors hover:bg-panel2 hover:text-ink disabled:opacity-30"
                title="Step forward"
              >
                <ChevronRight size={15} />
              </button>

              {/* Step counter */}
              <span className="min-w-[64px] font-mono text-[11px] text-muted">
                {scrubIndex} / {totalSteps}
              </span>

              {/* Scrubber */}
              <input
                type="range"
                min={0}
                max={totalSteps}
                value={scrubIndex}
                onChange={(e) => {
                  setPlaying(false);
                  setScrubIndex(Number(e.target.value));
                }}
                className="h-1.5 flex-1 cursor-pointer accent-accent"
              />

              {/* Elapsed */}
              <span className="min-w-[52px] text-right font-mono text-[11px] text-muted">
                {formatDuration(elapsedMs)}
              </span>

              {/* Speed */}
              <div className="flex gap-0.5">
                {SPEEDS.map((x) => (
                  <button
                    key={x}
                    onClick={() => setSpeed(x)}
                    className={cn(
                      "rounded px-1.5 py-0.5 text-[10px] font-medium transition-colors",
                      speed === x
                        ? "bg-accent/20 text-accent"
                        : "text-muted hover:bg-panel2 hover:text-ink",
                    )}
                  >
                    {x}×
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── Step traversal sidebar ── */}
        <aside className="w-80 shrink-0 overflow-auto border-l border-edge bg-panel/40 p-4">
          <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
            Traversal
          </h3>
          <ol className="relative space-y-1.5 border-l border-edge pl-4">
            {run.steps.map((t, i) => {
              const isFuture = i > scrubIndex;
              const isCurrent = i === scrubIndex && !isDone;
              const st = stepStatusStyle[isFuture ? "pending" : t.status];
              return (
                <li
                  key={t.stepId}
                  className={cn(
                    "relative cursor-pointer",
                    isFuture && "opacity-40",
                  )}
                  onClick={() => {
                    setPlaying(false);
                    setScrubIndex(i);
                  }}
                >
                  <span
                    className={cn(
                      "absolute -left-[21px] top-2 h-2.5 w-2.5 rounded-full border-2 border-canvas transition-all",
                      isCurrent && "scale-125",
                    )}
                    style={{ backgroundColor: st.hex }}
                  />
                  <div
                    className={cn(
                      "rounded-lg border px-3 py-2 transition-colors",
                      isCurrent
                        ? "border-accent/40 bg-accent/5"
                        : "border-edge bg-panel",
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-ink">
                        {t.index + 1}. {titleByStep.get(t.stepId) ?? t.stepId}
                      </span>
                      <span className="text-[10px]" style={{ color: st.hex }}>
                        {isFuture ? "—" : st.label}
                      </span>
                    </div>
                    {!isFuture && (
                      <div className="mt-0.5 flex items-center gap-3 text-[10px] text-muted">
                        <span>{formatDuration(t.durationMs ?? 0)}</span>
                        {t.monitorVerdict && (
                          <span>monitor: {t.monitorVerdict}</span>
                        )}
                      </div>
                    )}
                    {!isFuture && t.deviation && (
                      <div className="mt-1.5 rounded border border-flag/30 bg-flag/10 px-2 py-1 text-[10px] text-flag">
                        Deviation: {t.deviation}
                      </div>
                    )}
                    {!isFuture && t.detection && (
                      <div className="mt-1.5 rounded border border-halt/30 bg-halt/10 px-2 py-1 text-[10px] text-halt">
                        {t.detection.reason}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>

          {/* Run metadata */}
          <div className="mt-6 space-y-1.5 border-t border-edge pt-4">
            <div className="flex justify-between text-[11px]">
              <span className="text-muted">Total duration</span>
              <span className="font-mono text-ink">
                {formatDuration(
                  run.steps.reduce((s, t) => s + (t.durationMs ?? 0), 0),
                )}
              </span>
            </div>
            <div className="flex justify-between text-[11px]">
              <span className="text-muted">Steps</span>
              <span className="font-mono text-ink">{totalSteps}</span>
            </div>
            {Object.entries(run.variables ?? {}).map(([k, v]) => (
              <div key={k} className="flex justify-between text-[11px]">
                <span className="text-muted">{k}</span>
                <span className="max-w-[120px] truncate font-mono text-ink">
                  {v}
                </span>
              </div>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}
