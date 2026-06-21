"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import type { TaskGraph } from "@/lib/types";

const KIND_COLOR: Record<string, string> = {
  open: "#3b82f6",
  navigate: "#06b6d4",
  search: "#8b5cf6",
  research: "#f59e0b",
  scan: "#64748b",
  fill: "#22c55e",
  submit: "#ec4899",
  verify: "#eab308",
  interact: "#94a3b8",
};

interface MilestoneData {
  label: string;
  kind: string;
  value: string | null;
  timesSeen: number;
  lastStatus: string | null;
}

function MilestoneNode(props: NodeProps) {
  const data = props.data as unknown as MilestoneData;
  const color = KIND_COLOR[data.kind] ?? "#94a3b8";
  return (
    <div
      className="rounded-xl border bg-panel px-4 py-3 shadow-lg"
      style={{ borderColor: color, minWidth: 210 }}
    >
      <Handle type="target" position={Position.Left} className="!bg-edge" />
      <div className="flex items-center gap-2">
        <span
          className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
          style={{ background: `${color}22`, color }}
        >
          {data.kind}
        </span>
        {data.timesSeen > 1 && (
          <span className="ml-auto text-[10px] text-muted">seen {data.timesSeen}×</span>
        )}
      </div>
      <div className="mt-1.5 text-sm font-medium text-ink">{data.label}</div>
      {data.value && (
        <div className="mt-0.5 max-w-[190px] truncate text-[11px] text-muted">{data.value}</div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-edge" />
    </div>
  );
}

const nodeTypes = { milestone: MilestoneNode };

/**
 * Longest-path layering for a (mostly-acyclic) milestone graph: x = depth,
 * siblings on the same depth stack vertically. Falls back to array order for
 * any node a cycle leaves unreached.
 */
function layout(graph: TaskGraph): Map<string, { x: number; y: number }> {
  const COL = 300;
  const ROW = 150;
  const keys = graph.nodes.map((n) => n.key);
  const adj = new Map<string, string[]>();
  const indeg = new Map<string, number>();
  keys.forEach((k) => {
    adj.set(k, []);
    indeg.set(k, 0);
  });
  for (const e of graph.edges) {
    if (adj.has(e.from) && indeg.has(e.to)) {
      adj.get(e.from)!.push(e.to);
      indeg.set(e.to, (indeg.get(e.to) ?? 0) + 1);
    }
  }

  const level = new Map<string, number>();
  keys.forEach((k) => level.set(k, 0));
  const work = new Map(indeg);
  const queue = keys.filter((k) => (indeg.get(k) ?? 0) === 0);
  const seen = new Set(queue);
  while (queue.length) {
    const k = queue.shift()!;
    for (const to of adj.get(k) ?? []) {
      level.set(to, Math.max(level.get(to) ?? 0, (level.get(k) ?? 0) + 1));
      work.set(to, (work.get(to) ?? 0) - 1);
      if ((work.get(to) ?? 0) <= 0 && !seen.has(to)) {
        seen.add(to);
        queue.push(to);
      }
    }
  }
  // Any node a cycle left unreached: lay it out by its array index column.
  graph.nodes.forEach((n, i) => {
    if (!seen.has(n.key)) level.set(n.key, i);
  });

  const byLevel = new Map<number, string[]>();
  for (const k of keys) {
    const l = level.get(k) ?? 0;
    (byLevel.get(l) ?? byLevel.set(l, []).get(l)!).push(k);
  }
  const pos = new Map<string, { x: number; y: number }>();
  byLevel.forEach((group, l) => {
    group.forEach((k, i) => {
      pos.set(k, { x: l * COL, y: i * ROW - ((group.length - 1) * ROW) / 2 });
    });
  });
  return pos;
}

export function TaskGraphView({ graph }: { graph: TaskGraph }) {
  const { nodes, edges } = useMemo(() => {
    const pos = layout(graph);
    const nodes: Node[] = graph.nodes.map((n, i) => ({
      id: n.key,
      type: "milestone",
      position: pos.get(n.key) ?? { x: i * 300, y: 0 },
      data: {
        label: n.label,
        kind: n.kind,
        value: n.value,
        timesSeen: n.times_seen,
        lastStatus: n.last_status,
      },
    }));
    const edges: Edge[] = graph.edges.map((e, i) => ({
      id: `e${i}`,
      source: e.from,
      target: e.to,
      type: "smoothstep",
      animated: true,
      label: e.times_seen > 1 ? `${e.times_seen}×` : undefined,
      style: { strokeWidth: Math.min(1 + e.times_seen, 4), stroke: "#3b82f6" },
    }));
    return { nodes, edges };
  }, [graph]);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.2}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="#1b2230" />
        <Controls
          showInteractive={false}
          className="overflow-hidden rounded-lg border border-edge bg-panel text-ink"
        />
      </ReactFlow>
    </div>
  );
}
