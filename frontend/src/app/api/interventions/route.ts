import { NextResponse } from "next/server";

/**
 * Proxy: forwards to the Shepherd backend's /api/interventions endpoint.
 * Returns an empty array if the backend is unreachable.
 */
const BACKEND = process.env.SHEPHERD_API_BASE ?? "http://localhost:8765";

export async function GET() {
  try {
    const res = await fetch(`${BACKEND}/api/interventions`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch {
    return NextResponse.json([]);
  }
}
