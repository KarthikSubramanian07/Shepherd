import { NextResponse } from "next/server";
import { agents } from "@/lib/mock-data";

export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  const agent = agents.find((a) => a.id === params.id);
  if (!agent) {
    return NextResponse.json({ error: "agent not found" }, { status: 404 });
  }
  return NextResponse.json(agent);
}
