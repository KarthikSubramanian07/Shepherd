import { NextResponse } from "next/server";
import { routines } from "@/lib/mock-data";
import type { RoutineSummary } from "@/lib/types";

export async function GET() {
  const summaries: RoutineSummary[] = routines.map((r) => ({
    id: r.id,
    name: r.name,
    description: r.description,
    mode: r.mode,
    tags: r.tags,
    version: r.version,
    stepCount: r.stepCount,
    updatedAt: r.updatedAt,
    reliability: r.reliability,
    activeAgents: r.activeAgents,
  }));
  return NextResponse.json(summaries);
}
