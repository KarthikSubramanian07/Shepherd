import { NextResponse } from "next/server";
import { interventions } from "@/lib/mock-data";

export async function GET() {
  return NextResponse.json(interventions);
}
