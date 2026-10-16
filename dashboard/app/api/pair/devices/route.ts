/**
 * GET /api/pair/devices -- list every paired device. Paired callers only.
 *
 * Paired:   200 { "devices": [{ id, name, createdAt, lastSeenAt }], "self": "<id>|null" }
 * Unpaired: 401 { "error": "pairing_required" }
 * Error:    500 { "error": "pairing_unavailable" }
 *
 * `self` lets the pair page mark which row is the browser asking, so nobody
 * revokes the device they are standing at by accident.
 *
 * The ids returned here are NOT credentials -- a cookie has to be signed over
 * one to be worth anything -- but the list is still household-shaped data
 * (device names, last-seen times), so it is behind the cookie like everything
 * else. The 500 on an unreadable registry is deliberate: rendering an empty
 * list would tell the owner their devices were wiped when the file was merely
 * unreadable.
 *
 * puff puff pass
 */
import { NextRequest, NextResponse } from "next/server";
import { isPaired, listPairedDevices, pairedDeviceIdFor } from "@/app/_lib/server/pairing";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  if (!isPaired(req)) {
    return NextResponse.json(
      { error: "pairing_required" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }
  try {
    return NextResponse.json(
      { devices: listPairedDevices(), self: pairedDeviceIdFor(req) },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json({ error: "pairing_unavailable" }, { status: 500 });
  }
}
