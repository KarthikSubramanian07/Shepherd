"use client";

import Link from "next/link";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { RunSummary } from "@/lib/types";
import { runStatusStyle } from "@/lib/status";
import { PageHeader } from "@/components/layout/PageHeader";
import { Card, StatusDot } from "@/components/ui/primitives";
import { pct, timeAgo } from "@/lib/utils";

export default function RunsPage() {
  const { data, loading } = useAsync<RunSummary[]>(() => api.listRuns(), []);
  const runs = data ?? [];

  return (
    <div>
      <PageHeader
        title="Runs"
        subtitle="Each agent's exact traversal of a routine — open one to replay it."
      />

      <div className="p-6">
        {loading ? (
          <div className="h-64 animate-pulse rounded-xl border border-edge bg-panel/50" />
        ) : (
          <Card className="divide-y divide-edge overflow-hidden">
            {runs.map((r) => {
              const s = runStatusStyle[r.status];
              return (
                <Link
                  key={r.id}
                  href={`/runs/${r.id}`}
                  className="flex items-center justify-between px-4 py-3 transition-colors hover:bg-panel2"
                >
                  <div className="flex items-center gap-3">
                    <StatusDot hex={s.hex} pulse={r.status === "running" || r.status === "blocked"} />
                    <div>
                      <div className="text-sm font-medium text-ink">{r.routineName}</div>
                      <div className="text-[11px] text-muted">
                        {r.agentName} · {r.id}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-6 text-[11px] text-muted">
                    <span className={s.text}>{s.label}</span>
                    <span>conf {pct(r.confidence)}</span>
                    <span>{timeAgo(r.startedAt)}</span>
                  </div>
                </Link>
              );
            })}
          </Card>
        )}
      </div>
    </div>
  );
}
