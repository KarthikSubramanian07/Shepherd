"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Cpu,
  GitBranch,
  Hand,
  KeyRound,
  Monitor,
  Pause,
  Play,
  Copy,
  Radio,
  Save,
  Send,
  ShieldCheck,
  Sparkles,
  Trash2,
  WifiOff,
  Workflow as WorkflowIcon,
} from "lucide-react";
import {
  type RemoteAgent,
  type RemoteEvent,
  type RemoteOption,
  type RemoteRouting,
  type WorkflowFinalizePayload,
  type WorkflowIntervenePayload,
  useCoordinator,
} from "@/lib/coordinator";
import { agentStatusStyle } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { PageHeader } from "@/components/layout/PageHeader";
import { MicCommandButton } from "@/components/remote/MicCommandButton";
import { WorkflowGraph } from "@/components/graph/WorkflowGraph";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Progress,
  Stat,
  StatusDot,
  Textarea,
} from "@/components/ui/primitives";

export default function RemoteCommandCenterPage() {
  const c = useCoordinator();
  const [intent, setIntent] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [showActivity, setShowActivity] = useState(false);
  const [pickedNode, setPickedNode] = useState<string | null>(null);

  const running = c.agents.filter((a) => a.status === "running").length;
  const blocked = c.agents.filter((a) => a.status === "blocked").length;
  const online = c.agents.filter((a) => a.online).length;

  const send = useCallback(
    (text: string) => {
      const t = text.trim();
      if (!t || !c.selectedId) return;
      c.sendCommand(c.selectedId, "intent", { text: t });
      setToast(`Sent intent to ${c.selected?.name}: "${t}"`);
      setIntent("");
    },
    [c],
  );

  const intervene = useCallback(
    (payload: WorkflowIntervenePayload) => {
      if (!c.selectedId) return;
      c.sendCommand(c.selectedId, "workflow.intervene", { ...payload });
      setToast(
        `Sent steer to ${c.selected?.name}${payload.remember ? " (remembered for future runs)" : ""}`,
      );
    },
    [c],
  );

  const finalize = useCallback(
    (payload: WorkflowFinalizePayload) => {
      if (!c.selectedId) return;
      c.sendCommand(c.selectedId, "workflow.finalize", { ...payload });
      const verb =
        payload.decision === "discard"
          ? "Discarded"
          : payload.decision === "save_as_new"
            ? "Saved as new workflow"
            : "Persisted into the workflow";
      setToast(`${verb} for ${c.selected?.name}`);
    },
    [c],
  );

  const wf = c.selected?.workflow ?? null;

  return (
    <div>
      <PageHeader
        title="Remote Command Center"
        subtitle="Watch an agent operate another machine · its live screen beside the workflow it builds as it goes · and steer or teach it inline."
        actions={
          <div className="flex items-center gap-2">
            <SessionCode code={c.code} onSubmit={c.setCode} />
            <ConnBadge conn={c.conn} />
          </div>
        }
      />

      <div className="space-y-6 p-6">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          <Stat label="Agents" value={c.agents.length} hint={`${online} online`} />
          <Stat label="Running" value={running} />
          <Stat label="Blocked" value={blocked} hint="awaiting human" />
          <Stat label="Coordinator" value={c.conn === "open" ? "Linked" : "·"} />
        </div>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
          {/* Roster */}
          <div className="space-y-2 xl:col-span-1">
            <h2 className="text-sm font-semibold text-muted">Fleet</h2>
            {c.agents.length === 0 ? (
              <EmptyState
                icon={<Radio size={20} />}
                title={c.code ? `No agent on session ${c.code}` : "No agents connected"}
                description={
                  c.code
                    ? "Waiting for an agent to dial in with this session code. Check the code printed on the agent machine."
                    : "Enter the session code printed on the agent machine, or start Shepherd with COORDINATOR_URL set so it dials in here."
                }
              />
            ) : (
              c.agents.map((a) => (
                <RosterCard
                  key={a.id}
                  agent={a}
                  active={a.id === c.selectedId}
                  onClick={() => {
                    c.watch(a.id);
                    setPickedNode(null);
                  }}
                />
              ))
            )}
          </div>

          {/* Detail · unified live view */}
          <div className="xl:col-span-3">
            {!c.selected ? (
              <EmptyState
                icon={<Monitor size={20} />}
                title="Select an agent"
                description="Pick a machine from the fleet to watch its live screen and the workflow graph it traverses."
              />
            ) : (
              <div className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <StatusDot
                      hex={agentStatusStyle[c.selected.status].hex}
                      pulse={c.selected.status === "running" || c.selected.status === "blocked"}
                    />
                    <span className="font-medium text-ink">{c.selected.name}</span>
                    <span className="text-xs text-muted">{c.selected.host}</span>
                    {wf?.name && (
                      <Badge tone="accent">
                        <WorkflowIcon size={12} /> {wf.name}
                      </Badge>
                    )}
                  </div>
                  {wf && (
                    <div className="flex items-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          c.sendCommand(c.selected!.id, "workflow.pause");
                          setToast("Pause requested · agent will wait at the next milestone");
                        }}
                      >
                        <Pause size={14} /> Pause
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => {
                          c.sendCommand(c.selected!.id, "workflow.resume");
                          setToast("Resumed · agent proceeds autonomously");
                        }}
                      >
                        <Play size={14} /> Resume
                      </Button>
                    </div>
                  )}
                </div>

                {/* Inline intervention */}
                {c.selected.block?.workflow ? (
                  <WorkflowIntervenePanel
                    agent={c.selected}
                    options={c.selected.block.options ?? wf?.nodes.find((n) => n.key === wf.current)?.options ?? []}
                    targetNode={pickedNode}
                    onClearTarget={() => setPickedNode(null)}
                    onIntervene={intervene}
                    onResume={() => c.sendCommand(c.selected!.id, "workflow.resume")}
                  />
                ) : c.selected.block ? (
                  <InterventionBanner
                    agent={c.selected}
                    onApprove={() => c.sendCommand(c.selected!.id, "approve")}
                    onHalt={() => c.sendCommand(c.selected!.id, "halt")}
                    onOverride={(instruction) =>
                      c.sendCommand(c.selected!.id, "override", { instruction })
                    }
                  />
                ) : null}

                {/* End-of-run persist gate */}
                {wf?.finalize && (
                  <FinalizePanel
                    finalize={wf.finalize}
                    onFinalize={finalize}
                  />
                )}

                {/* Live screen + live workflow graph, side by side */}
                <div className="grid grid-cols-1 gap-3 2xl:grid-cols-2">
                  <LiveScreen frame={c.frame} agent={c.selected} />
                  <WorkflowPane
                    agent={c.selected}
                    nodeShots={c.nodeShots}
                    pickedNode={pickedNode}
                    onPickNode={(k) =>
                      setPickedNode((prev) => (prev === k ? null : k))
                    }
                  />
                </div>

                {/* Dispatch bar — ad-hoc task → vector router → workflow / fresh run */}
                <Card className="p-3">
                  <div className="flex items-center gap-2">
                    <Input
                      value={intent}
                      onChange={(e) => setIntent(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && send(intent)}
                      placeholder={`Dispatch a task to ${c.selected.name}… (e.g. "apply to the job")`}
                    />
                    <Button onClick={() => send(intent)} disabled={!intent.trim()}>
                      <Send size={15} /> Dispatch
                    </Button>
                    <MicCommandButton onTranscript={send} onError={(m) => setToast(m)} />
                    <Button variant="danger" onClick={() => c.sendCommand(c.selected!.id, "halt")}>
                      <Hand size={15} /> Halt
                    </Button>
                  </div>
                  <p className="mt-2 text-[11px] text-muted">
                    A typed or spoken task is routed by the vector layer to a saved
                    workflow (or a fresh autonomous run if none matches). Mid-run, Halt
                    stops the agent at the next safe step boundary.
                  </p>
                  {c.selected.routing && <RoutingBanner routing={c.selected.routing} />}
                </Card>

                {/* Raw activity (collapsible secondary pane) */}
                <Card className="p-0">
                  <button
                    onClick={() => setShowActivity((v) => !v)}
                    className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium text-muted hover:text-ink"
                  >
                    {showActivity ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    Raw activity log
                    <span className="ml-auto text-[11px] text-muted">{c.events.length} events</span>
                  </button>
                  {showActivity && <ActivityFeed events={c.events} />}
                </Card>
              </div>
            )}
          </div>
        </div>
      </div>

      {toast && (
        <button
          onClick={() => setToast(null)}
          className="fixed bottom-4 right-4 z-50 max-w-sm rounded-lg border border-edge bg-panel2 px-4 py-2 text-left text-sm text-ink shadow-lg"
        >
          {toast}
        </button>
      )}
    </div>
  );
}

