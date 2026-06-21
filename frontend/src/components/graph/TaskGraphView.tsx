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
import type { Conditional, TaskGraph } from "@/lib/types";

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
  taught: boolean;
  procedure: string | null;
  conditionals: Conditional[];
}

const TAUGHT_COLOR = "#a855f7";

function MilestoneNode(props: NodeProps) {
  const data = props.data as unknown as MilestoneData;
  const color = KIND_COLOR[data.kind] ?? "#94a3b8";
  // Taught nodes carry baked human knowledge · highlight them distinctly.
  const borderColor = data.taught ? TAUGHT_COLOR : color;
  return (
    <div
      className="rounded-xl border bg-panel px-4 py-3 shadow-lg"
      style={{
        borderColor,
        minWidth: 210,
        maxWidth: 280,
        boxShadow: data.taught ? `0 0 0 1px ${TAUGHT_COLOR}55` : undefined,
      }}
    >
      <Handle type="target" position={Position.Left} className="!bg-edge" />
      <div className="flex items-center gap-2">
        <span
          className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
          style={{ background: `${color}22`, color }}
        >
          {data.kind}
        </span>
        {data.taught && (
          <span
            className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide"
            style={{ background: `${TAUGHT_COLOR}22`, color: TAUGHT_COLOR }}
          >
            taught
          </span>
        )}
        {data.timesSeen > 1 && (
          <span className="ml-auto text-[10px] text-muted">seen {data.timesSeen}×</span>
        )}
      </div>
      <div className="mt-1.5 text-sm font-medium text-ink">{data.label}</div>
      {data.value && (
        <div className="mt-0.5 max-w-[190px] truncate text-[11px] text-muted">{data.value}</div>
      )}
      {data.procedure && (
        <div
          className="mt-2 rounded-md px-2 py-1 text-[11px] leading-snug"
          style={{ background: `${TAUGHT_COLOR}14`, color: "#d8b4fe" }}
        >
          <span className="font-semibold">procedure: </span>
          {data.procedure}
        </div>
      )}
      {data.conditionals.length > 0 && (
        <div className="mt-2 space-y-1">
          {data.conditionals.map((c, i) => (
            <div
              key={i}
              className="rounded-md px-2 py-1 text-[11px] leading-snug"
              style={{ background: `${TAUGHT_COLOR}14`, color: "#d8b4fe" }}
            >
              <span className="font-semibold">if</span> {c.when}{" "}
              <span className="font-semibold">→</span> {c.do}
            </div>
          ))}
        </div>
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
        taught: n.source === "taught",
        procedure: n.procedure ?? null,
        conditionals: n.conditionals ?? [],
      },
    }));
    const edges: Edge[] = graph.edges.map((e, i) => {
      // Conditional/taught branches read as labelled NL guards, styled distinctly.
      const conditional = Boolean(e.condition);
      return {
        id: `e${i}`,
        source: e.from,
        target: e.to,
        type: "smoothstep",
        animated: true,
        label: e.condition ?? (e.times_seen > 1 ? `${e.times_seen}×` : undefined),
        labelStyle: conditional ? { fill: TAUGHT_COLOR, fontSize: 11 } : undefined,
        style: {
          strokeWidth: Math.min(1 + e.times_seen, 4),
          stroke: conditional ? TAUGHT_COLOR : "#3b82f6",
          strokeDasharray: conditional ? "5 4" : undefined,
        },
      };
    });
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
        <Background gap={22} size={1} color="#ddd5c8" />
        <Controls
          showInteractive={false}
          className="overflow-hidden rounded-lg border border-edge bg-panel text-ink"
        />
      </ReactFlow>
    </div>
  );
}
