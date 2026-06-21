"use client";

import Link from "next/link";
import { Network, Workflow as WorkflowIcon } from "lucide-react";
import { api, type WorkflowSummary } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Card, EmptyState } from "@/components/ui/primitives";
import { timeAgo } from "@/lib/utils";

export default function WorkflowsPage() {
  const { data, loading, error } = useAsync<WorkflowSummary[]>(
    () => api.listWorkflows(),
    [],
  );
  const workflows = data ?? [];

  return (
    <div>
      <PageHeader
        title="Workflows"
        subtitle="Dispatchable, versioned snapshots of a task graph. Each version captures the judgment calls baked in from operator interventions."
      />

      <div className="p-6">
        {loading ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-36 animate-pulse rounded-xl border border-edge bg-panel/50"
              />
            ))}
          </div>
        ) : error ? (
          <EmptyState
            icon={<Network size={28} />}
            title="Couldn't reach the backend"
            description={`${error.message}. Is the Control Hub running on :8765? (NEXT_PUBLIC_BACKEND_BASE overrides the URL.)`}
          />
        ) : workflows.length === 0 ? (
          <EmptyState
            icon={<WorkflowIcon size={28} />}
            title="No workflows yet"
            description="Run a task and bake an intervention with “remember”, or promote a task graph — the dispatchable workflow will appear here."
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {workflows.map((w) => (
              <Link key={w.id} href={`/workflows/${w.id}`}>
                <Card className="flex h-full flex-col p-4 transition-colors hover:border-accent/50">
                  <div className="flex items-start justify-between gap-2">
                    <div className="flex items-center gap-2">
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent/10 text-accent">
                        <Network size={16} />
                      </div>
                      <span className="font-medium">{w.name}</span>
                    </div>
                    <Badge tone="accent">v{w.version}</Badge>
                  </div>

                  {w.description ? (
                    <p className="mt-2 line-clamp-2 text-[12px] text-muted">
                      {w.description}
                    </p>
                  ) : (
                    <p className="mt-2 line-clamp-1 flex-1 font-mono text-[11px] text-muted">
                      {w.id}
                    </p>
                  )}

                  {w.intent_patterns.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {w.intent_patterns.slice(0, 4).map((p) => (
                        <Badge key={p} tone="neutral">
                          {p}
                        </Badge>
                      ))}
                    </div>
                  )}

                  <div className="mt-4 flex items-center justify-between border-t border-edge pt-3 text-[11px] text-muted">
                    <span>{w.nodes} milestones</span>
                    {w.params.length > 0 && <span>{w.params.length} params</span>}
                    {w.updated_at > 0 && (
                      <span>{timeAgo(new Date(w.updated_at * 1000).toISOString())}</span>
                    )}
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
