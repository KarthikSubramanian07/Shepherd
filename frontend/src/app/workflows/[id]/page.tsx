"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, GitBranch, Network, Sparkles } from "lucide-react";
import { api, type WorkflowDetail } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { RemoteWorkflow } from "@/lib/coordinator";
import { WorkflowGraph } from "@/components/graph/WorkflowGraph";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Card, EmptyState, Spinner, Stat } from "@/components/ui/primitives";

/** Map the persisted workflow definition onto the shape the live graph renders.
 * No run is in flight, so there's no current node / status — this shows the
 * definition itself, including the baked conditionals (the judgment calls). */
function toRemoteWorkflow(wf: WorkflowDetail): RemoteWorkflow {
  return {
    id: wf.id,
    name: wf.name,
    current: null,
    awaiting: false,
    nodes: wf.nodes.map((n) => ({
      key: n.key,
      label: n.label,
      kind: n.kind,
      instruction: n.instruction,
      conditionals: n.conditionals.map((c) => ({
        when: c.when,
        do: c.do,
        goto: c.goto,
      })),
    })),
    edges: wf.edges.map((e) => ({ from: e.from, to: e.to, when: e.condition })),
  };
}

export default function WorkflowDetailPage() {
  const params = useParams<{ id: string }>();
  const id = params.id;

  const { data: wf, loading, error } = useAsync<WorkflowDetail | null>(
    () => api.getWorkflow(id),
    [id],
  );

  const remote = useMemo(() => (wf ? toRemoteWorkflow(wf) : null), [wf]);

  const conditionals = useMemo(
    () =>
      (wf?.nodes ?? []).flatMap((n) =>
        n.conditionals.map((c) => ({ node: n.label || n.key, ...c })),
      ),
    [wf],
  );

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center">
        <Spinner size={22} />
      </div>
    );
  }

  if (error || !wf || !remote) {
    return (
      <div className="flex h-full flex-col">
        <PageHeader title="Workflow" subtitle={id} />
        <div className="flex flex-1 items-center justify-center p-6">
          <EmptyState
            icon={<Network size={28} />}
            title={error ? "Couldn't load workflow" : "Workflow not found"}
            description={
              error
                ? `${error.message}. Is the Control Hub running on :8765?`
                : `No workflow with id ${id}.`
            }
            action={
              <Link
                href="/workflows"
                className="text-sm text-accent hover:underline"
              >
                ← Back to workflows
              </Link>
            }
          />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        title={wf.name}
        subtitle={wf.id}
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="accent">v{wf.version}</Badge>
            <Link
              href="/workflows"
              className="flex items-center gap-1 text-sm text-muted hover:text-ink"
            >
              <ArrowLeft size={14} /> All workflows
            </Link>
          </div>
        }
      />

      <div className="flex min-h-0 flex-1 flex-col gap-4 p-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Version" value={`v${wf.version}`} />
          <Stat label="Milestones" value={wf.nodes.length} />
          <Stat label="Transitions" value={wf.edges.length} />
          <Stat
            label="Judgment calls"
            value={conditionals.length}
            hint={conditionals.length ? "baked conditionals" : undefined}
          />
        </div>

        <Card className="relative min-h-0 flex-1 overflow-hidden p-0">
          <WorkflowGraph workflow={remote} nodeShots={{}} />
        </Card>

        {conditionals.length > 0 && (
          <Card className="p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-medium">
              <Sparkles size={14} className="text-accent" />
              Judgment calls baked in
            </div>
            <ul className="space-y-2">
              {conditionals.map((c, i) => (
                <li
                  key={i}
                  className="flex items-start gap-2 text-[13px] leading-snug"
                >
                  <GitBranch size={13} className="mt-0.5 shrink-0 text-accent" />
                  <span>
                    <span className="font-medium">{c.node}</span>: if{" "}
                    <span className="text-accent">{c.when}</span> → {c.do}
                    {c.goto && (
                      <span className="text-muted"> (go to {c.goto})</span>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {(wf.intent_patterns.length > 0 || wf.params.length > 0) && (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-muted">
            {wf.intent_patterns.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span>Dispatch intents:</span>
                {wf.intent_patterns.slice(0, 6).map((p, i) => (
                  <Badge key={i} tone="neutral">
                    {p}
                  </Badge>
                ))}
              </div>
            )}
            {wf.params.length > 0 && (
              <div className="flex flex-wrap items-center gap-2">
                <span>Params:</span>
                {wf.params.map((p, i) => (
                  <Badge key={i} tone="neutral">
                    {p}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
