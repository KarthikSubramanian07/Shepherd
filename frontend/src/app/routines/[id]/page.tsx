"use client";

import { type ReactNode } from "react";
import { useParams } from "next/navigation";
import { Play, Variable } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { Agent, Routine } from "@/lib/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { RoutineGraph } from "@/components/graph/RoutineGraph";
import { Badge, Button } from "@/components/ui/primitives";
import { pct } from "@/lib/utils";

export default function RoutineDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data: routine, loading } = useAsync<Routine>(
    () => api.getRoutine(id),
    [id],
  );
  const { data: agents } = useAsync<Agent[]>(() => api.listAgents(), []);

  if (loading || !routine) {
    return (
      <div className="flex h-full items-center justify-center text-muted">
        Loading routine…
      </div>
    );
  }

  const onRoutine = (agents ?? []).filter((a) => a.routineId === routine.id);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={routine.name}
        subtitle={routine.description}
        actions={
          <>
            <Badge tone={routine.mode === "LOCKED" ? "neutral" : "accent"}>
              {routine.mode}
            </Badge>
            <Button>
              <Play size={15} /> Run
            </Button>
          </>
        }
      />

      <div className="flex flex-1 overflow-hidden">
        <div className="flex-1">
          <RoutineGraph routine={routine} agents={onRoutine} />
        </div>

        <aside className="w-80 shrink-0 overflow-auto border-l border-edge bg-panel/40 p-4">
          <Section title="Overview">
            <Row label="Version" value={`v${routine.version}`} />
            <Row label="Steps" value={routine.stepCount} />
            <Row
              label="Reliability"
              value={pct(routine.reliability)}
              valueClass={routine.reliability >= 0.9 ? "text-ok" : "text-flag"}
            />
            <Row label="Active agents" value={onRoutine.length} />
          </Section>

          <Section title="Variables">
            {routine.variables.length === 0 ? (
              <p className="text-xs text-muted">No variables.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {routine.variables.map((v) => (
                  <Badge key={v} tone="neutral">
                    <Variable size={11} /> {v}
                  </Badge>
                ))}
              </div>
            )}
          </Section>

          <Section title="Steps">
            <ol className="space-y-1.5">
              {routine.steps.map((s) => (
                <li
                  key={s.id}
                  className="flex items-start gap-2 rounded-lg border border-edge bg-panel px-2.5 py-1.5 text-xs"
                >
                  <span className="mt-0.5 font-mono text-[10px] text-muted">
                    {s.index}
                  </span>
                  <div className="min-w-0">
                    <div className="truncate text-ink">{s.title}</div>
                    {s.monitorTrigger && (
                      <span className="text-[10px] text-halt">
                        monitor: {s.monitorTrigger}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          </Section>
        </aside>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-5">
      <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-muted">
        {title}
      </h3>
      {children}
    </div>
  );
}

function Row({
  label,
  value,
  valueClass,
}: {
  label: string;
  value: ReactNode;
  valueClass?: string;
}) {
  return (
    <div className="flex items-center justify-between py-1 text-sm">
      <span className="text-muted">{label}</span>
      <span className={valueClass ?? "text-ink"}>{value}</span>
    </div>
  );
}
