/**
 * POST /api/pair/rotate -- issue a new pairing code. Paired devices only.
 *
 * Success:    200 { "code": "M4T7-9QXB", "rotatedAt": "<iso>", "expiresAt": "<iso>" }
 * Unpaired:   401 { "error": "pairing_required" }
 * Cross-site: 403 { "error": "cross_origin" }
 *
 * Rotates the CODE only, never the HMAC secret -- devices that are already
 * paired keep working. This revokes the ability to pair *new* devices with the
 * old code. To revoke a specific device use POST /api/pair/devices/revoke; to
 * revoke every device at once, delete the pairing state file on the appliance
 * (a new secret invalidates all outstanding cookies).
 *
 * The same rotation is available from the box without a browser:
 *   npm run pair:code -- --rotate
 *
 * puff puff pass
 */
import { NextRequest, NextResponse } from "next/server";
import {
  getPairingCodeInfo,
  isPaired,
  isSameOrigin,
  rotatePairingCode,
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
  try {
    rotatePairingCode();
    // Re-read rather than reusing rotatePairingCode()'s return so the code and
    // its expiry are guaranteed to describe the same rotation. The fresh code
    // cannot be expired, so this never rotates a second time.
    const info = getPairingCodeInfo();
    return NextResponse.json(
      { code: info.code, rotatedAt: info.rotatedAt, expiresAt: info.expiresAt },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json({ error: "pairing_unavailable" }, { status: 500 });
  }
}
