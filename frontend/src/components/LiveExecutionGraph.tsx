"use client";

import { useEffect, useRef } from "react";
import {
  AppWindow,
  Compass,
  Search,
  BookOpen,
  ScanLine,
  PencilLine,
  Send,
  ShieldCheck,
  MousePointerClick,
  Check,
  AlertTriangle,
  OctagonX,
  type LucideIcon,
} from "lucide-react";
import type { LiveGraphNode, LiveNodeStatus } from "@/lib/shepherd-ws";
import { cn } from "@/lib/utils";

/**
 * The live execution path · the agent's run replays here node-by-node as the
 * WebSocket stream arrives. Milestones are waypoints the agent is herded
 * through; the active one glows, completed ones are solid, recalled-from-memory
 * milestones carry a memory mark so you can see what the flock already knows.
 *
 * This is the "live view" of the run · distinct from the static /task-graph DAG.
 */
const KIND_ICON: Record<string, LucideIcon> = {
  open: AppWindow,
  navigate: Compass,
  search: Search,
  research: BookOpen,
  scan: ScanLine,
  fill: PencilLine,
  submit: Send,
  verify: ShieldCheck,
  interact: MousePointerClick,
};

// Earthy, deepened hues · legible on the wool off-white ground and cohesive with
// the Daybreak palette (no pale dashboard rainbow).
const KIND_COLOR: Record<string, string> = {
  open: "#2f6f9e",      // deep sky
  navigate: "#2a8f8a",  // teal
  search: "#6d5bb8",    // muted violet
  research: "#9a6a2f",  // ochre
  scan: "#7c7064",      // taupe
  fill: "#1f8a5b",      // meadow green
  submit: "#b23a6b",    // deep rose
  verify: "#9a7d1a",    // deep gold
  interact: "#7a5c44",  // bark
};

function statusRing(status: LiveNodeStatus, kind: string): string {
  switch (status) {
    case "running":
      return KIND_COLOR[kind] ?? "#cf6a43";
    case "done":
      return "#2e8b57"; // ok
    case "flagged":
      return "#cf6a43"; // lantern (terracotta)
    case "halted":
      return "#c0463c"; // halt
    default:
      return "#a8997f"; // pending · warm taupe, ≥3:1 on the peach ground
  }
}

function Waypoint({ node, active }: { node: LiveGraphNode; active: boolean }) {
  const Icon = KIND_ICON[node.kind] ?? MousePointerClick;
  const ring = statusRing(node.status, node.kind);
  const dim = node.status === "pending";

  return (
    <div className="flex w-[132px] shrink-0 flex-col items-center gap-2">
      {/* Node medallion */}
      <div className="relative">
        {active && (
          <span
            className="absolute -inset-1.5 rounded-2xl animate-pulseRing"
            style={{ boxShadow: `0 0 0 2px ${ring}, 0 0 26px ${ring}66` }}
          />
        )}
        <div
          className={cn(
            "relative flex h-14 w-14 items-center justify-center rounded-2xl border-2 transition-all duration-500",
            dim ? "bg-panel" : "bg-panel2",
          )}
          style={{
            borderColor: ring,
            opacity: dim ? 0.72 : 1,
            color: ring,
          }}
        >
          {node.status === "done" ? (
            <Check size={22} strokeWidth={2.5} />
          ) : node.status === "halted" ? (
            <OctagonX size={22} />
          ) : node.status === "flagged" ? (
            <AlertTriangle size={22} />
          ) : (
            <Icon size={20} />
          )}

          {/* Memory mark · this milestone was recalled from prior runs */}
          {node.known && (
            <span
              className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full border border-canvas bg-accent text-[9px] font-bold text-white"
              title="Recalled from memory · the agent has done this before"
            >
              ↺
            </span>
          )}
        </div>
      </div>

      {/* Label */}
      <div
        className={cn(
          "text-center text-[11px] leading-tight transition-colors",
          dim ? "text-muted" : "text-ink",
        )}
      >
        {node.label}
      </div>
      <div
        className="text-[9px] font-semibold uppercase tracking-wide"
        style={{ color: KIND_COLOR[node.kind] ?? "#94a3b8", opacity: dim ? 0.5 : 0.9 }}
      >
        {node.kind}
      </div>
    </div>
  );
}

function Connector({ filled }: { filled: boolean }) {
  return (
    <div className="relative mt-7 h-0.5 w-8 shrink-0 overflow-hidden rounded-full bg-edge">
      <div
        className={cn(
          "absolute inset-y-0 left-0 rounded-full bg-accent transition-all duration-700",
          filled ? "w-full" : "w-0",
        )}
      />
    </div>
  );
}

export function LiveExecutionGraph({
  nodes,
}: {
  nodes: LiveGraphNode[];
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const activeIndex = nodes.findIndex((n) => n.status === "running");

  // Keep the active waypoint in view as the run advances.
  useEffect(() => {
    if (activeIndex < 0 || !scrollRef.current) return;
    const el = scrollRef.current.querySelector<HTMLElement>(
      `[data-wp="${activeIndex}"]`,
    );
    el?.scrollIntoView({ behavior: "smooth", inline: "center", block: "nearest" });
  }, [activeIndex]);

  if (nodes.length === 0) return null;

  return (
    <div
      ref={scrollRef}
      className="flex items-start overflow-x-auto pb-2"
      style={{ scrollbarWidth: "thin" }}
    >
      {nodes.map((n, i) => (
        <div key={n.key} data-wp={i} className="flex items-start">
          <Waypoint node={n} active={n.status === "running"} />
          {i < nodes.length - 1 && (
            <Connector
              filled={n.status === "done" || n.status === "flagged"}
            />
          )}
        </div>
      ))}
    </div>
  );
}
