import { NextResponse } from "next/server";

export async function GET() {
  return NextResponse.json({
    status: "ok",
    service: "PuffPAI Dashboard",
    version: process.env.NEXT_PUBLIC_VERSION || "1.0.0",
    timestamp: new Date().toISOString(),
  });
}
