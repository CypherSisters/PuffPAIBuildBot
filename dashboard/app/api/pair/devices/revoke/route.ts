/**
 * POST /api/pair/devices/revoke -- remove one device from the registry.
 *
 * Request:    { "id": "<12-char device id>" }
 * Success:    200 { "revoked": true }
 * Not found:  404 { "error": "not_found" }
 * Unpaired:   401 { "error": "pairing_required" }
 * Cross-site: 403 { "error": "cross_origin" }
 * Bad body:   400 { "error": "bad_request" }
 * Error:      500 { "error": "pairing_unavailable" }
 *
 * Revocation takes effect on the revoked device's NEXT request: its cookie
 * still verifies cryptographically, but membership is checked on every request
 * against a registry read fresh from disk, and its row is gone. No restart, no
 * secret change, no other device affected.
 *
 * A device may revoke itself. When it does, the response also clears its own
 * cookie -- a courtesy so the browser stops sending a credential that is
 * already dead, not the control. The control is the missing row.
 *
 * Errors are NOT swallowed. Reporting a successful revoke that did not persist
 * would be the worst possible lie to tell an owner who is trying to kick a lost
 * phone off their appliance, so an unwritable or unreadable registry is a 500.
 *
 * puff puff pass
 */
import { NextRequest, NextResponse } from "next/server";
import {
  clearedDeviceCookieOptions,
  isPaired,
  isSameOrigin,
  pairedDeviceIdFor,
  revokePairedDevice,
} from "@/app/_lib/server/pairing";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) {
    return NextResponse.json({ error: "cross_origin" }, { status: 403 });
  }
  if (!isPaired(req)) {
    return NextResponse.json(
      { error: "pairing_required" },
      { status: 401, headers: { "Cache-Control": "no-store" } },
    );
  }

  let id = "";
  try {
    const body = (await req.json()) as { id?: unknown };
    if (typeof body?.id === "string") id = body.id;
  } catch {
    return NextResponse.json({ error: "bad_request" }, { status: 400 });
  }
  if (!id) {
    return NextResponse.json({ error: "bad_request" }, { status: 400 });
  }

  // Read the caller's own id BEFORE the revoke, while its row still exists.
  const self = pairedDeviceIdFor(req);

  let revoked: boolean;
  try {
    revoked = revokePairedDevice(id);
  } catch {
    return NextResponse.json({ error: "pairing_unavailable" }, { status: 500 });
  }
  if (!revoked) {
    return NextResponse.json({ error: "not_found" }, { status: 404 });
  }

  const res = NextResponse.json({ revoked: true }, { headers: { "Cache-Control": "no-store" } });
  if (self !== null && self === id) {
    res.cookies.set(clearedDeviceCookieOptions());
  }
  return res;
}
