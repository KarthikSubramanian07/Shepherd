"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { Check, ShieldAlert, X } from "lucide-react";
import { api } from "@/lib/api";
import { useAsync } from "@/lib/hooks";
import { useShepherd } from "@/lib/shepherd-ws";
import type { Intervention } from "@/lib/types";
import { verdictStyle } from "@/lib/status";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card } from "@/components/ui/primitives";
import { timeAgo } from "@/lib/utils";

export default function InterventionsPage() {
  const { data, loading, setData } = useAsync<Intervention[]>(
    () => api.listInterventions(),
    [],
  );
  const { state } = useShepherd();
  const [busy, setBusy] = useState<string | null>(null);

  // Re-fetch when a new monitor alert comes in
  useEffect(() => {
    if (state.monitorAlert) {
      api.listInterventions().then(setData).catch(() => null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.monitorAlert]);

  const list = data ?? [];
  const pending = list.filter((i) => i.status === "pending");
  const resolved = list.filter((i) => i.status !== "pending");

  async function resolve(
    id: string,
    resolution: "approved" | "rejected",
  ) {
    setBusy(id);
    try {
      const updated = await api.resolveIntervention(id, resolution);
      setData(list.map((i) => (i.id === id ? updated : i)));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Interventions"
        subtitle="The proactive safety layer paused these agents. Resolve to continue or stop."
      />

      <div className="space-y-6 p-6">
        <div>
          <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold text-halt">
            <ShieldAlert size={16} /> Needs you ({pending.length})
          </h2>
          {loading ? (
            <div className="h-28 animate-pulse rounded-xl border border-edge bg-panel/50" />
          ) : pending.length === 0 ? (
            <Card className="p-6 text-center text-sm text-muted">
              All clear · no agents are blocked.
            </Card>
          ) : (
            <div className="space-y-3">
              {pending.map((i) => {
                const v = verdictStyle[i.detection.verdict];
                return (
                  <Card key={i.id} className="border-halt/30 p-4">
                    <div className="flex items-start justify-between gap-4">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <Badge tone="halt">{i.detection.type}</Badge>
                          <span className="text-[11px]" style={{ color: v.hex }}>
                            {v.label}
                          </span>
                          <span className="text-[11px] text-muted">
                            {timeAgo(i.createdAt)}
                          </span>
                        </div>
                        <p className="mt-2 text-sm text-ink">{i.detection.reason}</p>
                        <Link
                          href={`/runs/${i.runId}`}
                          className="mt-1 inline-block text-[11px] text-accent hover:underline"
                        >
                          View run {i.runId} →
                        </Link>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy === i.id}
                          onClick={() => resolve(i.id, "rejected")}
                        >
                          <X size={14} /> Stop
                        </Button>
                        <Button
                          size="sm"
                          disabled={busy === i.id}
                          onClick={() => resolve(i.id, "approved")}
                        >
                          <Check size={14} /> Approve &amp; continue
                        </Button>
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </div>

        {resolved.length > 0 && (
          <div>
            <h2 className="mb-3 text-sm font-semibold text-muted">Resolved</h2>
            <Card className="divide-y divide-edge">
              {resolved.map((i) => (
                <div
                  key={i.id}
                  className="flex items-center justify-between px-4 py-2.5 text-sm"
                >
                  <span className="truncate text-muted">{i.detection.reason}</span>
                  <Badge tone={i.resolution === "rejected" ? "halt" : "ok"}>
                    {i.resolution}
                  </Badge>
                </div>
              ))}
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
