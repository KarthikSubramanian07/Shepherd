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
import type { RemoteTrace, RemoteTraceNode } from "@/lib/coordinator";
import {
  NodeBody,
  NodeFrame,
  NodeHandles,
  NodePill,
  NodeScreenshot,
  type NodeTone,
} from "./node-kit";

interface TraceNodeData {
  label: string;
  action?: string;
  status?: RemoteTraceNode["status"];
  screenshotUrl?: string;
  durationMs?: number;
  note?: string;
  error?: string;
  index: number;
  current?: boolean;
  [key: string]: unknown;
}

function statusTone(status?: RemoteTraceNode["status"]): NodeTone | undefined {
  switch (status) {
    case "completed":
      return "ok";
    case "running":
      return "running";
    case "failed":
    case "error":
      return "halt";
    default:
      return undefined;
  }
}

function TraceNode({ data, selected }: NodeProps) {
  const d = data as TraceNodeData;
  const tone = statusTone(d.status);
  return (
    <NodeFrame tone={tone} selected={selected}>
      <NodeHandles layout="horizontal" handles="both" />
      <NodeScreenshot src={d.screenshotUrl} alt={d.label} index={d.index} />
      <NodeBody>
        <div className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium">
            {d.action ?? "step"}
          </span>
          {d.status && (
            <NodePill tone={tone ?? "idle"}>{d.status}</NodePill>
          )}
        </div>
        {d.label && (
          <p className="line-clamp-2 text-[11px] leading-snug text-muted">
            {d.label}
          </p>
        )}
        {d.error && (
          <p className="line-clamp-2 text-[10px] leading-snug text-red-500">
            {d.error}
          </p>
        )}
        {typeof d.durationMs === "number" && (
          <p className="text-[10px] text-muted">{d.durationMs}ms</p>
        )}
      </NodeBody>
    </NodeFrame>
  );
}

const traceNodeTypes = { trace: TraceNode };

interface TraceGraphProps {
  trace: RemoteTrace;
  /** Per-step screenshots from the live frame stream, keyed `trace:${index}`. */
  nodeShots: Record<string, string>;
}

/**
 * Live, on-the-fly execution-trace graph for a watched agent that is NOT
 * following a saved workflow (an autonomous goal or routine). Built from the
 * coordinator's step.* stream: one node per agent action, the current step
 * highlighted, each completed step carrying the screenshot captured then.
 */
export function TraceGraph({ trace, nodeShots }: TraceGraphProps) {
  const { nodes, edges } = useMemo(() => {
    const order = trace.nodes;
    const nodes: Node<TraceNodeData>[] = order.map((n, i) => ({
      id: String(n.index),
      type: "trace",
      position: { x: i * 360, y: (i % 2) * 120 },
      data: {
        label: n.description ?? n.thinking ?? "",
        action: n.action,
        status:
          trace.current === n.index && n.status !== "completed"
            ? "running"
            : n.status,
        screenshotUrl: nodeShots[`trace:${n.index}`],
        durationMs: n.durationMs,
        note: n.note,
        error: n.error,
        index: i,
        current: trace.current === n.index,
      },
    }));

    const edges: Edge[] = [];
    for (let i = 1; i < order.length; i++) {
      const from = String(order[i - 1].index);
      const to = String(order[i].index);
      edges.push({
        id: `${from}->${to}`,
        source: from,
        target: to,
        type: "smoothstep",
        animated: trace.current === order[i - 1].index,
      });
    }

    return { nodes, edges };
  }, [trace, nodeShots]);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={traceNodeTypes}
        fitView
        fitViewOptions={{ padding: 0.2 }}
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
