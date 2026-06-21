"use client";

import { type NodeProps } from "@xyflow/react";
import {
  MousePointerClick,
  Keyboard,
  Type as TypeIcon,
  Globe,
  AppWindow,
  Timer,
  Move,
  ScrollText,
  ShieldAlert,
  ClipboardList,
  type LucideIcon,
} from "lucide-react";
import type { ActionType, StepStatus } from "@/lib/types";
import { stepStatusStyle } from "@/lib/status";
import {
  NodeBlockedOverlay,
  NodeBody,
  NodeFooter,
  NodeFrame,
  NodeHandles,
  NodePill,
  NodeScreenshot,
  type NodeTone,
} from "./node-kit";

export interface StepNodeData {
  index: number;
  action: ActionType;
  title: string;
  instruction?: string;
  screenshotUrl?: string;
  highStakes?: boolean;
  monitorTrigger?: string;
  reliability?: number;
  /** Runtime status when rendered in replay mode. */
  runStatus?: StepStatus;
  /** Name of an agent currently sitting on this node. */
  agentHere?: string;
  /** True when an agent is blocked here (red pulsing ring + marker). */
  blocked?: boolean;
  [key: string]: unknown;
}

export const ACTION_ICON: Record<ActionType, LucideIcon> = {
  click: MousePointerClick,
  double_click: MousePointerClick,
  hotkey: Keyboard,
  type: TypeIcon,
  browser: Globe,
  navigate: Globe,
  open_app: AppWindow,
  wait: Timer,
  move: Move,
  scroll: ScrollText,
  batch_fill: ClipboardList,
};

/** Maps a runtime step status onto a node ring tone. */
export function stepTone(status?: StepStatus): NodeTone | undefined {
  switch (status) {
    case "completed":
      return "ok";
    case "running":
      return "running";
    case "failed":
    case "halted":
      return "halt";
    case "flagged":
    case "deviated":
    case "awaiting_human":
      return "flag";
    default:
      return undefined;
  }
}

/**
 * ActionNode · the workhorse routine-step node. Composed entirely from the
 * node-kit primitives so it stays consistent with other node types.
 */
export function StepNode({ data, selected }: NodeProps) {
  const d = data as StepNodeData;
  const Icon = ACTION_ICON[d.action] ?? MousePointerClick;
  const status = d.runStatus ? stepStatusStyle[d.runStatus] : null;
  const tone = d.blocked ? undefined : stepTone(d.runStatus);

  return (
    <NodeFrame tone={tone} blocked={d.blocked} selected={selected}>
      <NodeHandles layout="horizontal" handles="both" />

      <NodeScreenshot
        src={d.screenshotUrl}
        alt={d.title}
        index={d.index}
        topRight={
          d.highStakes ? (
            <NodePill tone="halt" filled icon={<ShieldAlert size={11} />}>
              high-stakes
            </NodePill>
          ) : undefined
        }
        overlay={d.blocked ? <NodeBlockedOverlay /> : undefined}
      />

      <NodeBody>
        <div className="flex items-center gap-2">
          <span className="text-muted">
            <Icon size={14} />
          </span>
          <span className="truncate text-sm font-medium">{d.title}</span>
        </div>
        {d.instruction && (
          <p className="line-clamp-2 text-[11px] leading-snug text-muted">
            {d.instruction}
          </p>
        )}
        <div className="flex items-center justify-between pt-0.5">
          <span className="font-mono text-[10px] uppercase tracking-wide text-muted">
            {d.action}
          </span>
          {status ? (
            <NodePill tone={stepTone(d.runStatus) ?? "idle"}>{status.label}</NodePill>
          ) : d.reliability != null ? (
            <span className="text-[10px] text-muted">
              {Math.round(d.reliability * 100)}% ok
            </span>
          ) : null}
        </div>
      </NodeBody>

      {d.agentHere && (
        <NodeFooter tone={d.blocked ? "halt" : "accent"}>
          {d.agentHere} is here
        </NodeFooter>
      )}
    </NodeFrame>
  );
}

/** Alias · the step node is our generic "action" node type. */
export { StepNode as ActionNode };
