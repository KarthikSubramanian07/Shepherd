"use client";

import { useEffect, useRef, useState } from "react";
import { Cpu, Globe, Loader2, Plus, Square, Users } from "lucide-react";
import { api, type FleetSnapshot } from "@/lib/api";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card, CardBody, CardHeader } from "@/components/ui/primitives";

const STATUS_TONE: Record<string, "neutral" | "ok" | "flag" | "halt" | "accent"> = {
  running: "accent",
  completed: "ok",
  failed: "halt",
  halted: "halt",
  pending: "neutral",
};

function fmtMs(ms: number): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export default function FleetPage() {
  const [snap, setSnap] = useState<FleetSnapshot | null>(null);
  const [goal, setGoal] = useState("");
  const [kind, setKind] = useState<"local" | "browserbase">("browserbase");
  const [busy, setBusy] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  async function load() {
    try {
      setSnap(await api.getFleet());
    } catch {
      setSnap({ enabled: false, agents: [], backlog: [], queue: [] });
    }
  }

  useEffect(() => {
    load();
    timer.current = setInterval(load, 1500);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  async function dispatch() {
    if (!goal.trim()) return;
    setBusy(true);
    await api.dispatchAgent(goal.trim(), kind);
    setGoal("");
    setBusy(false);
    load();
  }

  const enabled = snap?.enabled ?? false;
  const agents = snap?.agents ?? [];
  const queue = snap?.queue ?? [];
  const active = snap?.active ?? agents.filter((a) => a.status === "running").length;

  return (
    <div className="flex flex-col">
      <PageHeader
        title="Fleet"
        subtitle="Multiple agents at once — local Agent S serialized on the desktop, Browserbase sessions running in parallel."
        actions={
          <Button variant="danger" size="sm" onClick={() => api.haltAllAgents().then(load)}>
            <Square className="h-3.5 w-3.5" /> Halt all
          </Button>
        }
      />

      <div className="space-y-5 p-6">
        {!enabled && (
          <Card>
            <CardBody className="text-sm text-muted">
              The orchestrator isn&apos;t running. Start the agent with{" "}
              <code className="rounded bg-panel2 px-1.5 py-0.5 text-ink">ENABLE_ORCHESTRATOR=true</code>{" "}
              to dispatch and supervise multiple agents here.
            </CardBody>
          </Card>
        )}

        {/* Dispatch */}
        <Card>
          <CardHeader>
            <div className="flex items-center gap-2 text-sm font-medium text-ink">
              <Plus className="h-4 w-4" /> Dispatch an agent
            </div>
          </CardHeader>
          <CardBody className="flex flex-wrap items-center gap-2">
            <input
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && dispatch()}
              placeholder="e.g. find the cheapest flight to NYC"
              className="h-9 flex-1 min-w-[260px] rounded-lg border border-edge bg-surface px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent/40"
            />
            <div className="flex rounded-lg border border-edge p-0.5">
              {(["browserbase", "local"] as const).map((k) => (
                <button
                  key={k}
                  onClick={() => setKind(k)}
                  className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition ${
                    kind === k ? "bg-panel2 text-ink" : "text-muted"
                  }`}
                >
                  {k === "browserbase" ? <Globe className="h-3.5 w-3.5" /> : <Cpu className="h-3.5 w-3.5" />}
                  {k === "browserbase" ? "Browser" : "Desktop"}
                </button>
              ))}
            </div>
            <Button size="sm" disabled={busy || !enabled} onClick={dispatch}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plus className="h-3.5 w-3.5" />}
              Dispatch
            </Button>
          </CardBody>
        </Card>

        <div className="grid grid-cols-1 gap-5 lg:grid-cols-3">
          {/* Agents */}
          <div className="lg:col-span-2 space-y-3">
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted">
              <Users className="h-3.5 w-3.5" /> Agents
              <span className="text-ink">{active}</span> running · {agents.length} total
            </div>
            {agents.length === 0 && (
              <Card>
                <CardBody className="text-sm text-muted">No agents yet.</CardBody>
              </Card>
            )}
            {agents.map((a) => (
              <Card key={a.agent_id}>
                <CardBody className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs text-muted">{a.agent_id}</span>
                      <Badge tone={STATUS_TONE[a.status] ?? "neutral"}>{a.status}</Badge>
                      <Badge tone="neutral">
                        {a.surface_kind === "browserbase" ? (
                          <Globe className="h-3 w-3" />
                        ) : (
                          <Cpu className="h-3 w-3" />
                        )}
                        {a.surface_kind}
                      </Badge>
                    </div>
                    <p className="mt-1 truncate text-sm text-ink">{a.goal}</p>
                    {a.error && <p className="mt-0.5 text-xs text-halt">{a.error}</p>}
                    <p className="mt-0.5 font-mono text-[11px] text-muted">
                      {fmtMs(a.duration_ms)}
                    </p>
                  </div>
                  {a.status === "running" && (
                    <Button variant="outline" size="sm" onClick={() => api.haltAgent(a.agent_id).then(load)}>
                      <Square className="h-3 w-3" /> Halt
                    </Button>
                  )}
                </CardBody>
              </Card>
            ))}
          </div>

          {/* Action queue */}
          <div className="space-y-3">
            <div className="text-xs font-medium uppercase tracking-wide text-muted">
              Action queue (surface leases)
            </div>
            {queue.length === 0 && (
              <Card>
                <CardBody className="text-sm text-muted">Idle — no leases held.</CardBody>
              </Card>
            )}
            {queue.map((q) => (
              <Card key={q.surface}>
                <CardBody>
                  <div className="font-mono text-[11px] text-muted">{q.surface}</div>
                  <div className="mt-1.5 flex items-center gap-2 text-sm">
                    {q.holder ? (
                      <>
                        <Badge tone="accent">holding</Badge>
                        <span className="font-mono text-xs text-ink">{q.holder}</span>
                        <span className="font-mono text-[11px] text-muted">{fmtMs(q.held_ms)}</span>
                      </>
                    ) : (
                      <span className="text-muted">free</span>
                    )}
                  </div>
                  {q.waiters.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap gap-1">
                      {q.waiters.map((w) => (
                        <Badge key={w.agent_id} tone="neutral">
                          ⏳ {w.agent_id}
                        </Badge>
                      ))}
                    </div>
                  )}
                </CardBody>
              </Card>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
