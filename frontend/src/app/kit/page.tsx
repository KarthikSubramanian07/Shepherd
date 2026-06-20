"use client";

import { useState, type ReactNode } from "react";
import { Background, ReactFlow, type Edge, type Node } from "@xyflow/react";
import { Plus, Search, ShieldAlert } from "lucide-react";
import { nodeTypes } from "@/components/graph/nodes";
import { PageHeader } from "@/components/layout/PageHeader";
import { placeholderShot } from "@/lib/utils";
import {
  Avatar,
  Badge,
  Button,
  Card,
  EmptyState,
  IconButton,
  Input,
  Kbd,
  Progress,
  Separator,
  Skeleton,
  Spinner,
  StatusDot,
  Stat,
  Tabs,
  Textarea,
  Tooltip,
} from "@/components/ui/primitives";

const demoNodes: Node[] = [
  {
    id: "t",
    type: "trigger",
    position: { x: 0, y: 70 },
    data: {
      label: "Voice command",
      source: "voice",
      description: "“fill out the form for Alex”",
    },
  },
  {
    id: "a",
    type: "action",
    position: { x: 250, y: 30 },
    data: {
      index: 4,
      action: "type",
      title: "Enter email",
      instruction: "Tab to the email field and type the injected value.",
      screenshotUrl: placeholderShot("Enter email"),
      reliability: 0.92,
    },
  },
  {
    id: "a2",
    type: "action",
    position: { x: 540, y: 30 },
    data: {
      index: 8,
      action: "hotkey",
      title: "Credential field",
      screenshotUrl: placeholderShot("Credential field", "halt"),
      highStakes: true,
      blocked: true,
      agentHere: "Aurora",
    },
  },
  {
    id: "b",
    type: "branch",
    position: { x: 830, y: 60 },
    data: { label: "Captcha solved?", yes: "continue", no: "halt" },
  },
  {
    id: "n",
    type: "note",
    position: { x: 300, y: 250 },
    data: { text: "Monitor halts at the credential step on every run." },
  },
];

const demoEdges: Edge[] = [
  { id: "e1", source: "t", target: "a", type: "smoothstep", animated: true },
  { id: "e2", source: "a", target: "a2", type: "smoothstep" },
  { id: "e3", source: "a2", target: "b", type: "smoothstep" },
];

export default function KitPage() {
  const [tab, setTab] = useState("overview");
  const [text, setText] = useState("");

  return (
    <div>
      <PageHeader
        title="Component Library"
        subtitle="Primitives the dashboard is built from — UI atoms and composable graph nodes."
      />

      <div className="space-y-8 p-6">
        <Section title="Graph nodes" hint="Composed from the node-kit primitives. Live React Flow canvas.">
          <div className="h-[360px] overflow-hidden rounded-xl border border-edge">
            <ReactFlow
              nodes={demoNodes}
              edges={demoEdges}
              nodeTypes={nodeTypes}
              fitView
              nodesDraggable
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={22} size={1} color="#1b2230" />
            </ReactFlow>
          </div>
        </Section>

        <Section title="Buttons">
          <Row>
            <Button>Primary</Button>
            <Button variant="outline">Outline</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="danger">Danger</Button>
            <Button size="sm">
              <Plus size={14} /> Small
            </Button>
            <IconButton aria-label="search">
              <Search size={16} />
            </IconButton>
            <Tooltip label="I'm a tooltip">
              <Button variant="outline">Hover me</Button>
            </Tooltip>
          </Row>
        </Section>

        <Section title="Badges & status">
          <Row>
            <Badge>neutral</Badge>
            <Badge tone="accent">accent</Badge>
            <Badge tone="ok">ok</Badge>
            <Badge tone="flag">
              <ShieldAlert size={11} /> flag
            </Badge>
            <Badge tone="halt">halt</Badge>
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <StatusDot hex="#22c55e" /> idle
            </span>
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <StatusDot hex="#3b82f6" pulse /> running
            </span>
            <span className="flex items-center gap-1.5 text-xs text-muted">
              <StatusDot hex="#ef4444" pulse /> blocked
            </span>
          </Row>
        </Section>

        <Section title="Inputs">
          <div className="grid max-w-md gap-3">
            <Input placeholder="Routine name…" />
            <Textarea
              rows={3}
              placeholder="Describe the task you recorded…"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <Tabs
              items={[
                { value: "overview", label: "Overview" },
                { value: "steps", label: "Steps" },
                { value: "history", label: "History" },
              ]}
              value={tab}
              onValueChange={setTab}
            />
          </div>
        </Section>

        <Section title="Data display">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <Stat label="Agents" value={4} hint="across routines" />
            <Stat label="Reliability" value="86%" />
            <Stat label="Runs today" value={128} />
            <Stat label="Blocked" value={2} hint="need a human" />
          </div>
          <div className="mt-4 max-w-md space-y-3">
            <div className="flex items-center gap-3">
              <Avatar name="Aurora" hex="#3b82f6" />
              <Avatar name="Borealis" hex="#22c55e" />
              <Avatar name="Cirrus" hex="#f59e0b" />
              <Separator orientation="vertical" className="h-6" />
              <span className="flex items-center gap-1 text-xs text-muted">
                press <Kbd>⌘</Kbd> <Kbd>K</Kbd>
              </span>
            </div>
            <Progress value={0.86} tone="#22c55e" />
            <Progress value={0.4} />
          </div>
        </Section>

        <Section title="Feedback">
          <Row>
            <Spinner />
            <Spinner size={24} />
          </Row>
          <div className="mt-3 grid max-w-2xl gap-3 md:grid-cols-2">
            <Card className="space-y-2 p-4">
              <Skeleton className="h-4 w-2/3" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-5/6" />
              <Skeleton className="h-24 w-full" />
            </Card>
            <EmptyState
              icon={<ShieldAlert size={22} />}
              title="No interventions"
              description="All agents are running clean — nothing needs you right now."
              action={<Button size="sm">Refresh</Button>}
            />
          </div>
        </Section>
      </div>
    </div>
  );
}

function Section({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section>
      <div className="mb-3">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        {hint && <p className="text-xs text-muted">{hint}</p>}
      </div>
      {children}
    </section>
  );
}

function Row({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-3">{children}</div>;
}
