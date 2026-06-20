import { NextResponse } from "next/server";
import { agents, interventions } from "@/lib/mock-data";
import type { InterventionResolution } from "@/lib/types";

/**
 * Resolve a human-in-the-loop intervention. Mutates the in-memory mock so the
 * UI reflects the change for the session. A real backend would persist this and
 * signal the blocked agent to resume.
 */
export async function POST(
  req: Request,
  { params }: { params: { id: string } },
) {
  const body = (await req.json()) as {
    resolution: InterventionResolution;
    note?: string;
  };

  const intervention = interventions.find((i) => i.id === params.id);
  if (!intervention) {
    return NextResponse.json({ error: "intervention not found" }, { status: 404 });
  }

  intervention.status = "resolved";
  intervention.resolution = body.resolution;
  intervention.note = body.note;
  intervention.resolvedBy = "you";
  intervention.resolvedAt = new Date().toISOString();

  // Unblock the associated agent.
  const agent = agents.find((a) => a.id === intervention.agentId);
  if (agent) {
    agent.status = body.resolution === "rejected" ? "failed" : "running";
    agent.block = undefined;
    agent.lastActivityAt = new Date().toISOString();
  }

  return NextResponse.json(intervention);
}
