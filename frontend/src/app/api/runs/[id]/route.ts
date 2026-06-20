import { NextResponse } from "next/server";
import { runs } from "@/lib/mock-data";

export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  const run = runs.find((r) => r.id === params.id);
  if (!run) {
    return NextResponse.json({ error: "run not found" }, { status: 404 });
  }
  return NextResponse.json(run);
}
