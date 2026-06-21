"use client";

import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertTriangle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Cpu,
  GitBranch,
  Hand,
  KeyRound,
  Loader2,
  Maximize2,
  ListTree,
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
  X,
} from "lucide-react";
import {
  type RemoteAgent,
  type RemoteEvent,
  type RemoteOption,
  type RemoteRouting,
  type WorkflowFinalizePayload,
  type WorkflowIntervenePayload,
  fetchAgentCatalog,
  useCoordinator,
} from "@/lib/coordinator";
import { agentStatusStyle } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { useWebRTC, type WebRTCState } from "@/lib/webrtc";
import { PageHeader } from "@/components/layout/PageHeader";
import { MicCommandButton } from "@/components/remote/MicCommandButton";
import { WorkflowGraph } from "@/components/graph/WorkflowGraph";
import { TraceGraph } from "@/components/graph/TraceGraph";
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
  const [halting, setHalting] = useState(false);
  const haltEvtBase = useRef(0);
  const [bakeToggle, setBakeToggle] = useState(true);
  const [rosterOpen, setRosterOpen] = useState(true);
  const [expanded, setExpanded] = useState(false);
  // Track whether we've already fired the promote command for this run.
  // Keyed by "agentId:runId" to avoid re-firing after agent switch-back.
  const promotedRef = useRef<string | null>(null);

  // Clear halting state when the selected agent changes.
  useEffect(() => {
    setHalting(false);
    setExpanded(false);
  }, [c.selectedId]);

  // Auto-reopen roster when the selected agent disappears (disconnect, etc.)
  // so the user isn't stuck with no way to pick another agent.
  useEffect(() => {
    if (!c.selected && !rosterOpen) setRosterOpen(true);
  }, [c.selected, rosterOpen]);

  // Clear halting state when the halt is confirmed or the agent stops naturally.
  const selectedStatus = c.selected?.status;
  const events = c.events;
  const eventsLen = events.length;
  useEffect(() => {
    if (!halting) return;
    for (let i = haltEvtBase.current; i < events.length; i++) {
      if (events[i].type === "execution.halted" || events[i].type === "execution.suspended") {
        const stepIdx = events[i].data?.step_index as number | undefined;
        const verb = events[i].type === "execution.suspended" ? "suspended" : "halted";
        setToast(`Agent ${verb} at step ${stepIdx ?? "?"}`);
        setHalting(false);
        return;
      }
    }
    if (selectedStatus && selectedStatus !== "running") {
      setHalting(false);
    }
  }, [halting, selectedStatus, eventsLen, events]);

  // Show toast when agent proactively requests help.
  const helpEvtBase = useRef(0);
  useEffect(() => {
    for (let i = helpEvtBase.current; i < events.length; i++) {
      if (events[i].type === "step.help_requested") {
        const msg = (events[i].data?.help_message as string) || "Agent needs assistance";
        setToast(`\u26A0\uFE0F ${msg}`);
      }
      if (events[i].type === "execution.suspended" && events[i].data?.reason === "agent_requested_help") {
        const msg = (events[i].data?.help_message as string) || "Agent needs assistance";
        setToast(`\u26A0\uFE0F ${msg}`);
      }
    }
    helpEvtBase.current = events.length;
  }, [eventsLen, events]);

  // Auto-promote: when the toggle is on and the trace signals promoteReady,
  // fire the promote command exactly once per run (idempotency guard).
  const trace = c.selected?.trace ?? null;
  const promoteKey = c.selectedId && trace?.runId ? `${c.selectedId}:${trace.runId}` : null;
  useEffect(() => {
    if (
      bakeToggle &&
      trace?.promoteReady &&
      !trace?.promoted &&
      trace?.routineId &&
      c.selectedId &&
      promoteKey &&
      promotedRef.current !== promoteKey
    ) {
      promotedRef.current = promoteKey;
      c.sendCommand(c.selectedId, "promote", { task_key: trace.routineId });
    }
  }, [bakeToggle, trace?.promoteReady, trace?.promoted, trace?.routineId, promoteKey, c.selectedId, c.sendCommand]);

  // Reset the toggle when a genuinely new run starts (runId changes).
  const prevRunId = useRef(trace?.runId);
  useEffect(() => {
    if (trace?.runId && trace.runId !== prevRunId.current) {
      prevRunId.current = trace.runId;
      setBakeToggle(true);
    }
  }, [trace?.runId]);

  // WebRTC P2P screen streaming.
  const sendSignalForAgent = useCallback(
    (type: string, data: unknown) => {
      if (c.selectedId) c.sendSignal(c.selectedId, type, data);
    },
    [c],
  );
  const webrtc = useWebRTC(sendSignalForAgent, c.selectedId);

  // Wire up incoming WebRTC signals from the coordinator.
  const webrtcRef = useRef(webrtc);
  webrtcRef.current = webrtc;
  useEffect(() => {
    c.onWebRTCSignal((type, agentId, data) => {
      if (agentId === c.selectedId) {
        webrtcRef.current.handleSignal(type, data);
      }
    });
    return () => c.onWebRTCSignal(null);
  }, [c, c.selectedId]);

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

  const steer = useCallback(
    (text: string) => {
      const t = text.trim();
      if (!t || !c.selectedId) return;
      c.sendCommand(c.selectedId, "steer", { text: t, remember: true });
      setToast(`Steer sent: "${t}"`);
      setIntent("");
    },
    [c],
  );

  const newTask = useCallback(
    (text: string) => {
      const t = text.trim();
      if (!t || !c.selectedId) return;
      c.sendCommand(c.selectedId, "new_task", { text: t });
      setToast(`New task dispatched: "${t}"`);
      setIntent("");
      setHalting(false);
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
          {/* Roster — full panel or collapsed agent chip */}
          {rosterOpen ? (
            <div className="space-y-2 xl:col-span-1">
              <div className="flex items-center justify-between">
                <h2 className="text-sm font-semibold text-muted">Fleet</h2>
                {c.selectedId && (
                  <button
                    onClick={() => setRosterOpen(false)}
                    className="rounded p-0.5 text-muted hover:bg-panel2 hover:text-ink"
                    title="Collapse fleet roster"
                  >
                    <ChevronLeft size={14} />
                  </button>
                )}
              </div>
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
          ) : (
            c.selected && (
              <div className="flex items-center gap-2 xl:col-span-4">
                <button
                  onClick={() => setRosterOpen(true)}
                  className="flex items-center gap-2 rounded-lg border border-edge bg-panel/80 px-3 py-1.5 text-sm transition-colors hover:border-accent/40"
                >
                  <StatusDot
                    hex={agentStatusStyle[c.selected.status].hex}
                    pulse={c.selected.status === "running" || c.selected.status === "blocked"}
                  />
                  <span className="font-medium text-ink">{c.selected.name}</span>
                  <ChevronRight size={14} className="text-muted" />
                </button>
              </div>
            )
          )}

          {/* Detail · unified live view */}
          <div className={rosterOpen ? "xl:col-span-3" : "xl:col-span-4"}>
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
                    {/* Mode switcher */}
                    <div className="ml-2 inline-flex overflow-hidden rounded-md border border-edge text-[10px]">
                      {(["LIVE", "LOCKED", "AUTONOMOUS"] as const).map((m) => (
                        <button
                          key={m}
                          disabled={c.selected!.status === "running"}
                          onClick={() => {
                            c.sendCommand(c.selected!.id, "mode", { mode: m });
                            setToast(`Mode → ${m}`);
                          }}
                          className={[
                            "px-2 py-0.5 font-semibold transition-colors",
                            c.selected!.mode === m
                              ? "bg-accent text-white"
                              : "text-muted hover:bg-panel2 hover:text-ink",
                          ].join(" ")}
                        >
                          {m === "AUTONOMOUS" ? "AUTO" : m}
                        </button>
                      ))}
                    </div>
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
                ) : c.selected.block && c.selected.block.type !== "suspended" ? (
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
                  <LiveScreen
                    frame={c.frame}
                    agent={c.selected}
                    webrtcState={webrtc.state}
                    videoRef={webrtc.videoRef}
                    expanded={expanded}
                    onToggleExpand={() => setExpanded((v) => !v)}
                    onHalt={() => {
                      haltEvtBase.current = c.events.length;
                      setHalting(true);
                      setToast("Halt requested \u2014 agent will stop at next step boundary");
                      c.sendCommand(c.selected!.id, "halt");
                    }}
                    halting={halting}
                    onPause={() => {
                      c.sendCommand(c.selected!.id, "workflow.pause");
                      setToast("Pause requested · agent will wait at the next milestone");
                    }}
                    onResume={() => {
                      c.sendCommand(c.selected!.id, "workflow.resume");
                      setToast("Resumed · agent proceeds autonomously");
                    }}
                  />
                  <WorkflowPane
                    agent={c.selected}
                    nodeShots={c.nodeShots}
                    pickedNode={pickedNode}
                    onPickNode={(k) =>
                      setPickedNode((prev) => (prev === k ? null : k))
                    }
                  />
                </div>

                {/* Auto-promote toggle: visible for first-time autonomous tasks */}
                {trace?.known === false && !trace?.promoted && (
                  <Card className="flex items-center gap-3 border-accent/30 bg-accent/5 px-4 py-2">
                    <label className="flex cursor-pointer items-center gap-2 text-[13px] text-ink">
                      <input
                        type="checkbox"
                        checked={bakeToggle}
                        onChange={(e) => setBakeToggle(e.target.checked)}
                        className="h-4 w-4 rounded border-edge accent-accent"
                      />
                      <Sparkles size={13} className="text-accent" />
                      Bake out a new workflow from this run
                    </label>
                    <span className="ml-auto text-[11px] text-muted">
                      {trace.promoteReady
                        ? "Graph crystallized — promoting…"
                        : trace.status === "completed"
                          ? "Waiting for crystallization…"
                          : "Will promote on completion"}
                    </span>
                  </Card>
                )}
                {trace?.promoted && (
                  <Card className="flex items-center gap-2 border-ok/40 bg-ok/5 px-4 py-2 text-sm text-ok">
                    <ShieldCheck size={16} />
                    <span>
                      Baked into workflow: <span className="font-mono text-[12px]">{trace.promoted.name}</span>
                      {trace.promoted.description && (
                        <span className="ml-1 text-[11px] text-ok/70">— {trace.promoted.description}</span>
                      )}
                    </span>
                  </Card>
                )}

                {/* Dispatch bar — 3-state: idle/running/suspended */}
                <Card className="p-3">
                  {(() => {
                    const agentStatus = c.selected.status;
                    const isRunning = agentStatus === "running";
                    const isSuspended = agentStatus === "suspended";
                    const isIdle = !isRunning && !isSuspended;

                    const placeholder = isRunning
                      ? "Steer: amend the current task…"
                      : isSuspended
                        ? "Instruction before resuming…"
                        : `Describe a new task for ${c.selected.name}…`;

                    const handleEnter = () => {
                      if (!intent.trim()) return;
                      if (isRunning || isSuspended) steer(intent);
                      else send(intent);
                    };

                    const helpMessage = isSuspended
                      && c.selected.block?.type === "suspended"
                      && c.selected.block?.reason === "agent_requested_help"
                      ? c.selected.block?.helpMessage ?? null
                      : null;

                    return (
                      <>
                        {helpMessage && (
                          <div className="mb-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
                            <span className="font-semibold">Agent needs help:</span>{" "}
                            {helpMessage}
                          </div>
                        )}
                        <div className="flex items-center gap-2">
                          <Input
                            value={intent}
                            onChange={(e) => setIntent(e.target.value)}
                            onKeyDown={(e) => e.key === "Enter" && handleEnter()}
                            placeholder={helpMessage ? "Provide the info or instructions to help…" : placeholder}
                          />
                          {isIdle && (
                            <Button onClick={() => send(intent)} disabled={!intent.trim()}>
                              <Play size={15} /> New Task
                            </Button>
                          )}
                          {isRunning && (
                            <>
                              <Button onClick={() => steer(intent)} disabled={!intent.trim()}>
                                <Send size={15} /> Steer
                              </Button>
                              <Button
                                variant="danger"
                                disabled={halting}
                                onClick={() => {
                                  haltEvtBase.current = c.events.length;
                                  setHalting(true);
                                  setToast("Halt requested \u2014 agent will stop at next step boundary");
                                  c.sendCommand(c.selected!.id, "halt");
                                }}
                              >
                                {halting ? <Loader2 size={15} className="animate-spin" /> : <Hand size={15} />}
                                {halting ? "Halting\u2026" : "Halt"}
                              </Button>
                            </>
                          )}
                          {isSuspended && (
                            <Button onClick={() => steer(intent)} disabled={!intent.trim()}>
                              <Play size={15} /> Resume
                            </Button>
                          )}
                          {(isRunning || isSuspended) && (
                            <Button
                              variant="ghost"
                              onClick={() => {
                                if (intent.trim()) newTask(intent);
                                else {
                                  setToast("Type a new task first, then click New Task");
                                }
                              }}
                              title="Abandon current task and start fresh"
                            >
                              <Sparkles size={15} /> New Task
                            </Button>
                          )}
                          <MicCommandButton onTranscript={isRunning || isSuspended ? steer : send} onError={(m) => setToast(m)} />
                        </div>
                        <p className="mt-2 text-[11px] text-muted">
                          {isRunning && "Agent is running. Steer amends the current goal. Halt pauses without losing context."}
                          {isSuspended && (helpMessage
                            ? "Agent is asking for help. Provide the info or instructions it needs, then Resume."
                            : "Agent is suspended. Type a steer instruction and Resume, or start a New Task.")}
                          {isIdle && "Describe a task — routed to a saved workflow or a fresh autonomous run."}
                        </p>
                      </>
                    );
                  })()}
                  {c.selected.routing && <RoutingBanner routing={c.selected.routing} />}
                </Card>

                {/* Agent catalog (routines, workflows, task graphs) */}
                {c.selectedId && <CatalogPanel agentId={c.selectedId} />}

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

function WorkflowBadge({ agent }: { agent: RemoteAgent }) {
  const routing = agent.routing;
  const wfName = agent.workflow?.name;

  if (wfName) {
    return (
      <Badge tone="accent">
        <WorkflowIcon size={11} /> {wfName}
      </Badge>
    );
  }
  if (routing?.state === "matched" && routing.target) {
    return (
      <Badge tone="accent">
        <WorkflowIcon size={11} /> {routing.target}
      </Badge>
    );
  }
  if (routing?.state === "autonomous" || routing?.kind === "AUTONOMOUS") {
    return (
      <Badge tone="neutral">
        <Sparkles size={11} /> autonomous
      </Badge>
    );
  }
  if (agent.status === "running" || agent.status === "blocked") {
    return (
      <Badge tone="neutral">
        <Sparkles size={11} /> new task
      </Badge>
    );
  }
  return null;
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
      {/* Title — async-generated summary of what the agent is working on */}
      {agent.title && (
        <div className="mt-1 truncate text-xs font-medium text-ink/80">
          {agent.title}
        </div>
      )}
      {/* Workflow badge — which saved workflow was routed to */}
      <div className="mt-1 flex items-center gap-1.5">
        <WorkflowBadge agent={agent} />
        {!agent.title && (
          <span className="text-xs text-muted">
            {agent.workflow?.name ?? agent.routineId ?? "idle"}
          </span>
        )}
      </div>
      <Progress className="mt-2" value={agent.progress} tone={s.hex} />
      {/* Recent-steps peek — last 2-3 step descriptions */}
      {agent.recentSteps && agent.recentSteps.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {agent.recentSteps.slice(-3).map((step, i) => (
            <li key={i} className="truncate text-[11px] text-muted">
              <span className="text-accent/70">#{step.index ?? "·"}</span>{" "}
              {step.description}
            </li>
          ))}
        </ul>
      )}
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

function LiveScreen({
  frame,
  agent,
  webrtcState,
  videoRef,
  expanded,
  onToggleExpand,
  onHalt,
  onPause,
  onResume,
  halting,
}: {
  frame: string | null;
  agent: RemoteAgent;
  webrtcState: WebRTCState;
  videoRef: (el: HTMLVideoElement | null) => void;
  expanded: boolean;
  onToggleExpand: () => void;
  onHalt: () => void;
  onPause: () => void;
  onResume: () => void;
  halting: boolean;
}) {
  const isWebRTC = webrtcState === "connected";
  const s = agentStatusStyle[agent.status];

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onToggleExpand();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded, onToggleExpand]);

  return (
    <>
      <Card className="overflow-hidden">
        <div className="relative flex min-h-[320px] items-center justify-center bg-black/60">
          {!expanded && (
            <>
              {/* WebRTC video (hidden unless connected) */}
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={[
                  "max-h-[60vh] w-full object-contain",
                  isWebRTC ? "" : "hidden",
                ].join(" ")}
              />
              {/* Fallback: base64 frame relay */}
              {!isWebRTC && (
                <>
                  {frame ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={frame} alt={`${agent.name} live screen`} className="max-h-[60vh] w-full object-contain" />
                  ) : (
                    <div className="flex flex-col items-center gap-2 py-16 text-muted">
                      <Monitor size={28} />
                      <span className="text-sm">{agent.online ? "Waiting for frames…" : "Agent offline"}</span>
                    </div>
                  )}
                </>
              )}
            </>
          )}
          {expanded && (
            <span className="text-sm text-white/60">Live view expanded</span>
          )}
          <div className="absolute left-2 top-2 flex items-center gap-1 rounded-md bg-black/60 px-2 py-1 text-[11px] text-white">
            <Radio size={11} className="text-red-400" /> LIVE · {agent.mode}
            {isWebRTC && (
              <span className="ml-1 rounded bg-green-600/80 px-1 text-[10px]">P2P</span>
            )}
            {webrtcState === "connecting" && (
              <span className="ml-1 rounded bg-yellow-600/80 px-1 text-[10px]">P2P…</span>
            )}
          </div>
          <button
            onClick={onToggleExpand}
            className="absolute right-2 top-2 rounded-md bg-black/60 p-1.5 text-white/80 transition-colors hover:text-white"
            title="Expand live view"
          >
            <Maximize2 size={14} />
          </button>
        </div>
      </Card>

      {expanded &&
        createPortal(
          <div className="fixed inset-0 z-50 flex flex-col bg-black/95">
            {/* Close button */}
            <button
              onClick={onToggleExpand}
              className="absolute right-4 top-4 z-10 rounded-lg bg-white/10 p-2 text-white/80 transition-colors hover:bg-white/20 hover:text-white"
              title="Close (Esc)"
            >
              <X size={20} />
            </button>

            {/* LIVE badge */}
            <div className="absolute left-4 top-4 z-10 flex items-center gap-1 rounded-md bg-black/60 px-2 py-1 text-[11px] text-white">
              <Radio size={11} className="text-red-400" /> LIVE · {agent.mode}
              {isWebRTC && (
                <span className="ml-1 rounded bg-green-600/80 px-1 text-[10px]">P2P</span>
              )}
            </div>

            {/* Video / frame */}
            <div className="flex flex-1 items-center justify-center p-4">
              <video
                ref={videoRef}
                autoPlay
                playsInline
                muted
                className={[
                  "max-h-[85vh] max-w-full object-contain",
                  isWebRTC ? "" : "hidden",
                ].join(" ")}
              />
              {!isWebRTC && (
                <>
                  {frame ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={frame}
                      alt={`${agent.name} live screen`}
                      className="max-h-[85vh] max-w-full object-contain"
                    />
                  ) : (
                    <div className="flex flex-col items-center gap-2 py-16 text-white/50">
                      <Monitor size={40} />
                      <span className="text-base">
                        {agent.online ? "Waiting for frames…" : "Agent offline"}
                      </span>
                    </div>
                  )}
                </>
              )}
            </div>

            {/* Bottom toolbar */}
            <div className="flex items-center justify-between bg-black/60 px-6 py-3 backdrop-blur-sm">
              <div className="flex items-center gap-3">
                <StatusDot
                  hex={s.hex}
                  pulse={agent.status === "running" || agent.status === "blocked"}
                />
                <span className="text-sm font-medium text-white">{agent.name}</span>
                <Badge tone="accent">{agent.mode}</Badge>
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
                  {s.label}
                </Badge>
              </div>
              <div className="flex items-center gap-2">
                <Button variant="danger" disabled={halting} onClick={onHalt}>
                  {halting ? <Loader2 size={15} className="animate-spin" /> : <Hand size={15} />}
                  {halting ? "Halting\u2026" : "Halt"}
                </Button>
                <Button size="sm" variant="outline" onClick={onPause}>
                  <Pause size={14} /> Pause
                </Button>
                <Button size="sm" variant="outline" onClick={onResume}>
                  <Play size={14} /> Resume
                </Button>
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
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
  const trace = agent.trace;
  // Default to whichever view has live data: the saved workflow if we're
  // following one, otherwise the granular execution trace.
  const [view, setView] = useState<"workflow" | "trace">(
    wf ? "workflow" : trace ? "trace" : "workflow",
  );
  // Snap to the view that has data as presence changes (incl. agent switches,
  // since this component doesn't remount). Depend only on the presence booleans
  // so this doesn't re-run on every roster update.
  const hasWf = !!wf;
  const hasTrace = !!trace;
  useEffect(() => {
    if (hasWf && !hasTrace) setView("workflow");
    else if (hasTrace && !hasWf) setView("trace");
    else if (!hasWf && !hasTrace) setView("workflow");
  }, [hasWf, hasTrace]);

  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-edge px-3 py-2 text-xs text-muted">
        <div className="inline-flex overflow-hidden rounded-md border border-edge">
          <ViewTab
            active={view === "workflow"}
            onClick={() => setView("workflow")}
            icon={<WorkflowIcon size={12} />}
          >
            Workflow
          </ViewTab>
          <ViewTab
            active={view === "trace"}
            onClick={() => setView("trace")}
            icon={<ListTree size={12} />}
          >
            Execution trace
          </ViewTab>
        </div>
        <span className="ml-auto">
          {view === "workflow"
            ? "high-level milestones · click one to target a steer"
            : "granular live steps as the agent operates"}
        </span>
      </div>
      <div className="h-[60vh] min-h-[320px] bg-canvas">
        {view === "workflow" ? (
          wf && wf.nodes.length > 0 ? (
            <WorkflowGraph
              workflow={wf}
              nodeShots={nodeShots}
              pickedNode={pickedNode}
              onPickNode={onPickNode}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
              <WorkflowIcon size={28} />
              <span className="text-sm">Not following a saved workflow.</span>
              <span className="text-[11px]">
                This task didn&apos;t match an existing workflow — see the live
                execution trace.
              </span>
            </div>
          )
        ) : trace && trace.nodes.length > 0 ? (
          <div className="flex h-full flex-col">
            {!wf && (
              <div className="flex items-start gap-2 border-b border-edge bg-panel2 px-3 py-2 text-[12px] text-muted">
                <Sparkles size={13} className="mt-0.5 shrink-0 text-accent" />
                <span>
                  {trace.known === false
                    ? "New task — no saved workflow matched. A crystallized workflow is being recorded from this run; here is the live detailed execution trace."
                    : "Running a task without a saved workflow. Here is the live detailed execution trace."}
                </span>
              </div>
            )}
            <div className="min-h-0 flex-1">
              <TraceGraph trace={trace} nodeShots={nodeShots} />
            </div>
          </div>
        ) : (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-muted">
            <ListTree size={28} />
            <span className="text-sm">No execution trace yet.</span>
            <span className="text-[11px]">
              Granular steps appear here as the agent acts on a task.
            </span>
          </div>
        )}
      </div>
    </Card>
  );
}

