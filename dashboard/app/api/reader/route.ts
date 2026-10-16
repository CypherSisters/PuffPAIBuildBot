/**
 * POST /api/reader — server-side article fetcher for the contained-link
 * overlay.
 *
 * WHAT IT IS FOR
 * --------------
 * The appliance is a wall-mounted kiosk: Chromium runs with --kiosk, so a link
 * that opens a new window is a trap with no way back. Every external link is
 * therefore intercepted client-side (ExternalLinkGuard) and rendered inside an
 * escapable overlay. This route is the half that has to leave the house: it
 * fetches the page and returns EXTRACTED PLAIN TEXT. No HTML, no CSS, no
 * scripts, no remote images ever cross back into the dashboard's origin.
 *
 * SECURITY POSTURE — read app/_lib/server/urlGuard.ts for the detail.
 *  - Accepts a URL from the browser, so the URL is treated as hostile input.
 *  - http/https only, ports 80/443 only, no userinfo.
 *  - DNS-resolved and refused unless EVERY address is public unicast (v4+v6,
 *    including mapped/NAT64/6to4/Teredo forms).
 *  - The socket is opened to the approved IP with the hostname carried only as
 *    Host/SNI, so DNS rebinding cannot move the connection.
 *  - Redirects followed by hand, re-validated per hop, max 4.
 *  - 10 s total, 2 MB wire cap, identity encoding, text/html|text/plain only.
 *  - No cookies, no Referer, no household-identifying User-Agent outbound.
 *
 * ACCESS CONTROL — why SAME-ORIGIN and not the pairing gate
 * ---------------------------------------------------------
 * This route is a capability ("make the appliance fetch a URL"), so it should
 * not be drivable by anything but this dashboard's own pages.
 *
 *  - It is NOT behind the pairing gate, deliberately. The kiosk browser on the
 *    appliance is an UNPAIRED client by design at the default "knowledge"
 *    enforcement stage (see pairing.ts enforceMode). Gating this route would
 *    make tapping a news story on the wall fail with a 401 — which is the exact
 *    trap this whole feature exists to remove. The route also reaches nothing
 *    household-side: it carries no credential, cannot touch the backend, HA,
 *    the cameras or the knowledge base, and can only ever reach public
 *    internet addresses.
 *  - It IS same-origin-only, and STRICTER than pairing.ts's isSameOrigin():
 *    that helper allows a MISSING Origin header (its contract, since a
 *    credential-less curl is not CSRF). Here a missing Origin is exactly the
 *    shape of the caller we want to refuse — a script on some other LAN device
 *    using the appliance as an anonymising fetch relay. Browsers always send
 *    Origin on POST, including same-origin POST, so requiring it costs the
 *    real client nothing. See sameOriginStrict() below.
 *  - requirePairing() is still called: today it is a no-op for this prefix, but
 *    if an operator ever adds "/api/reader" to ALL_PREFIXES the route starts
 *    enforcing immediately, without needing a middleware change.
 *  - A small global rate limit caps the relay/DoS value of the capability.
 *
 * RESIDUAL RISK, stated plainly:
 *  - A household member can still make the appliance fetch any public URL by
 *    tapping a link. That is the feature.
 *  - The remote host learns the appliance's public IP (unavoidable for any
 *    fetch) and that a generic Chrome-shaped client asked for the page.
 *  - Nothing here defends against a malicious *upstream* page; it defends the
 *    LAN from the URL. The plain-text-only return path is what protects the
 *    browser.
 *
 * puff puff pass
 */
import { NextRequest, NextResponse } from "next/server";
import { fetchArticle } from "@/app/_lib/server/readerFetch";
import { requirePairing } from "@/app/_lib/server/pairing";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

/** Bodies are tiny; anything larger is not a URL. */
const MAX_BODY_BYTES = 4096;

/* ── rate limit ────────────────────────────────────────────────────── */

const RL_WINDOW_MS = 60_000;
const RL_MAX = 20;

const G = globalThis as unknown as {
  __puffReaderRl?: { count: number; windowStart: number };
};

/**
 * ONE global bucket, not per-IP — same reasoning as pairing.ts: under bare
 * `next start` there is no trusted proxy, so x-forwarded-for is an
 * attacker-supplied string and bucketing on it would mean no limit at all.
 * 20/min is far above what a human tapping links generates and far below what
 * makes the appliance useful as a scraping relay.
 */
function rateLimited(now = Date.now()): boolean {
  const e = G.__puffReaderRl;
  if (!e || now - e.windowStart >= RL_WINDOW_MS) {
    G.__puffReaderRl = { count: 1, windowStart: now };
    return false;
  }
  e.count += 1;
  return e.count > RL_MAX;
}

/* ── CSRF ──────────────────────────────────────────────────────────── */

/**
 * Origin MUST be present and MUST match Host. Stricter than
 * pairing.isSameOrigin() on purpose — see the header comment.
 */
function sameOriginStrict(req: NextRequest): boolean {
  const origin = req.headers.get("origin");
  if (!origin) return false;
  const host = req.headers.get("host");
  if (!host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

function json(body: unknown, status: number): NextResponse {
  return NextResponse.json(body, {
    status,
    headers: { "Cache-Control": "no-store" },
  });
}

export async function POST(req: NextRequest): Promise<NextResponse> {
  // No-op today for this prefix; live the moment an operator gates it.
  const denied = requirePairing(req);
  if (denied) return denied as unknown as NextResponse;

  if (!sameOriginStrict(req)) {
    return json({ ok: false, reason: "cross_origin" }, 403);
  }
  // Blocks the simple-request CSRF shapes outright (form posts cannot set it).
  const ct = (req.headers.get("content-type") || "").split(";")[0].trim();
  if (ct !== "application/json") {
    return json({ ok: false, reason: "bad_content_type" }, 415);
  }
  if (rateLimited()) {
    return json({ ok: false, reason: "rate_limited" }, 429);
  }

  let raw: string;
  try {
    raw = await req.text();
  } catch {
    return json({ ok: false, reason: "unreadable_body" }, 400);
  }
  if (raw.length > MAX_BODY_BYTES) {
    return json({ ok: false, reason: "body_too_large" }, 413);
  }

  let url: unknown;
  try {
    const parsed = JSON.parse(raw) as { url?: unknown };
    url = parsed && typeof parsed === "object" ? parsed.url : undefined;
  } catch {
    return json({ ok: false, reason: "bad_json" }, 400);
  }

  // Single argument on purpose: the default validator (which refuses every
  // private address) is the only one production ever uses. The test harness is
  // the sole caller that passes a second argument.
  const result = await fetchArticle(url);

  if (!result.ok) {
    // 502 = "the fetch did not work"; 400 = "your URL was not acceptable".
    const clientFault =
      result.reason.indexOf("blocked_") === 0 ||
      result.reason === "unparseable_url" ||
      result.reason === "empty_url";
    return json(result, clientFault ? 400 : 502);
  }

  return json(
    {
      ok: true,
      title: result.title,
      host: result.host,
      url: result.url,
      paragraphs: result.paragraphs,
      truncated: result.truncated,
    },
    200,
  );
}

/** Everything else is refused — this is a POST-only capability. */
export async function GET(): Promise<NextResponse> {
  return json({ ok: false, reason: "method_not_allowed" }, 405);
}
