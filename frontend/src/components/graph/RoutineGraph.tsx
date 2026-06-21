"use client";

import { useMemo } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import { type StepNodeData } from "./StepNode";
import { nodeTypes } from "./nodes";
import type { Agent, Routine, Run } from "@/lib/types";

interface RoutineGraphProps {
  routine: Routine;
  /** When provided, overlays this run's per-step status onto the graph. */
  run?: Run;
  /** Live agents to mark on the nodes they currently occupy. */
  agents?: Agent[];
}

export function RoutineGraph({ routine, run, agents = [] }: RoutineGraphProps) {
  const { nodes, edges } = useMemo(() => {
    const traceByStep = new Map(
      (run?.steps ?? []).map((s) => [s.stepId, s]),
    );
    const agentByStep = new Map<string, Agent>();
    for (const a of agents) {
      if (a.currentStepId) agentByStep.set(a.currentStepId, a);
    }

    const nodes: Node<StepNodeData>[] = routine.steps.map((s, i) => {
      const trace = traceByStep.get(s.id);
      const agent = agentByStep.get(s.id);
      const blocked =
        agent?.status === "blocked" ||
        trace?.status === "halted" ||
        trace?.status === "awaiting_human";
      return {
        id: s.id,
        type: "step",
        position: s.position ?? { x: i * 320, y: (i % 2) * 70 },
        data: {
          index: s.index,
          action: s.action,
          title: s.title,
          instruction: s.instruction,
          screenshotUrl: s.screenshotUrl,
          highStakes: s.highStakes,
          monitorTrigger: s.monitorTrigger,
          reliability: s.stats
            ? s.stats.successCount / Math.max(1, s.stats.executionCount)
            : undefined,
          runStatus: trace?.status,
          agentHere: agent?.name,
          blocked,
        },
      };
    });

    const edges: Edge[] = routine.edges.map((e) => {
      const targetTrace = traceByStep.get(e.target);
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        type: "smoothstep",
        animated: run ? targetTrace?.status === "running" : true,
        label: e.label,
      };
    });

    return { nodes, edges };
  }, [routine, run, agents]);

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
        <MiniMap
          pannable
          zoomable
          className="rounded-lg border border-edge"
          style={{ background: "#f0ece4" }}
          maskColor="rgba(0,0,0,0.6)"
          nodeColor={(n) => {
            const d = n.data as StepNodeData;
            if (d.blocked) return "#ef4444";
            if (d.runStatus === "completed") return "#22c55e";
            if (d.runStatus === "flagged" || d.runStatus === "deviated") return "#f59e0b";
            return "#3b82f6";
          }}
        />
      </ReactFlow>
    </div>
  );
}
