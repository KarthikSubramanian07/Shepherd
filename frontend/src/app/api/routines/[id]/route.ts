import { NextResponse } from "next/server";
import { routines } from "@/lib/mock-data";

export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  const routine = routines.find((r) => r.id === params.id);
  if (!routine) {
    return NextResponse.json({ error: "routine not found" }, { status: 404 });
  }
  return NextResponse.json(routine);
}
