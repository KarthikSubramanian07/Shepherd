"use client";

import Link from "next/link";
import { GitBranch, Lock, Users } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { RoutineSummary } from "@/lib/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card } from "@/components/ui/primitives";
import { pct, timeAgo } from "@/lib/utils";

export default function RoutinesPage() {
  const { data, loading } = useAsync<RoutineSummary[]>(() => api.listRoutines(), []);
  const routines = data ?? [];

  return (
    <div>
      <PageHeader
        title="Routines"
        subtitle="Recorded tasks, saved as reusable tools. Each is a task graph."
        actions={<Button>New routine</Button>}
      />

      <div className="p-6">
        {loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-40 animate-pulse rounded-xl border border-edge bg-panel/50"
              />
            ))}
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {routines.map((r) => (
              <Link key={r.id} href={`/routines/${r.id}`}>
                <Card className="flex h-full flex-col p-4 transition-colors hover:border-accent/50">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 text-accent">
                        <GitBranch size={16} />
                      </div>
                      <span className="font-medium">{r.name}</span>
                    </div>
                    {r.mode === "LOCKED" ? (
                      <Badge tone="neutral">
                        <Lock size={11} /> locked
                      </Badge>
                    ) : (
                      <Badge tone="accent">v{r.version}</Badge>
                    )}
                  </div>

                  <p className="mt-2 line-clamp-2 flex-1 text-sm text-muted">
                    {r.description}
                  </p>

                  <div className="mt-3 flex flex-wrap gap-1.5">
                    {r.tags.map((t) => (
                      <Badge key={t} tone="neutral">
                        {t}
                      </Badge>
                    ))}
                  </div>

                  <div className="mt-4 flex items-center justify-between border-t border-edge pt-3 text-[11px] text-muted">
                    <span>{r.stepCount} steps</span>
                    <span
                      className={
                        r.reliability >= 0.9
                          ? "text-ok"
                          : r.reliability >= 0.7
                            ? "text-flag"
                            : "text-halt"
                      }
                    >
                      {pct(r.reliability)} reliable
                    </span>
                    <span className="flex items-center gap-1">
                      <Users size={11} /> {r.activeAgents}
                    </span>
                    <span>{timeAgo(r.updatedAt)}</span>
                  </div>
                </Card>
              </Link>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
