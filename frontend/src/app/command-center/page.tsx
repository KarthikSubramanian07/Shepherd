"use client";

import Link from "next/link";
import { AlertTriangle, ArrowRight } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import type { Agent, Intervention } from "@/lib/types";
import { PageHeader } from "@/components/layout/PageHeader";
import { AgentCard } from "@/components/agents/AgentCard";
import { Card, Stat } from "@/components/ui/primitives";

export default function CommandCenterPage() {
  const { data: agents, loading } = useAsync<Agent[]>(() => api.listAgents(), []);
  const { data: interventions } = useAsync<Intervention[]>(
    () => api.listInterventions(),
    [],
  );

  const list = agents ?? [];
  const running = list.filter((a) => a.status === "running").length;
  const blocked = list.filter((a) => a.status === "blocked").length;
  const pending = (interventions ?? []).filter((i) => i.status === "pending");

  return (
    <div>
      <PageHeader
        title="Command Center"
        subtitle="Every agent, where it landed, and what needs a human."
      />

      <div className="space-y-6 p-6">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Agents" value={list.length} hint="across all routines" />
          <Stat label="Running" value={running} />
          <Stat label="Blocked" value={blocked} hint="awaiting human" />
          <Stat label="Pending interventions" value={pending.length} />
        </div>

        {pending.length > 0 && (
          <Card className="border-halt/30 bg-halt/5 p-4">
            <div className="mb-3 flex items-center gap-2 text-halt">
              <AlertTriangle size={16} />
              <span className="text-sm font-semibold">
                {pending.length} step{pending.length > 1 ? "s" : ""} need you
              </span>
            </div>
            <div className="space-y-2">
              {pending.map((i) => (
                <Link
                  key={i.id}
                  href="/interventions"
                  className="flex items-center justify-between rounded-lg border border-edge bg-panel px-3 py-2 text-sm transition-colors hover:border-halt/50"
                >
                  <span className="text-ink">{i.detection.reason}</span>
                  <span className="flex items-center gap-1 text-[11px] text-muted">
                    resolve <ArrowRight size={12} />
                  </span>
                </Link>
              ))}
            </div>
          </Card>
        )}

        <div>
          <h2 className="mb-3 text-sm font-semibold text-muted">Active agents</h2>
          {loading ? (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {[0, 1, 2].map((i) => (
                <div
                  key={i}
                  className="h-44 animate-pulse rounded-xl border border-edge bg-panel/50"
                />
              ))}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">
              {list.map((a) => (
                <AgentCard key={a.id} agent={a} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
