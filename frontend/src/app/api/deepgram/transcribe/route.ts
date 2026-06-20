import { NextResponse } from "next/server";

/**
 * Proxy: forwards a multipart audio upload to the Shepherd FastAPI backend's
 * /api/deepgram/transcribe endpoint. Keeps the browser same-origin (no CORS).
 *
 * Set SHEPHERD_API_BASE if the backend isn't at http://localhost:8765.
 */
const BACKEND = process.env.SHEPHERD_API_BASE ?? "http://localhost:8765";

export async function POST(req: Request) {
  let incoming: FormData;
  try {
    incoming = await req.formData();
  } catch {
    return NextResponse.json({ error: "expected multipart/form-data" }, { status: 400 });
  }

  const file = incoming.get("file");
  if (!(file instanceof Blob)) {
    return NextResponse.json({ error: "missing 'file' field" }, { status: 400 });
  }

  const out = new FormData();
  const filename = file instanceof File ? file.name : "recording.webm";
  out.append("file", file, filename);
  for (const key of ["api_key", "model", "language"] as const) {
    const v = incoming.get(key);
    if (typeof v === "string" && v.length > 0) out.append(key, v);
  }

  try {
    const res = await fetch(`${BACKEND}/api/deepgram/transcribe`, {
      method: "POST",
      body: out,
    });
    const data = await res.json().catch(() => ({ error: "invalid backend response" }));
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