function SessionCode({ code, onSubmit }: { code: string; onSubmit: (code: string) => void }) {
  const [draft, setDraft] = useState(code);
  useEffect(() => setDraft(code), [code]);
  return (
    <div className="flex items-center gap-1.5">
      <KeyRound size={14} className="text-muted" />
      <Input
        value={draft}
        onChange={(e) => setDraft(e.target.value.toUpperCase())}
        onKeyDown={(e) => e.key === "Enter" && onSubmit(draft)}
        placeholder="Session code"
        className="h-8 w-32 font-mono uppercase"
      />
      <Button size="sm" variant="outline" onClick={() => onSubmit(draft)}>
        Connect
      </Button>
    </div>
  );
}

function ConnBadge({ conn }: { conn: "connecting" | "open" | "closed" }) {
  if (conn === "open")
    return (
      <Badge tone="ok">
        <Radio size={12} /> coordinator linked
      </Badge>
    );
  if (conn === "connecting")
    return (
      <Badge tone="flag">
        <Radio size={12} /> connecting…
      </Badge>
    );
  return (
    <Badge tone="halt">
      <WifiOff size={12} /> coordinator offline
    </Badge>
  );
}

function RosterCard({
  agent,
  active,
  onClick,
}: {
  agent: RemoteAgent;
  active: boolean;
  onClick: () => void;
}) {
  const s = agentStatusStyle[agent.status];
  return (
    <button
      onClick={onClick}
      className={[
        "w-full rounded-xl border bg-panel/80 p-3 text-left transition-colors",
        active ? "border-accent/60" : "border-edge hover:border-accent/40",
        !agent.online && "opacity-60",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <StatusDot
            hex={agent.online ? s.hex : "#64748b"}
            pulse={agent.status === "running" || agent.status === "blocked"}
          />
          <span className="font-medium text-ink">{agent.name}</span>
        </div>
        <Badge
          tone={
            agent.status === "blocked" || agent.status === "failed"
              ? "halt"
              : agent.status === "completed"
                ? "ok"
                : agent.status === "running"
                  ? "accent"
                  : "neutral"
          }
        >
          {agent.online ? s.label : "Offline"}
        </Badge>
      </div>
      <div className="mt-1 text-xs text-muted">
        {agent.workflow?.name ?? agent.routineId ?? "idle"}
      </div>
      <Progress className="mt-2" value={agent.progress} tone={s.hex} />
      {agent.block?.reason && (
        <div className="mt-2 rounded-lg border border-halt/30 bg-halt/10 px-2 py-1 text-[11px] text-halt">
          {agent.block.reason}
        </div>
      )}
      <div className="mt-2 flex items-center justify-between text-[11px] text-muted">
        <span className="flex items-center gap-1">
          <Cpu size={12} /> {agent.host}
        </span>
        <span>
          step {agent.currentStepIndex ?? "·"} · {timeAgo(agent.lastActivityAt)}
        </span>
      </div>
    </button>
  );
}

function LiveScreen({ frame, agent }: { frame: string | null; agent: RemoteAgent }) {
  return (
    <Card className="overflow-hidden">
      <div className="relative flex min-h-[320px] items-center justify-center bg-black/60">
        {frame ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={frame} alt={`${agent.name} live screen`} className="max-h-[60vh] w-full object-contain" />
        ) : (
          <div className="flex flex-col items-center gap-2 py-16 text-muted">
            <Monitor size={28} />
            <span className="text-sm">{agent.online ? "Waiting for frames…" : "Agent offline"}</span>
          </div>
        )}
        <div className="absolute left-2 top-2 flex items-center gap-1 rounded-md bg-black/60 px-2 py-1 text-[11px] text-white">
          <Radio size={11} className="text-red-400" /> LIVE · {agent.mode}
        </div>
      </div>
    </Card>
  );
}

function WorkflowPane({
  agent,
  nodeShots,
  pickedNode,
  onPickNode,
}: {
  agent: RemoteAgent;
  nodeShots: Record<string, string>;
  pickedNode: string | null;
  onPickNode: (key: string) => void;
}) {
  const wf = agent.workflow;
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-edge px-3 py-2 text-xs text-muted">
        <WorkflowIcon size={13} /> Live workflow graph
        <span className="ml-auto">click a milestone to target a steer</span>
      </div>
      <div className="h-[60vh] min-h-[320px] bg-canvas">
        {wf && wf.nodes.length > 0 ? (
          <WorkflowGraph
            workflow={wf}
            nodeShots={nodeShots}
            pickedNode={pickedNode}
            onPickNode={onPickNode}
          />
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
            <WorkflowIcon size={28} />
            <span className="text-sm">No workflow running yet.</span>
            <span className="text-[11px]">Dispatch a workflow intent to build the graph live.</span>
          </div>
        )}
      </div>
    </Card>
  );
}

