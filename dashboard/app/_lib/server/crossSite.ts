/**
 * Reject browser requests that another site made to the keyed proxy.
 *
 * WHAT THIS DEFENDS AGAINST
 * -------------------------
 * The dashboard has no CSRF token, and /api/pai
 * attaches the appliance's backend key to whatever it forwards. So a page on
 * any website the household happens to open — an ad frame, a link from a
 * message — can `fetch("http://<appliance>:3003/api/pai/ha/light", {...})` and
 * drive the house, or read the vault listing, with the owner's own credential.
 * The browser sends that request happily; the attacker never has to be on the
 * LAN at all, only to get one tab open on a machine that is.
 *
 * That is the realistic remote attack on a dashboard reached from a browser, and
 * it is the one thing a header check can actually stop, because the browser
 * stamps `Origin` / `Sec-Fetch-Site` itself and page JavaScript cannot forge
 * either. It still matters now that pairing exists: the pairing cookie is
 * SameSite=Lax, which stops a cross-site POST but not a cross-site GET, and a
 * hostile page in a paired browser would otherwise ride that cookie.
 *
 * WHAT THIS DOES NOT DEFEND AGAINST — say it plainly
 * --------------------------------------------------
 * Nothing that is not a browser. `curl http://<appliance>:3003/api/pai/...`
 * from any host on the LAN sends no Origin and no Sec-Fetch-Site and is
 * allowed, exactly as the iPhone-Shortcut style integrations need. This is a
 * cross-site guard, NOT authentication, and it must never be described as one.
 * Authentication is requirePairing() in the route, which a curl from the LAN
 * now has to satisfy as well: it needs a valid pairing cookie, which it can only
 * get by redeeming a code read off the appliance itself. This module still runs
 * FIRST, so a cross-site browser request is refused before the cookie is even
 * consulted -- the two guards answer different questions and neither replaces
 * the other.
 *
 * WHY ABSENT HEADERS ARE ALLOWED
 * ------------------------------
 * Failing closed on a missing header would reject every non-browser client and
 * buy nothing: an attacker scripting the appliance directly simply omits the
 * header. The header is only evidence when it is present. So: present and
 * wrong → reject; absent → no evidence either way. A non-browser client that
 * omits the header now still has to carry a valid pairing cookie to get past
 * requirePairing() in the route, which is what closed the LAN exposure this
 * comment used to describe as open.
 *
 * Pure module — no next/server import, so the plain-node test imports it
 * directly under Node's TypeScript type stripping.
 *
 * puff puff pass
 */

/** The minimum a caller must expose; NextRequest satisfies it structurally. */
export interface HeaderBag {
  get(name: string): string | null;
}

/**
 * Sec-Fetch-Site values that are not cross-site.
 *  - "same-origin": the dashboard's own pages and the app pages in public/.
 *  - "none": a user-initiated navigation (typing the URL, a bookmark).
 * "same-site" is NOT safe: every integrated browser app runs
 * at its own address `http://<id>.localhost:<port>`, which is a different
 * origin but the SAME site as a dashboard reached at `localhost`. A request an
 * app page makes to the dashboard is therefore "same-site", and it must be
 * refused like any other site's. "cross-site", "same-site" and any unknown
 * value are rejected.
 */
const SAFE_FETCH_SITE = new Set(["same-origin", "none"]);

function originOf(value: string | null): string | null {
  if (!value) return null;
  const v = value.trim();
  // "null" is a real Origin value (sandboxed iframe, some redirects). It is not
  // an origin we can match, and it is exactly what a hostile sandboxed frame
  // sends, so treat it as cross-site rather than as "no evidence".
  if (v === "null") return "null";
  try {
    return new URL(v).origin;
  } catch {
    return "null"; // unparseable Origin proves nothing good; treat as hostile
  }
}

/**
 * True if this request is a browser request initiated by a DIFFERENT site.
 *
 * Two independent signals, either of which is enough:
 *   1. Sec-Fetch-Site: cross-site  (sent by every current browser)
 *   2. Origin present and not equal to the origin the request was addressed to
 *      (Host + scheme), which covers browsers that omit Sec-Fetch-Site.
 *
 * The Host header is client-supplied, but that does not help an attacker here:
 * to pass check 2 by lying they would have to send Origin equal to the
 * appliance's own origin, which the browser will not let page script do — the
 * browser writes Origin from the initiating page, not from the fetch options.
 */
export function isCrossSiteRequest(headers: HeaderBag, url: string): boolean {
  const site = (headers.get("sec-fetch-site") || "").toLowerCase();
  if (site && !SAFE_FETCH_SITE.has(site)) return true;

  const origin = originOf(headers.get("origin"));
  if (origin === null) return false; // no Origin header: not browser-initiated
  if (origin === "null") return true;

  let selfOrigin: string;
  try {
    selfOrigin = new URL(url).origin;
  } catch {
    return true; // we cannot establish our own origin: fail closed
  }
  if (origin === selfOrigin) return false;

  // Behind a reverse proxy the request URL's scheme/port can differ from what
  // the browser used, so also accept an Origin whose HOST matches the Host
  // header the request actually carried.
  const host = headers.get("host");
  if (host) {
    try {
      if (new URL(origin).host === host) return false;
    } catch {
      /* fall through to reject */
    }
  }
  return true;
}

/** 403 with a body that names the reason without naming the backend. */
export function crossSiteResponse(): Response {
  return new Response(JSON.stringify({ error: "cross_site_request_blocked" }), {
    status: 403,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
