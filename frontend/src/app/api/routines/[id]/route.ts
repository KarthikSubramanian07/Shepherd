import { NextResponse } from "next/server";

const BACKEND = process.env.SHEPHERD_API_BASE ?? "http://localhost:8765";

export async function GET(
  _req: Request,
  { params }: { params: { id: string } },
) {
  try {
    const res = await fetch(`${BACKEND}/api/routines/${params.id}`, {
      cache: "no-store",
    });
    const data = await res.json();
    return NextResponse.json(data, { status: res.status });
  } catch (e) {
    return NextResponse.json(
      {
        error: `cannot reach Shepherd backend at ${BACKEND} — is it running? (${(e as Error).message})`,
      },
      { status: 502 },
    );
  }
}
