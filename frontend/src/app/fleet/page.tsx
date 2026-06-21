"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Activity, ChevronDown, ChevronRight, Cpu, Globe, Loader2, MessageSquareText, Plus, Rocket, Square, Trash2, Users, X } from "lucide-react";
import { api, type FleetSnapshot } from "@/lib/api";
import type { RemoteTrace } from "@/lib/coordinator";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, Button, Card, CardBody, CardHeader } from "@/components/ui/primitives";
import { TraceGraph } from "@/components/graph/TraceGraph";

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

interface StagedTask {
  id: number;
  goal: string;
  kind: "local" | "browserbase";
}

export default function FleetPage() {
  const [snap, setSnap] = useState<FleetSnapshot | null>(null);
  const [goal, setGoal] = useState("");
  const [kind, setKind] = useState<"local" | "browserbase">("browserbase");
  const [staged, setStaged] = useState<StagedTask[]>([]);
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [traceOpen, setTraceOpen] = useState<Record<string, boolean>>({});
  const [traces, setTraces] = useState<Record<string, RemoteTrace>>({});
  const idRef = useRef(1);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const traceTimer = useRef<ReturnType<typeof setInterval> | null>(null);

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

  const loadTraces = useCallback(async () => {
    const openIds = Object.entries(traceOpen)
      .filter(([, v]) => v)
      .map(([k]) => k);
    if (openIds.length === 0) return;
    try {
      const results = await Promise.all(
        openIds.map(async (id) => {
          const tr = await api.getAgentTrace(id);
          return [id, tr] as const;
        }),
      );
      setTraces((prev) => {
        const next = { ...prev };
        for (const [id, tr] of results) {
          if (tr) next[id] = tr;
        }
        return next;
      });
    } catch {
      // Backend unreachable — keep existing traces, retry on next interval.
    }
  }, [traceOpen]);

  useEffect(() => {
    loadTraces();
    traceTimer.current = setInterval(loadTraces, 1500);
    return () => {
      if (traceTimer.current) clearInterval(traceTimer.current);
    };
  }, [loadTraces]);

  function addToQueue() {
    const g = goal.trim();
    if (!g) return;
    setStaged((s) => [...s, { id: idRef.current++, goal: g, kind }]);
    setGoal("");
  }

  function removeStaged(id: number) {
    setStaged((s) => s.filter((t) => t.id !== id));
  }

  async function deployAll() {
    if (staged.length === 0) return;
    setBusy(true);
    await api.dispatchBatch(staged.map((t) => ({ goal: t.goal, surface_kind: t.kind })));
    setStaged([]);
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

        {/* Queue builder — stage tasks, then deploy all at once */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 text-sm font-medium text-ink">
                <Plus className="h-4 w-4" /> Queue tasks
              </div>
              <span className="text-xs text-muted">
                {staged.length} staged — add tasks, then deploy them all at once
              </span>
            </div>
          </CardHeader>
          <CardBody className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <input
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && addToQueue()}
                placeholder="e.g. find the cheapest flight to NYC"
                className="h-9 flex-1 min-w-[260px] rounded-lg border border-edge bg-canvas px-3 text-sm text-ink outline-none focus:ring-2 focus:ring-accent/40"
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
              <Button variant="outline" size="sm" onClick={addToQueue}>
                <Plus className="h-3.5 w-3.5" /> Add to queue
              </Button>
            </div>

            {/* Staged list */}
            {staged.length > 0 && (
              <div className="space-y-1.5">
                {staged.map((t, i) => (
                  <div
                    key={t.id}
                    className="flex items-center gap-2 rounded-lg border border-edge bg-panel2 px-3 py-2"
                  >
                    <span className="font-mono text-[11px] text-muted">{i + 1}</span>
                    <Badge tone="neutral">
                      {t.kind === "browserbase" ? <Globe className="h-3 w-3" /> : <Cpu className="h-3 w-3" />}
                      {t.kind === "browserbase" ? "Browser" : "Desktop"}
                    </Badge>
                    <span className="flex-1 truncate text-sm text-ink">{t.goal}</span>
                    <button
                      onClick={() => removeStaged(t.id)}
                      className="text-muted hover:text-halt"
                      aria-label="Remove task"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ))}
              </div>
            )}

            {/* Deploy bar */}
            <div className="flex items-center justify-end gap-2 border-t border-edge pt-3">
              {staged.length > 0 && (
                <Button variant="ghost" size="sm" onClick={() => setStaged([])}>
                  <Trash2 className="h-3.5 w-3.5" /> Clear
                </Button>
              )}
              <Button size="sm" disabled={busy || !enabled || staged.length === 0} onClick={deployAll}>
                {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Rocket className="h-3.5 w-3.5" />}
                Deploy all{staged.length > 0 ? ` (${staged.length})` : ""}
              </Button>
            </div>
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

                    {/* Execution graph toggle */}
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <button
                        type="button"
                        onClick={() =>
                          setTraceOpen((e) => ({ ...e, [a.agent_id]: !e[a.agent_id] }))
                        }
                        className="flex items-center gap-1 text-[11px] font-medium text-muted hover:text-ink"
                        aria-expanded={!!traceOpen[a.agent_id]}
                      >
                        {traceOpen[a.agent_id] ? (
                          <ChevronDown className="h-3 w-3" />
                        ) : (
                          <ChevronRight className="h-3 w-3" />
                        )}
                        <Activity className="h-3 w-3" />
                        Execution graph
                      </button>

                      {/* Response · expandable medium summary of the finished run */}
                      {a.response && (
                        <button
                          type="button"
                          onClick={() =>
                            setExpanded((e) => ({ ...e, [a.agent_id]: !e[a.agent_id] }))
                          }
                          className="flex items-center gap-1 text-[11px] font-medium text-muted hover:text-ink"
                          aria-expanded={!!expanded[a.agent_id]}
                        >
                          {expanded[a.agent_id] ? (
                            <ChevronDown className="h-3 w-3" />
                          ) : (
                            <ChevronRight className="h-3 w-3" />
                          )}
                          <MessageSquareText className="h-3 w-3" />
                          Response
                        </button>
                      )}
                    </div>

                    {traceOpen[a.agent_id] && traces[a.agent_id] && (
                      <div className="mt-2 h-[320px] rounded-lg border border-edge bg-panel2/40">
                        <TraceGraph
                          trace={traces[a.agent_id]}
                          nodeShots={{}}
                        />
                      </div>
                    )}
                    {traceOpen[a.agent_id] && !traces[a.agent_id] && (
                      <div className="mt-2 flex h-[120px] items-center justify-center rounded-lg border border-edge bg-panel2/40">
                        <span className="text-xs text-muted">No trace data yet</span>
                      </div>
                    )}

                    {expanded[a.agent_id] && a.response && (
                      <p className="mt-1 whitespace-pre-wrap rounded-lg border border-edge bg-panel2/60 p-2.5 text-xs leading-relaxed text-ink">
                        {a.response}
                      </p>
                    )}
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
