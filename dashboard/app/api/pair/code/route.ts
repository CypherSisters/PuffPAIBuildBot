/**
 * GET /api/pair/code -- reveal the current pairing code so an ALREADY-PAIRED
 * device can read it out to add a second one.
 *
 * Paired:   200 { "code": "7XK4-P2M9", "rotatedAt": "<iso>", "expiresAt": "<iso>" }
 * Unpaired: 401 { "error": "pairing_required" }
 *
 * Gated on the COOKIE, not on the enforcement stage -- the code is a credential
 * and must never be readable by an unpaired client even while
 * PUFF_PAIRING_ENFORCE is "off". (That is why this route sits under the exempt
 * /api/pair prefix and still checks isPaired itself: the prefix exemption is
 * about letting a stranger REDEEM a code, never about letting them read one.)
 *
 * Codes expire (PAIR_CODE_TTL_SEC). getPairingCodeInfo() lazily rotates an
 * expired one before returning, so whatever this hands the UI is always
 * redeemable. That rotation is reachable only behind the isPaired gate below,
 * so a stranger cannot spin the code by hammering this endpoint.
 *
 * puff puff pass
 */
import { NextRequest, NextResponse } from "next/server";
import { getPairingCodeInfo, isPaired } from "@/app/_lib/server/pairing";

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
    const info = getPairingCodeInfo();
    return NextResponse.json(
      { code: info.code, rotatedAt: info.rotatedAt, expiresAt: info.expiresAt },
      { headers: { "Cache-Control": "no-store" } },
    );
  } catch {
    return NextResponse.json({ error: "pairing_unavailable" }, { status: 500 });
  }
}
