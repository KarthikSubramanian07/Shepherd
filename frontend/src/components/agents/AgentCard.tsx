import Link from "next/link";
import { Cpu, MapPin } from "lucide-react";
import type { Agent } from "@/lib/types";
import { agentStatusStyle } from "@/lib/status";
import { timeAgo } from "@/lib/utils";
import { Badge, Card, StatusDot } from "@/components/ui/primitives";

export function AgentCard({ agent }: { agent: Agent }) {
  const s = agentStatusStyle[agent.status];
  const href = agent.runId ? `/runs/${agent.runId}` : `/routines/${agent.routineId}`;

  return (
    <Link href={href}>
      <Card className="p-4 transition-colors hover:border-accent/50">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <StatusDot hex={s.hex} pulse={agent.status === "running" || agent.status === "blocked"} />
            <span className="font-medium">{agent.name}</span>
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
            {s.label}
          </Badge>
        </div>

        <div className="mt-2 text-sm text-muted">{agent.routineName}</div>

        <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-panel2">
          <div
            className="h-full rounded-full"
            style={{
              width: `${Math.round(agent.progress * 100)}%`,
              backgroundColor: s.hex,
            }}
          />
        </div>

        {agent.block && (
          <div className="mt-3 rounded-lg border border-halt/30 bg-halt/10 px-2.5 py-1.5 text-[11px] text-halt">
            {agent.block.reason}
          </div>
        )}

        <div className="mt-3 flex items-center justify-between text-[11px] text-muted">
          <span className="flex items-center gap-1">
            <Cpu size={12} /> {agent.host}
          </span>
          <span className="flex items-center gap-1">
            <MapPin size={12} /> step {agent.currentStepIndex ?? "—"} · {timeAgo(agent.lastActivityAt)}
          </span>
        </div>
      </Card>
    </Link>
  );
}
