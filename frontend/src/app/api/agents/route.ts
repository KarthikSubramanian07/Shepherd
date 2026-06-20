import { NextResponse } from "next/server";
import { agents } from "@/lib/mock-data";

export async function GET() {
  return NextResponse.json(agents);
}
