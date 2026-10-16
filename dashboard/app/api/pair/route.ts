/**
 * POST /api/pair -- redeem the pairing code for a device cookie.
 *
 * Request:  { "code": "7XK4-P2M9", "name": "kitchen tablet" }
 *           dashes/spaces/case optional; name optional and sanitised.
 * Success:  200 { "paired": true }    + Set-Cookie: puff_device=...
 * Failure:  401 { "paired": false, "error": "invalid_code" }
 *           429 { "paired": false, "error": "rate_limited" }
 *           403 { "paired": false, "error": "cross_origin" }
 *           400 { "paired": false, "error": "bad_request" }
 *           500 { "paired": false, "error": "pairing_unavailable" }
 *
 * There is deliberately NO `GET /api/pair/redeem?code=...` variant. A GET that
 * mints an auth cookie from a query parameter is a state-changing GET: the code
 * would land in browser history, Referer headers and any access log, and it
 * would be reachable by cross-site top-level navigation.
 *
 * This route is exempt from the pairing gate by design (see PAIR_API_PREFIX in
 * app/_lib/server/pairing.ts) -- it is the only way a fresh device can ever get
 * in. Everything an unpaired caller can learn from it is one bit: whether the
 * code they typed was right.
 *
 * puff puff pass
 */
import { NextRequest, NextResponse } from "next/server";
import {
  addPairedDevice,
  clearPairFailures,
  deviceCookieOptions,
  isRateLimited,
  isSameOrigin,
  issueDeviceCookieValueV2,
  recordPairFailure,
  sanitizeDeviceName,
  verifyCode,
} from "@/app/_lib/server/pairing";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  if (!isSameOrigin(req)) {
    return NextResponse.json({ paired: false, error: "cross_origin" }, { status: 403 });
  }

  // Rate-limit BEFORE doing any comparison so a limited client learns nothing.
  if (isRateLimited(req)) {
    return NextResponse.json(
      { paired: false, error: "rate_limited" },
      { status: 429, headers: { "Retry-After": "60" } },
    );
  }

  let code = "";
  let name: unknown = "";
  try {
    const body = (await req.json()) as { code?: unknown; name?: unknown };
    if (typeof body?.code === "string") code = body.code;
    name = body?.name;
  } catch {
    return NextResponse.json({ paired: false, error: "bad_request" }, { status: 400 });
  }
  if (!code) {
    return NextResponse.json({ paired: false, error: "bad_request" }, { status: 400 });
  }

  let ok = false;
  try {
    ok = verifyCode(code);
  } catch {
    // State unreadable/unwritable -- fail closed rather than pair anyone.
    return NextResponse.json({ paired: false, error: "pairing_unavailable" }, { status: 500 });
  }

  if (!ok) {
    recordPairFailure(req);
    return NextResponse.json({ paired: false, error: "invalid_code" }, { status: 401 });
  }

  clearPairFailures(req);

  // Mint the registry row FIRST, then sign a cookie over its id. Order matters:
  // a cookie whose device id is not in the registry fails membership on its very
  // next use, so a device would appear to pair and then 401 on everything. If
  // the registry cannot be written we fail the pairing outright rather than hand
  // out a credential we know is already dead.
  //
  // The name is whatever the person typed on the pair page, sanitised, or the
  // generic fallback. It exists so the owner can tell one row from another when
  // revoking. Deliberately NOT derived from the User-Agent: that would be both
  // unreliable and a small piece of fingerprinting we have no reason to store.
  let cookieValue: string;
  try {
    const label = sanitizeDeviceName(name) || "New device";
    const device = addPairedDevice(label);
    cookieValue = issueDeviceCookieValueV2(device.id);
  } catch {
    return NextResponse.json({ paired: false, error: "pairing_unavailable" }, { status: 500 });
  }

  const res = NextResponse.json(
    { paired: true },
    { status: 200, headers: { "Cache-Control": "no-store" } },
  );
  res.cookies.set(deviceCookieOptions(cookieValue));
  return res;
}
