"use client";

/**
 * Node type registry for the routine graph.
 *
 * Each variant is composed from the node-kit primitives. Register a new node
 * type here and it becomes available to React Flow everywhere via `nodeTypes`.
 */
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Clock, GitFork, Keyboard, Mic, StickyNote, Zap } from "lucide-react";
import {
  NodeBody,
  NodeFrame,
  NodeHandles,
  NodeHeader,
  NodePill,
} from "./node-kit";
import { StepNode } from "./StepNode";

// ── Trigger node (routine entry point) ──────────────────────────────────────
export interface TriggerNodeData {
  label: string;
  source?: "voice" | "typed" | "schedule" | "event";
  description?: string;
  [key: string]: unknown;
}

const TRIGGER_ICON = {
  voice: Mic,
  typed: Keyboard,
  schedule: Clock,
  event: Zap,
} as const;

export function TriggerNode({ data, selected }: NodeProps) {
  const d = data as TriggerNodeData;
  const Icon = TRIGGER_ICON[d.source ?? "event"];
  return (
    <NodeFrame tone="accent" selected={selected} width={220}>
      <NodeHandles layout="horizontal" handles="out" />
      <NodeHeader
        icon={<Icon size={15} />}
        kicker="trigger"
        title={d.label}
        trailing={<NodePill tone="accent">start</NodePill>}
      />
      {d.description && (
        <NodeBody className="pt-0">
          <p className="text-[11px] text-muted">{d.description}</p>
        </NodeBody>
      )}
    </NodeFrame>
  );
}

// ── Branch node (conditional fork) ──────────────────────────────────────────
export interface BranchNodeData {
  label: string;
  yes?: string;
  no?: string;
  [key: string]: unknown;
}

export function BranchNode({ data, selected }: NodeProps) {
  const d = data as BranchNodeData;
  return (
    <NodeFrame tone="flag" selected={selected} width={220}>
      <Handle type="target" position={Position.Left} />
      <NodeHeader icon={<GitFork size={15} />} kicker="branch" title={d.label} />
      <NodeBody className="pt-0">
        <div className="flex items-center justify-between">
          <NodePill tone="ok">{d.yes ?? "yes"}</NodePill>
          <NodePill tone="halt">{d.no ?? "no"}</NodePill>
        </div>
      </NodeBody>
      <Handle id="yes" type="source" position={Position.Right} style={{ top: "62%" }} />
      <Handle id="no" type="source" position={Position.Bottom} />
    </NodeFrame>
  );
}

// ── Note node (sticky annotation, no flow connections) ──────────────────────
export interface NoteNodeData {
  text: string;
  [key: string]: unknown;
}

export function NoteNode({ data }: NodeProps) {
  const d = data as NoteNodeData;
  return (
    <div className="w-[200px] rounded-lg border border-flag/40 bg-flag/10 p-3 shadow-lg">
      <div className="mb-1 flex items-center gap-1 text-[11px] font-semibold text-flag">
        <StickyNote size={12} /> Note
      </div>
      <p className="text-[11px] leading-snug text-ink/80">{d.text}</p>
    </div>
  );
}

// ── Registry passed to <ReactFlow nodeTypes={...}> ──────────────────────────
export const nodeTypes = {
  step: StepNode,
  action: StepNode,
  trigger: TriggerNode,
  branch: BranchNode,
  note: NoteNode,
};