function ActivityFeed({ events }: { events: RemoteEvent[] }) {
  const recent = [...events].slice(-120).reverse();
  return (
    <div className="max-h-[40vh] overflow-auto px-3 pb-3">
      {recent.length === 0 ? (
        <p className="py-8 text-center text-sm text-muted">No activity yet.</p>
      ) : (
        <ul className="space-y-1 font-mono text-[12px]">
          {recent.map((e, i) => (
            <li key={i} className="flex gap-2">
              <span className="shrink-0 text-accent">{e.type}</span>
              <span className="truncate text-muted">{describeEvent(e)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function describeEvent(e: RemoteEvent): string {
  const d = e.data ?? {};
  switch (e.type) {
    case "step.start":
      return `#${d.index as number} ${(d.description as string) ?? ""}`;
    case "step.complete":
      return `#${d.index as number} ${(d.status as string) ?? ""} (${d.duration_ms as number}ms)`;
    case "monitor.alert":
      return `${d.verdict as string}: ${d.reason as string}`;
    case "monitor.decision":
      return `decision: ${d.decision as string}`;
    case "workflow.node.enter":
      return `▸ ${(d.label as string) ?? (d.node_key as string)}`;
    case "workflow.step":
      return `${(d.label as string) ?? ""} → ${(d.status as string) ?? ""}${d.branch ? ` (branch: ${d.branch as string})` : ""}`;
    case "workflow.awaiting":
      return `awaiting you at ${(d.label as string) ?? (d.node_key as string)}`;
    case "workflow.intervention":
      return `steer: ${(d.instruction as string) ?? (d.decision as string)}`;
    case "workflow.baked":
      return `crystallized ${(d.ops as unknown[])?.length ?? ""} edit(s)`;
    case "workflow.finalize":
      return `awaiting persist choice (${(d.ops as unknown[])?.length ?? 0} judgment call(s))`;
    case "workflow.finalized":
      return `${d.action as string} → ${d.workflow_id as string} v${d.version as number}`;
    case "workflow.done":
      return `${d.status as string} · ${d.steps as number} steps`;
    case "remote.intent":
      return `“${d.text as string}”`;
    case "intent.received":
      return `dispatched: “${d.raw_text as string}”`;
    case "plan.resolved":
      return `routed → ${(d.kind as string) ?? "?"} ${(d.target as string) ?? ""}${
        typeof d.confidence === "number" ? ` @ ${Math.round((d.confidence as number) * 100)}%` : ""
      }`;
    case "execution.complete":
      return `${d.status as string} · ${d.steps_completed as number} steps`;
    default:
      try {
        return JSON.stringify(d).slice(0, 120);
      } catch {
        return "";
      }
  }
}

function RoutingBanner({ routing }: { routing: RemoteRouting }) {
  const pct =
    typeof routing.confidence === "number"
      ? `${Math.round(routing.confidence * 100)}%`
      : null;

  if (routing.state === "routing") {
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-edge bg-panel2 px-3 py-2 text-[12px] text-muted">
        <GitBranch size={13} className="shrink-0 animate-pulse" />
        Routing “{routing.text}” through the vector layer…
      </div>
    );
  }

  if (routing.state === "matched") {
    const isWorkflow = routing.kind === "WORKFLOW";
    return (
      <div className="mt-3 flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-[12px] text-ink">
        <Sparkles size={13} className="shrink-0 text-accent" />
        Matched {isWorkflow ? "workflow" : "routine"}{" "}
        <span className="font-mono text-accent">{routing.target}</span>
        {pct && <Badge tone="accent">{pct}</Badge>}
        {routing.source && (
          <span className="text-muted">via {routing.source}</span>
        )}
      </div>
    );
  }

  // unmatched / autonomous
  const autonomous = routing.state === "autonomous";
  return (
    <div className="mt-3 flex items-center gap-2 rounded-lg border border-edge bg-panel2 px-3 py-2 text-[12px] text-muted">
      <Cpu size={13} className="shrink-0" />
      {autonomous
        ? "No saved workflow matched → running a fresh autonomous task."
        : "No saved workflow matched this task."}
    </div>
  );
}

function FinalizePanel({
  finalize,
  onFinalize,
}: {
  finalize: NonNullable<RemoteAgent["workflow"]>["finalize"];
  onFinalize: (payload: WorkflowFinalizePayload) => void;
}) {
  const [saveAsNew, setSaveAsNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [newId, setNewId] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const f = finalize!;
  const ops = f.ops ?? [];

  if (done) {
    return (
      <Card className="border-ok/40 bg-ok/5 p-4">
        <div className="flex items-center gap-2 text-sm text-ok">
          <ShieldCheck size={16} /> {done}
        </div>
      </Card>
    );
  }

  return (
    <Card className="border-accent/50 bg-accent/5 p-4">
      <div className="mb-2 flex items-center gap-2 text-accent">
        <Sparkles size={16} />
        <span className="text-sm font-semibold">
          Run captured {ops.length} judgment call{ops.length === 1 ? "" : "s"} — persist them?
        </span>
      </div>
      <p className="mb-3 text-[13px] text-muted">
        {f.name ?? f.workflow_id} would go from v{f.current_version} → v{f.proposed_version} so future
        agents inherit these decisions. Persist into the workflow (default), save as a new workflow,
        or discard.
      </p>

      {ops.length > 0 && (
        <ul className="mb-3 space-y-1.5">
          {ops.map((o, i) => (
            <li key={i} className="flex items-start gap-2 text-[13px] leading-snug">
              <GitBranch size={13} className="mt-0.5 shrink-0 text-accent" />
              <span>
                <span className="font-mono text-[11px] text-muted">{o.node}</span>: if{" "}
                <span className="text-accent">{o.when}</span> → {o.do}
              </span>
            </li>
          ))}
        </ul>
      )}

      {saveAsNew && (
        <div className="mb-3 space-y-2">
          <Input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="New workflow name (e.g. Apply to a job — taught)"
          />
          <Input
            value={newId}
            onChange={(e) => setNewId(e.target.value)}
            placeholder="New workflow id (optional — auto-generated if blank)"
          />
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {!saveAsNew ? (
          <>
            <Button
              onClick={() => {
                onFinalize({ decision: "persist" });
                setDone(`Persisted → v${f.proposed_version}`);
              }}
            >
              <Save size={15} /> Persist (v{f.proposed_version})
            </Button>
            <Button variant="outline" onClick={() => setSaveAsNew(true)}>
              <Copy size={15} /> Save as new…
            </Button>
            <Button
              variant="danger"
              onClick={() => {
                onFinalize({ decision: "discard" });
                setDone("Discarded — workflow unchanged");
              }}
            >
              <Trash2 size={15} /> Discard
            </Button>
          </>
        ) : (
          <>
            <Button
              onClick={() => {
                onFinalize({
                  decision: "save_as_new",
                  name: newName.trim(),
                  new_id: newId.trim(),
                });
                setDone("Saved as a new workflow");
              }}
            >
              <Copy size={15} /> Save as new workflow
            </Button>
            <Button variant="outline" onClick={() => setSaveAsNew(false)}>
              Back
            </Button>
          </>
        )}
      </div>
    </Card>
  );
}

function WorkflowIntervenePanel({
  agent,
  options,
  targetNode,
  onClearTarget,
  onIntervene,
  onResume,
}: {
  agent: RemoteAgent;
  options: RemoteOption[];
  targetNode: string | null;
  onClearTarget: () => void;
  onIntervene: (payload: WorkflowIntervenePayload) => void;
  onResume: () => void;
}) {
  const b = agent.block!;
  const [instruction, setInstruction] = useState("");
  const [scenario, setScenario] = useState("");
  const [branch, setBranch] = useState<string>("");
  const [remember, setRemember] = useState(false);

  const submit = () => {
    if (!instruction.trim() && !branch) return;
    onIntervene({
      instruction: instruction.trim(),
      next_key: branch,
      scenario: scenario.trim(),
      remember,
      target_node: targetNode ?? "",
    });
    setInstruction("");
    setScenario("");
    setBranch("");
    setRemember(false);
  };

  return (
    <Card className="border-flag/50 bg-flag/5 p-4">
      <div className="mb-2 flex items-center gap-2 text-flag">
        <AlertTriangle size={16} />
        <span className="text-sm font-semibold">
          Milestone “{b.label ?? b.nodeKey}” is awaiting you
        </span>
      </div>
      <p className="mb-3 text-sm text-ink">{b.reason}</p>

      {targetNode && (
        <div className="mb-3 flex items-center gap-2 rounded-lg border border-accent/30 bg-accent/10 px-2 py-1 text-[11px] text-accent">
          <GitBranch size={12} /> Targeting milestone: <span className="font-mono">{targetNode}</span>
          <button onClick={onClearTarget} className="ml-auto underline">
            clear
          </button>
        </div>
      )}

      {options.length > 0 && (
        <div className="mb-3">
          <div className="mb-1 text-[11px] font-medium text-muted">Force a branch (optional)</div>
          <div className="flex flex-wrap gap-1.5">
            {options.map((o) => (
              <button
                key={o.key}
                onClick={() => setBranch((prev) => (prev === o.key ? "" : o.key))}
                className={[
                  "rounded-full border px-2.5 py-1 text-[11px] transition-colors",
                  branch === o.key
                    ? "border-accent bg-accent/20 text-accent"
                    : "border-edge text-muted hover:border-accent/50",
                ].join(" ")}
                title={o.when ?? undefined}
              >
                {o.label ?? o.key}
                {o.when ? ` · if ${o.when}` : ""}
              </button>
            ))}
          </div>
        </div>
      )}

      <Textarea
        value={instruction}
        onChange={(e) => setInstruction(e.target.value)}
        placeholder="Steer this milestone · e.g. “research the projects page and fill in the summary”"
        rows={2}
      />
      <Input
        className="mt-2"
        value={scenario}
        onChange={(e) => setScenario(e.target.value)}
        placeholder="When does this apply? (the condition to remember, e.g. “the projects field is empty”)"
      />

      <label className="mt-3 flex cursor-pointer items-center gap-2 text-sm text-ink">
        <input
          type="checkbox"
          checked={remember}
          onChange={(e) => setRemember(e.target.checked)}
          className="h-4 w-4 rounded border-edge accent-accent"
        />
        <Sparkles size={14} className="text-accent" />
        Remember this as a default answer for future agents running this workflow
      </label>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button onClick={submit} disabled={!instruction.trim() && !branch}>
          <Send size={15} /> Send steer
        </Button>
        <Button variant="outline" onClick={onResume}>
          <Play size={15} /> Resume without steering
        </Button>
      </div>
    </Card>
  );
}

function InterventionBanner({
  agent,
  onApprove,
  onHalt,
  onOverride,
}: {
  agent: RemoteAgent;
  onApprove: () => void;
  onHalt: () => void;
  onOverride: (instruction: string) => void;
}) {
  const [override, setOverride] = useState("");
  const b = agent.block!;
  return (
    <Card className="border-halt/40 bg-halt/5 p-4">
      <div className="mb-2 flex items-center gap-2 text-halt">
        <AlertTriangle size={16} />
        <span className="text-sm font-semibold">
          Step {b.stepIndex ?? "?"} needs you · {b.trigger ?? b.verdict}
        </span>
      </div>
      <p className="mb-3 text-sm text-ink">{b.reason}</p>

      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={onApprove}>
          <ShieldCheck size={15} /> Approve & continue
        </Button>
        <Button variant="danger" onClick={onHalt}>
          <Hand size={15} /> Halt
        </Button>
        {b.suggestions?.map((s) => (
          <Button
            key={s.label}
            variant="outline"
            onClick={() => (s.action === "halt" ? onHalt() : onApprove())}
          >
            {s.label}
          </Button>
        ))}
      </div>

      <div className="mt-3 flex items-center gap-2">
        <Input
          value={override}
          onChange={(e) => setOverride(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && override.trim()) {
              onOverride(override.trim());
              setOverride("");
            }
          }}
          placeholder="Or steer: type an override instruction for the agent…"
        />
        <Button
          variant="outline"
          disabled={!override.trim()}
          onClick={() => {
            onOverride(override.trim());
            setOverride("");
          }}
        >
          Override
        </Button>
      </div>
    </Card>
  );
}
