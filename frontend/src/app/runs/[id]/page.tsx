"use client";

import { useMemo } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { Routine, Run } from "@/lib/types";
import { runStatusStyle, stepStatusStyle } from "@/lib/status";
import { PageHeader } from "@/components/layout/PageHeader";
import { RoutineGraph } from "@/components/graph/RoutineGraph";
import { Badge } from "@/components/ui/primitives";
import { formatDuration, pct } from "@/lib/utils";

export default function RunReplayPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data, loading } = useAsync<{ run: Run; routine: Routine }>(async () => {
    const run = await api.getRun(id);
    const routine = await api.getRoutine(run.routineId);
    return { run, routine };
  }, [id]);

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

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1">
          <RoutineGraph routine={routine} run={run} />
        </div>

        <aside className="w-96 shrink-0 overflow-auto border-l border-edge bg-panel/40 p-4">
          <h3 className="mb-3 text-[11px] font-semibold uppercase tracking-wide text-muted">
            Traversal
          </h3>
          <ol className="relative space-y-2 border-l border-edge pl-4">
            {run.steps.map((t) => {
              const st = stepStatusStyle[t.status];
              return (
                <li key={t.stepId} className="relative">
                  <span
                    className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-canvas"
                    style={{ backgroundColor: st.hex }}
                  />
                  <div className="rounded-lg border border-edge bg-panel px-3 py-2">
                    <div className="flex items-center justify-between">
                      <span className="text-sm text-ink">
                        {t.index}. {titleByStep.get(t.stepId) ?? t.stepId}
                      </span>
                      <span className="text-[10px]" style={{ color: st.hex }}>
                        {st.label}
                      </span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-3 text-[10px] text-muted">
                      <span>{formatDuration(t.durationMs)}</span>
                      {t.monitorVerdict && <span>monitor: {t.monitorVerdict}</span>}
                    </div>
                    {t.deviation && (
                      <div className="mt-1.5 rounded border border-flag/30 bg-flag/10 px-2 py-1 text-[10px] text-flag">
                        Deviation: {t.deviation}
                      </div>
                    )}
                    {t.detection && (
                      <div className="mt-1.5 rounded border border-halt/30 bg-halt/10 px-2 py-1 text-[10px] text-halt">
                        {t.detection.reason}
                      </div>
                    )}
                  </div>
                </li>
              );
            })}
          </ol>
        </aside>
      </div>
    </div>
  );
}
