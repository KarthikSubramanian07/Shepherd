import { NextResponse } from "next/server";

/**
 * Proxy: forwards to the Shepherd backend's /api/agents/:id endpoint.
 */
const BACKEND = process.env.SHEPHERD_API_BASE ?? "http://localhost:8765";

export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  try {
    const res = await fetch(`${BACKEND}/api/agents/${encodeURIComponent(params.id)}`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json({ error: "backend unreachable" }, { status: 502 });
  }
}
