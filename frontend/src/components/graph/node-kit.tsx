"use client";

/**
 * Node Kit — composable primitives for building React Flow node types.
 *
 * Every node in the routine graph is assembled from these pieces so they share
 * one visual language (status rings, handles, screenshots, footers). Build a new
 * node type by composing <NodeFrame> with the parts you need — see
 * `components/graph/nodes.tsx` for ActionNode / TriggerNode / BranchNode / NoteNode.
 */
import * as React from "react";
import { Handle, Position } from "@xyflow/react";
import { cn } from "@/lib/utils";

// ── Tone system (shared with lib/status hexes) ──────────────────────────────
export type NodeTone = "idle" | "running" | "ok" | "flag" | "halt" | "accent";

const TONE_HEX: Record<NodeTone, string> = {
  idle: "#64748b",
  running: "#3b82f6",
  ok: "#22c55e",
  flag: "#f59e0b",
  halt: "#ef4444",
  accent: "#3b82f6",
};

export const toneHex = (t: NodeTone): string => TONE_HEX[t];

// ── Frame: the outer card + status ring + blocked pulse ─────────────────────
export interface NodeFrameProps {
  /** Colors the border ring (e.g. runtime status). Omit for the neutral edge. */
  tone?: NodeTone;
  /** Red pulsing ring — agent is blocked here. Overrides `tone`. */
  blocked?: boolean;
  selected?: boolean;
  width?: number;
  className?: string;
  children: React.ReactNode;
}

export function NodeFrame({
  tone,
  blocked,
  selected,
  width = 260,
  className,
  children,
}: NodeFrameProps) {
  const hex = tone ? TONE_HEX[tone] : undefined;
  return (
    <div
      className={cn(
        "overflow-hidden rounded-xl bg-panel text-ink shadow-lg transition-colors",
        blocked
          ? "border-2 border-halt shadow-halt animate-pulseRing"
          : tone
            ? "border-2"
            : "border border-edge",
        selected && "ring-2 ring-accent ring-offset-2 ring-offset-canvas",
        className,
      )}
      style={{ width, ...(hex && !blocked ? { borderColor: hex } : {}) }}
    >
      {children}
    </div>
  );
}

// ── Handles (connection ports) ──────────────────────────────────────────────
export type HandleSpec = "none" | "in" | "out" | "both";

export function NodeHandles({
  layout = "horizontal",
  handles = "both",
}: {
  layout?: "horizontal" | "vertical";
  handles?: HandleSpec;
}) {
  const targetPos = layout === "horizontal" ? Position.Left : Position.Top;
  const sourcePos = layout === "horizontal" ? Position.Right : Position.Bottom;
  return (
    <>
      {(handles === "in" || handles === "both") && (
        <Handle type="target" position={targetPos} />
      )}
      {(handles === "out" || handles === "both") && (
        <Handle type="source" position={sourcePos} />
      )}
    </>
  );
}

// ── Header (icon + title + optional kicker/trailing) ────────────────────────
export function NodeHeader({
  icon,
  title,
  kicker,
  trailing,
  className,
}: {
  icon?: React.ReactNode;
  title: React.ReactNode;
  kicker?: string;
  trailing?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center gap-2 px-3 py-2", className)}>
      {icon && <span className="shrink-0 text-muted">{icon}</span>}
      <div className="min-w-0 flex-1">
        {kicker && (
          <div className="font-mono text-[10px] uppercase tracking-wide text-muted">
            {kicker}
          </div>
        )}
        <div className="truncate text-sm font-medium">{title}</div>
      </div>
      {trailing && <div className="shrink-0">{trailing}</div>}
    </div>
  );
}

// ── Screenshot region with index badge + overlay slot ───────────────────────
export function NodeScreenshot({
  src,
  alt,
  index,
  height = 120,
  overlay,
  topRight,
}: {
  src?: string;
  alt?: string;
  index?: number;
  height?: number;
  overlay?: React.ReactNode;
  topRight?: React.ReactNode;
}) {
  return (
    <div className="relative w-full bg-panel2" style={{ height }}>
      {src ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={src} alt={alt ?? ""} className="h-full w-full object-cover" />
      ) : (
        <div className="flex h-full items-center justify-center text-[11px] text-muted">
          no preview
        </div>
      )}
      {index != null && (
        <div className="absolute left-2 top-2 flex h-6 w-6 items-center justify-center rounded-md bg-canvas/80 text-[11px] font-semibold text-ink">
          {index}
        </div>
      )}
      {topRight && <div className="absolute right-2 top-2">{topRight}</div>}
      {overlay}
    </div>
  );
}

// ── Body wrapper ─────────────────────────────────────────────────────────────
export function NodeBody({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={cn("space-y-1.5 p-3", className)}>{children}</div>;
}

// ── Pill (small status/label chip used inside nodes) ────────────────────────
export function NodePill({
  tone = "neutral",
  icon,
  children,
  filled,
}: {
  tone?: "neutral" | NodeTone;
  icon?: React.ReactNode;
  children: React.ReactNode;
  filled?: boolean;
}) {
  const hex = tone === "neutral" ? "#8b97a8" : TONE_HEX[tone];
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10px] font-medium"
      style={
        filled
          ? { color: "#fff", backgroundColor: hex }
          : { color: hex, backgroundColor: `${hex}1a` }
      }
    >
      {icon}
      {children}
    </span>
  );
}

// ── Footer strip (agent presence, etc.) ─────────────────────────────────────
export function NodeFooter({
  tone = "accent",
  icon,
  children,
}: {
  tone?: NodeTone;
  icon?: React.ReactNode;
  children: React.ReactNode;
}) {
  const hex = TONE_HEX[tone];
  return (
    <div
      className="flex items-center gap-1.5 border-t px-3 py-1.5 text-[11px]"
      style={{ borderColor: `${hex}66`, backgroundColor: `${hex}1a`, color: hex }}
    >
      {icon ?? <span className="h-1.5 w-1.5 rounded-full bg-current" />}
      {children}
    </div>
  );
}

// ── Blocked overlay (centered banner over the screenshot) ───────────────────
export function NodeBlockedOverlay({ label = "Blocked" }: { label?: string }) {
  return (
    <div className="absolute inset-0 flex items-center justify-center bg-halt/15">
      <div className="flex items-center gap-1.5 rounded-md bg-halt px-2 py-1 text-[11px] font-semibold text-white">
        {label}
      </div>
    </div>
  );
}
