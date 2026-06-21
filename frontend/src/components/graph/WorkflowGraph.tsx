"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { GitBranch, Sparkles } from "lucide-react";
import type { RemoteWorkflow, RemoteWorkflowNode } from "@/lib/coordinator";
import {
  NodeBody,
  NodeFrame,
  NodeHandles,
  NodePill,
  NodeScreenshot,
  type NodeTone,
} from "./node-kit";

export interface WorkflowNodeData {
  nodeKey: string;
  label: string;
  kind?: string;
  instruction?: string;
  status?: RemoteWorkflowNode["status"];
  screenshotUrl?: string;
  conditionals?: { when: string; do: string }[];
  taught?: boolean;
  current?: boolean;
  awaiting?: boolean;
  index: number;
  onPick?: (key: string) => void;
  picked?: boolean;
  [key: string]: unknown;
}

function statusTone(status?: RemoteWorkflowNode["status"]): NodeTone | undefined {
  switch (status) {
    case "done":
      return "ok";
    case "running":
      return "running";
    case "blocked":
      return "halt";
    case "awaiting":
      return "flag";
    default:
      return undefined;
  }
}

const STATUS_LABEL: Record<NonNullable<RemoteWorkflowNode["status"]>, string> = {
  pending: "pending",
  running: "running",
  done: "done",
  blocked: "blocked",
  awaiting: "awaiting you",
};

function WorkflowNode({ data, selected }: NodeProps) {
  const d = data as WorkflowNodeData;
  const tone = statusTone(d.status);
  return (
    <div
      onClick={() => d.onPick?.(d.nodeKey)}
      className="cursor-pointer"
      title="Click to target an intervention at this milestone"
    >
      <NodeFrame
        tone={tone}
        blocked={d.awaiting}
        selected={selected || d.picked}
      >
        <NodeHandles layout="horizontal" handles="both" />
        <NodeScreenshot
          src={d.screenshotUrl}
          alt={d.label}
          index={d.index}
          topRight={
            d.taught ? (
              <NodePill tone="accent" filled icon={<Sparkles size={11} />}>
                taught
              </NodePill>
            ) : undefined
          }
        />
        <NodeBody>
          <div className="flex items-center justify-between gap-2">
            <span className="truncate text-sm font-medium">{d.label}</span>
            {d.status && (
              <NodePill tone={tone ?? "idle"}>{STATUS_LABEL[d.status]}</NodePill>
            )}
          </div>
          {d.instruction && (
            <p className="line-clamp-2 text-[11px] leading-snug text-muted">
              {d.instruction}
            </p>
          )}
          {d.conditionals && d.conditionals.length > 0 && (
            <div className="space-y-0.5 pt-0.5">
              {d.conditionals.map((c, i) => (
                <div
                  key={i}
                  className="flex items-start gap-1 text-[10px] leading-snug text-accent"
                >
                  <GitBranch size={11} className="mt-0.5 shrink-0" />
                  <span className="line-clamp-2">
                    if {c.when} → {c.do}
                  </span>
                </div>
              ))}
            </div>
          )}
        </NodeBody>
      </NodeFrame>
    </div>
  );
}

const workflowNodeTypes = { workflow: WorkflowNode };

interface WorkflowGraphProps {
  workflow: RemoteWorkflow;
  nodeShots: Record<string, string>;
  pickedNode?: string | null;
  onPickNode?: (key: string) => void;
}

/**
 * Live, on-the-fly workflow graph for a watched agent. Built entirely from the
 * coordinator's workflow.* event stream: nodes carry the screenshot captured at
 * each milestone, the current node is highlighted, and conditional/taught
 * branches are rendered. Clicking a node targets an intervention there.
 */
export function WorkflowGraph({
  workflow,
  nodeShots,
  pickedNode,
  onPickNode,
}: WorkflowGraphProps) {
  const { nodes, edges } = useMemo(() => {
    const order = workflow.nodes;
    const indexByKey = new Map(order.map((n, i) => [n.key, i]));

    const nodes: Node<WorkflowNodeData>[] = order.map((n, i) => ({
      id: n.key,
      type: "workflow",
      position: { x: i * 300, y: (i % 2) * 90 },
      data: {
        nodeKey: n.key,
        label: n.label ?? n.key,
        kind: n.kind,
        instruction: n.instruction,
        status: workflow.current === n.key && n.status !== "done" && n.status !== "awaiting"
          ? "running"
          : n.status,
        screenshotUrl: nodeShots[n.key],
        conditionals: (n.conditionals ?? []).map((c) => ({ when: c.when, do: c.do })),
        taught: (n.conditionals ?? []).length > 0,
        current: workflow.current === n.key,
        awaiting: workflow.current === n.key && (workflow.awaiting || n.status === "awaiting"),
        index: i,
        picked: pickedNode === n.key,
        onPick: onPickNode,
      },
    }));

    const seen = new Set<string>();
    const edges: Edge[] = [];
    for (const e of workflow.edges) {
      if (!indexByKey.has(e.from) || !indexByKey.has(e.to)) continue;
      const id = `${e.from}->${e.to}`;
      if (seen.has(id)) continue;
      seen.add(id);
      edges.push({
        id,
        source: e.from,
        target: e.to,
        type: "smoothstep",
        animated: workflow.current === e.from,
        label: e.when ?? undefined,
        style: e.when ? { stroke: "#3b82f6", strokeDasharray: "5 4" } : undefined,
      });
    }

    return { nodes, edges };
  }, [workflow, nodeShots, pickedNode, onPickNode]);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={workflowNodeTypes}
        fitView
        minZoom={0.15}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="#ddd5c8" />
        <Controls
          showInteractive={false}
          className="overflow-hidden rounded-lg border border-edge bg-panel text-ink"
        />
      </ReactFlow>
    </div>
  );
}
