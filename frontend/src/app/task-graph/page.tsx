"use client";

import { useCallback, useEffect, useState } from "react";
import { Network, RefreshCw, Workflow } from "lucide-react";
import { api } from "@/lib/api";
import type { TaskGraph } from "@/lib/types";
import { TaskGraphView } from "@/components/graph/TaskGraphView";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  Spinner,
  Stat,
} from "@/components/ui/primitives";

const KNOWN_ROUTINES = [
  "ROUTINE_JOB_APPLICATION",
  "ROUTINE_FORM_FILL",
  "ROUTINE_BROWSER_SHOWPIECE",
];

export default function TaskGraphPage() {
  const [routineId, setRoutineId] = useState(KNOWN_ROUTINES[0]);
  const [graph, setGraph] = useState<TaskGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async (id: string) => {
    setLoading(true);
    setError(null);
    try {
      setGraph(await api.getTaskGraph(id));
    } catch (e) {
      setError((e as Error).message);
      setGraph(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(routineId);
  }, [load, routineId]);

  const empty = !graph || graph.nodes.length === 0;

  return (
    <div className="flex h-full flex-col gap-4 p-6">
      <header className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent/15 text-accent">
            <Workflow size={18} />
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight">Task Graph</h1>
            <p className="text-xs text-muted">
              High-level milestones an agent crystallized across runs (live backend).
            </p>
          </div>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <select
            value={routineId}
            onChange={(e) => setRoutineId(e.target.value)}
            className="h-9 rounded-lg border border-edge bg-panel2 px-3 text-sm text-ink focus:border-accent focus:outline-none"
          >
            {KNOWN_ROUTINES.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <Button variant="outline" size="sm" onClick={() => void load(routineId)}>
            <RefreshCw size={14} />
            Reload
          </Button>
        </div>
      </header>

      {graph && !empty && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Milestones" value={graph.nodes.length} />
          <Stat label="Transitions" value={graph.edges.length} />
          <Stat label="Runs observed" value={graph.run_count} />
          <Stat
            label="Last run"
            value={graph.last_run_id || "·"}
            hint={graph.last_run_id ? "most recent traversal" : undefined}
          />
        </div>
      )}

      <Card className="relative min-h-0 flex-1 overflow-hidden p-0">
        {loading ? (
          <div className="flex h-full items-center justify-center">
            <Spinner size={22} />
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState
              icon={<Network size={28} />}
              title="Couldn't reach the backend"
              description={`${error}. Is the Control Hub running on :8765? (NEXT_PUBLIC_BACKEND_BASE overrides the URL.)`}
              action={
                <Button variant="outline" size="sm" onClick={() => void load(routineId)}>
                  <RefreshCw size={14} />
                  Retry
                </Button>
              }
            />
          </div>
        ) : empty ? (
          <div className="flex h-full items-center justify-center p-6">
            <EmptyState
              icon={<Workflow size={28} />}
              title="No graph yet"
              description={`${routineId} hasn't been run, so no milestones have crystallized. Run it once and the graph will appear here.`}
            />
          </div>
        ) : (
          <TaskGraphView graph={graph} />
        )}
      </Card>

      {graph && !empty && graph.intents.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted">
          <span>Seen intents:</span>
          {graph.intents.slice(0, 6).map((it, i) => (
            <Badge key={i} tone="neutral">
              {it}
            </Badge>
          ))}
        </div>
      )}
    </div>
  );
}
