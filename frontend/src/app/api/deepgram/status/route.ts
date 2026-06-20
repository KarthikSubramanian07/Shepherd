import { NextResponse } from "next/server";

/**
 * Proxy: reports whether the backend has a Deepgram key configured (without
 * leaking it) plus the default model/language. Used by the Voice Lab page to
 * tell the user if they still need to supply a key.
 */
const BACKEND = process.env.SHEPHERD_API_BASE ?? "http://localhost:8765";

export async function GET() {
  try {
    const res = await fetch(`${BACKEND}/api/deepgram/status`, { cache: "no-store" });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json(
      { configured: false, error: `backend unreachable at ${BACKEND} (${(e as Error).message})` },
      { status: 502 },
    );
  }
}
