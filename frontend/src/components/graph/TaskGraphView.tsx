"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  Handle,
  Panel,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import type { Conditional, TaskGraph } from "@/lib/types";
import { analyzeGraph, type GraphMetrics, type NodeStat } from "@/lib/graph-analysis";

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

const TAUGHT_COLOR = "#a855f7"; // baked human knowledge
const MODAL_COLOR = "#cf6a43"; // the modal (most-probable) execution path

interface MilestoneData {
  label: string;
  kind: string;
  value: string | null;
  timesSeen: number;
  taught: boolean;
  procedure: string | null;
  conditionals: Conditional[];
  stat: NodeStat;
}

function MilestoneNode(props: NodeProps) {
  const data = props.data as unknown as MilestoneData;
  const color = KIND_COLOR[data.kind] ?? "#94a3b8";
  const { stat } = data;
  const borderColor = data.taught
    ? TAUGHT_COLOR
    : stat.onModalPath
      ? MODAL_COLOR
      : color;
  return (
    <div
      className="rounded-xl border bg-panel px-4 py-3 shadow-lg"
      style={{
        borderColor,
        minWidth: 210,
        maxWidth: 280,
        boxShadow: data.taught
          ? `0 0 0 1px ${TAUGHT_COLOR}55`
          : stat.onModalPath
            ? `0 0 0 1px ${MODAL_COLOR}, 0 0 22px ${MODAL_COLOR}33`
            : undefined,
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
          <span className="ml-auto font-mono text-[10px] text-muted">×{data.timesSeen}</span>
        )}
      </div>
      <div className="mt-1.5 text-sm font-medium text-ink">{data.label}</div>
      {data.value && (
        <div className="mt-0.5 max-w-[190px] truncate text-[11px] text-muted">{data.value}</div>
      )}

      {/* Graph-theoretic role: decision point (with branch entropy) / merge */}
      {(stat.isBranch || stat.isMerge) && (
        <div className="mt-2 flex flex-wrap items-center gap-1">
          {stat.isBranch && (
            <span
              className="rounded px-1.5 py-0.5 text-[9px] font-medium"
              style={{ background: `${MODAL_COLOR}1f`, color: MODAL_COLOR }}
              title={`Decision point · ${stat.entropy.toFixed(2)} bits of branch entropy`}
            >
              ⑂ branch · {stat.entropy.toFixed(2)} bits
            </span>
          )}
          {stat.isMerge && (
            <span
              className="rounded px-1.5 py-0.5 text-[9px] font-medium text-muted"
              style={{ background: "#94a3b81f" }}
              title="Paths converge here"
            >
              ⑃ merge ×{stat.inDegree}
            </span>
          )}
        </div>
      )}

      {data.procedure && (
        <div
          className="mt-2 rounded-md px-2 py-1 text-[11px] leading-snug"
          style={{ background: `${TAUGHT_COLOR}14`, color: "#9333ea" }}
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
              style={{ background: `${TAUGHT_COLOR}14`, color: "#9333ea" }}
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

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4" title={hint}>
      <span className="text-[11px] text-muted">{label}</span>
      <span className="font-mono text-[12px] tabular-nums text-ink">{value}</span>
    </div>
  );
}

function MetricsPanel({ m }: { m: GraphMetrics }) {
  return (
    <div className="w-[212px] rounded-xl border border-edge bg-panel/95 p-3 shadow-card backdrop-blur">
      <div className="mb-2 text-[10px] font-semibold uppercase tracking-eyebrow text-muted">
        Workflow analysis
      </div>
      <div className="space-y-1">
        <Stat label="Milestones" value={String(m.nodeCount)} />
        <Stat label="Transitions" value={String(m.edgeCount)} />
        <Stat label="Runs observed" value={String(m.runCount)} />
        <Stat label="Depth" value={`${m.depth} deep`} hint="Longest path through the workflow" />
        <Stat label="Decision points" value={String(m.branchPoints)} hint="Milestones with more than one observed next step" />
        <Stat label="Merge points" value={String(m.mergePoints)} hint="Where paths converge" />
        <Stat label="Branching factor" value={m.meanBranching.toFixed(2)} hint="Average transitions per milestone" />
        <Stat label="Decision entropy" value={`${m.avgEntropy.toFixed(2)} bits`} hint="Mean Shannon entropy at decision points. 0 = fully deterministic." />
        <Stat label="Modal coverage" value={`${Math.round(m.modalCoverage * 100)}%`} hint="Probability mass of the single most-likely run (highlighted path)" />
      </div>
      <div className="mt-2.5 flex items-center gap-1.5 border-t border-edge pt-2">
        <span className="h-1.5 w-5 rounded-full" style={{ background: MODAL_COLOR }} />
        <span className="text-[10px] text-muted">modal execution path</span>
      </div>
    </div>
  );
}

export function TaskGraphView({ graph }: { graph: TaskGraph }) {
  const { nodes, edges, metrics } = useMemo(() => {
    const a = analyzeGraph(graph);

    const nodes: Node[] = graph.nodes.map((n, i) => ({
      id: n.key,
      type: "milestone",
      position: a.pos.get(n.key) ?? { x: i * 300, y: 0 },
      data: {
        label: n.label,
        kind: n.kind,
        value: n.value,
        timesSeen: n.times_seen,
        taught: n.source === "taught",
        procedure: n.procedure ?? null,
        conditionals: n.conditionals ?? [],
        stat: a.nodeStats.get(n.key)!,
      },
    }));

    // Index the analysis edge-stats by from->to so conditional metadata is kept.
    const statByKey = new Map(a.edgeStats.map((e) => [`${e.from}->${e.to}`, e]));
    const edges: Edge[] = graph.edges.map((e, i) => {
      const es = statByKey.get(`${e.from}->${e.to}`);
      const conditional = Boolean(e.condition);
      const onModal = es?.onModalPath ?? false;
      const prob = es?.prob ?? 0;
      const pctLabel = prob > 0 && prob < 1 ? `${Math.round(prob * 100)}%` : undefined;
      return {
        id: `e${i}`,
        source: e.from,
        target: e.to,
        type: "smoothstep",
        animated: onModal || conditional,
        label: e.condition ?? pctLabel ?? (e.times_seen > 1 ? `${e.times_seen}×` : undefined),
        labelStyle: {
          fill: conditional ? TAUGHT_COLOR : "#7c7064",
          fontSize: 10,
          fontFamily: "monospace",
        },
        style: {
          strokeWidth: conditional ? 2 : onModal ? 3 : 1 + prob * 2,
          stroke: conditional ? TAUGHT_COLOR : onModal ? MODAL_COLOR : "#c9bfae",
          strokeDasharray: conditional ? "5 4" : es?.isBackEdge ? "2 3" : undefined,
          opacity: onModal || conditional ? 1 : 0.5 + prob * 0.4,
        },
      };
    });
    return { nodes, edges, metrics: a.metrics };
  }, [graph]);

  return (
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        minZoom={0.15}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={22} size={1} color="#ddd5c8" />
        <Controls
          showInteractive={false}
          className="overflow-hidden rounded-lg border border-edge bg-panel text-ink"
        />
        <Panel position="top-right">
          <MetricsPanel m={metrics} />
        </Panel>
      </ReactFlow>
    </div>
  );
}
