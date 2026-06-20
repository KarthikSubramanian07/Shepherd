import { NextResponse } from "next/server";
import { runs } from "@/lib/mock-data";
import type { RunSummary } from "@/lib/types";

export async function GET() {
  const summaries: RunSummary[] = runs.map((r) => ({
    id: r.id,
    routineId: r.routineId,
    routineName: r.routineName,
    agentId: r.agentId,
    agentName: r.agentName,
    status: r.status,
    startedAt: r.startedAt,
    endedAt: r.endedAt,
    confidence: r.confidence,
  }));
  return NextResponse.json(summaries);
}
