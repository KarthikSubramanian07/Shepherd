"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Cpu,
  Hand,
  KeyRound,
  Monitor,
  Radio,
  Send,
  ShieldCheck,
  WifiOff,
} from "lucide-react";
import {
  type RemoteAgent,
  type RemoteEvent,
  useCoordinator,
} from "@/lib/coordinator";
import { agentStatusStyle } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { PageHeader } from "@/components/layout/PageHeader";
import { MicCommandButton } from "@/components/remote/MicCommandButton";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Input,
  Progress,
  Stat,
  StatusDot,
  Tabs,
} from "@/components/ui/primitives";

export default function RemoteCommandCenterPage() {
  const c = useCoordinator();
  const [tab, setTab] = useState("screen");
  const [intent, setIntent] = useState("");
  const [toast, setToast] = useState<string | null>(null);

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

  return (
    <div>
      <PageHeader
        title="Remote Command Center"
        subtitle="Observe every operated machine, watch its live screen, and steer or intervene from anywhere."
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
          <Stat label="Coordinator" value={c.conn === "open" ? "Linked" : "—"} />
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          {/* Roster */}
          <div className="space-y-2 lg:col-span-1">
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
                  onClick={() => c.watch(a.id)}
                />
              ))
            )}
          </div>

          {/* Detail */}
          <div className="lg:col-span-2">
            {!c.selected ? (
              <EmptyState
                icon={<Monitor size={20} />}
                title="Select an agent"
                description="Pick a machine from the fleet to view its live screen and take control."
              />
            ) : (
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <StatusDot
                      hex={agentStatusStyle[c.selected.status].hex}
                      pulse={c.selected.status === "running" || c.selected.status === "blocked"}
                    />
                    <span className="font-medium text-ink">{c.selected.name}</span>
                    <span className="text-xs text-muted">{c.selected.host}</span>
                  </div>
                  <Tabs
                    value={tab}
                    onValueChange={setTab}
                    items={[
                      { value: "screen", label: "Live screen" },
                      { value: "activity", label: "Activity" },
                    ]}
                  />
                </div>

                {c.selected.block && (
                  <InterventionBanner
                    agent={c.selected}
                    onApprove={() => c.sendCommand(c.selected!.id, "approve")}
                    onHalt={() => c.sendCommand(c.selected!.id, "halt")}
                    onOverride={(instruction) =>
                      c.sendCommand(c.selected!.id, "override", { instruction })
                    }
                  />
                )}

                {tab === "screen" ? (
                  <LiveScreen frame={c.frame} agent={c.selected} />
                ) : (
                  <ActivityFeed events={c.events} />
                )}

                {/* Command bar */}
                <Card className="p-3">
                  <div className="flex items-center gap-2">
                    <Input
                      value={intent}
                      onChange={(e) => setIntent(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && send(intent)}
                      placeholder={`Type a command for ${c.selected.name}… (e.g. "fill form")`}
                    />
                    <Button onClick={() => send(intent)} disabled={!intent.trim()}>
                      <Send size={15} /> Send
                    </Button>
                    <MicCommandButton
                      onTranscript={send}
                      onError={(m) => setToast(m)}
                    />
                    <Button
                      variant="danger"
                      onClick={() => c.sendCommand(c.selected!.id, "halt")}
                    >
                      <Hand size={15} /> Halt
                    </Button>
                  </div>
                  <p className="mt-2 text-[11px] text-muted">
                    Typed or spoken commands start/steer the agent. Mid-run, Halt
                    stops it at the next safe step boundary.
                  </p>
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

function SessionCode({
  code,
  onSubmit,
}: {
  code: string;
  onSubmit: (code: string) => void;
}) {
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
      <div className="mt-1 text-xs text-muted">{agent.routineId ?? "idle"}</div>
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
          step {agent.currentStepIndex ?? "—"} · {timeAgo(agent.lastActivityAt)}
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
            <span className="text-sm">
              {agent.online ? "Waiting for frames…" : "Agent offline"}
            </span>
          </div>
        )}
        <div className="absolute left-2 top-2 flex items-center gap-1 rounded-md bg-black/60 px-2 py-1 text-[11px] text-white">
          <Radio size={11} className="text-red-400" /> LIVE · {agent.mode}
        </div>
      </div>
    </Card>
  );
}

function ActivityFeed({ events }: { events: RemoteEvent[] }) {
  const recent = [...events].slice(-120).reverse();
  return (
    <Card className="max-h-[60vh] overflow-auto p-3">
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
    </Card>
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
    case "step.deviation":
      return `${(d.reason as string) ?? "deviation"}`;
    case "remote.intent":
      return `“${d.text as string}”`;
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
          Step {b.stepIndex ?? "?"} needs you — {b.trigger ?? b.verdict}
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