function ViewTab({
  active,
  onClick,
  icon,
  children,
}: {
  active: boolean;
  onClick: () => void;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <button
      onClick={onClick}
      className={`inline-flex items-center gap-1 px-2.5 py-1 text-[12px] font-medium transition-colors ${
        active ? "bg-accent text-white" : "bg-panel text-muted hover:text-ink"
      }`}
    >
      {icon}
      {children}
    </button>
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
            placeholder="New workflow name (ex. Apply to a job — taught)"
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
        placeholder="Steer this milestone · ex. “research the projects page and fill in the summary”"
        rows={2}
      />
      <Input
        className="mt-2"
        value={scenario}
        onChange={(e) => setScenario(e.target.value)}
        placeholder="When does this apply? (the condition to remember, ex. “the projects field is empty”)"
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

// ── Catalog panel — agent's routines, workflows, task-graphs ─────────────────

function CatalogPanel({ agentId }: { agentId: string }) {
  const [open, setOpen] = useState(false);
  const [catalog, setCatalog] = useState<{
    routines: { id: string; name: string; description: string; mode: string; stepCount: number; version: number }[];
    workflows: { id: string; name: string; description?: string | null; version: number; nodes: number }[];
    task_graphs: { task_key: string; routine_id: string | null; run_count: number; node_count: number; labels: string[] }[];
    version: number;
  } | null>(null);

  useEffect(() => {
    if (!open) return;
    setCatalog(null);
    let cancelled = false;
    fetchAgentCatalog(agentId).then((c) => { if (!cancelled) setCatalog(c); });
    return () => { cancelled = true; };
  }, [agentId, open]);

  const total = catalog
    ? catalog.routines.length + catalog.workflows.length + catalog.task_graphs.length
    : 0;

  return (
    <Card className="p-0">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-2 text-sm font-medium text-muted hover:text-ink"
      >
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        Agent catalog
        <span className="ml-auto text-[11px] text-muted">
          {catalog ? `${total} items · v${catalog.version}` : "…"}
        </span>
      </button>
      {open && catalog && (
        <div className="space-y-3 border-t border-edge px-3 py-2 text-xs">
          {catalog.workflows.length > 0 && (
            <div>
              <h4 className="mb-1 font-semibold text-muted">Workflows ({catalog.workflows.length})</h4>
              <ul className="space-y-1">
                {catalog.workflows.map((w) => (
                  <li key={w.id} className="flex items-center gap-2 rounded border border-edge px-2 py-1">
                    <WorkflowIcon size={12} className="text-accent" />
                    <span className="font-medium text-ink">{w.name}</span>
                    <span className="ml-auto text-muted">v{w.version} · {w.nodes} nodes</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {catalog.routines.length > 0 && (
            <div>
              <h4 className="mb-1 font-semibold text-muted">Routines ({catalog.routines.length})</h4>
              <ul className="space-y-1">
                {catalog.routines.map((r) => (
                  <li key={r.id} className="flex items-center gap-2 rounded border border-edge px-2 py-1">
                    <GitBranch size={12} className="text-accent" />
                    <span className="font-medium text-ink">{r.name}</span>
                    <span className="ml-auto text-muted">{r.mode} · {r.stepCount} steps</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {catalog.task_graphs.length > 0 && (
            <div>
              <h4 className="mb-1 font-semibold text-muted">Task Graphs ({catalog.task_graphs.length})</h4>
              <ul className="space-y-1">
                {catalog.task_graphs.map((g) => (
                  <li key={g.task_key} className="flex items-center gap-2 rounded border border-edge px-2 py-1">
                    <ListTree size={12} className="text-accent" />
                    <span className="font-medium text-ink">{g.task_key}</span>
                    <span className="ml-auto text-muted">{g.run_count} runs · {g.node_count} nodes</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {total === 0 && (
            <p className="text-muted">No routines, workflows, or task graphs on this agent yet.</p>
          )}
        </div>
      )}
    </Card>
  );
}
