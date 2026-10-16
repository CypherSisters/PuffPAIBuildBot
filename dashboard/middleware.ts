/**
 * Middleware: the device-pairing gate, plus security headers for pages.
 *
 * RUNTIME: this middleware runs on the **Node.js** runtime (`runtime: "nodejs"`
 * in the exported config, supported since Next 15.5 and used here on 16.2.x).
 * That is required: the pairing check needs node:crypto (HMAC + timingSafeEqual)
 * and node:fs (the pairing state file), neither of which exists on the edge
 * runtime. If a future Next upgrade drops Node middleware, the build fails
 * loudly -- it cannot silently degrade to "no gate", and the per-route
 * requirePairing() guard in the keyed proxy remains as a second layer.
 *
 * puff puff pass
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { PAIR_PAGE, gateKindFor, isPaired, renewalCookieFor } from "@/app/_lib/server/pairing";
import { crossSiteResponse, isCrossSiteRequest } from "@/app/_lib/server/crossSite";

export const runtime = "nodejs";

/** The locked document an MCP App's screen runs in (app/mcp-app-host/route.ts). */
const MCP_APP_HOST = "/mcp-app-host";

/**
 * The one browser-app address a page may frame, or "" for none.
 *
 * An integrated web app lives at its own never-reused address
 * `http://<id>.localhost:<webapps port>` (backend `puffpai.webapps.host`).
 * Only the app's own page, `/dashboard/apps/<id>`, may frame it, and only that
 * one app: every other page lists no app address at all. The id must have
 * the issued shape `<slug>-<8 base32>`, so nothing typed into the path can
 * put another host into the policy. The port is a plain number or nothing.
 *
 * Plain JavaScript inside (tests/test_app_page_frame_src.py runs this exact body in
 * node).
 */
function webAppFrameSrc(pathname: string, port: string): string {
  const m = /^\/dashboard\/apps\/([a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8})\/?$/.exec(String(pathname || ""));
  if (!m) return "";
  const p = String(port || "");
  if (!/^[0-9]{2,5}$/.test(p) || Number(p) < 1024 || Number(p) > 65535) return "";
  return "http://" + m[1] + ".localhost:" + p;
}

/**
 * What a thing's page may frame for an app BuildBot put together, or "".
 *
 * An assembled app runs on this computer at `http://127.0.0.1:<port>` (its
 * port is picked when it starts, so the policy names the loopback host on any
 * port, never another host). Its "Use it here" frame is on the thing's own
 * page, `/dashboard/things/<id>`, and on the page of the job it does,
 * `/dashboard/jobs/<id>`, so only those pages list it, plus the one
 * video host its player may embed (YouTube's no-cookie address; the app says
 * her browser contacts YouTube directly). Every other page lists neither.
 * The id must have the issued thing-id shape, so nothing typed into the path
 * can widen the policy.
 *
 * Plain JavaScript inside (tests/test_assemble_frame_src.py runs this exact
 * body in node).
 */
function assembledFrameSrc(pathname: string): string {
  if (!/^\/dashboard\/(things|jobs)\/[a-z0-9][a-z0-9_-]{0,100}\/?$/.test(String(pathname || ""))) return "";
  return "http://127.0.0.1:* https://www.youtube-nocookie.com";
}

export function middleware(request: NextRequest) {
  const { pathname } = request.nextUrl;

  // ── cross-site gate, for every API route ───────────────────────────
  // A browser request that ANOTHER SITE initiated never reaches an API route
  // here, whatever it asked for and whatever it carries. Applied at the matcher
  // rather than per handler because that is the half that keeps being forgotten:
  // /api/pai had this check and /api/ha/service (a route since removed; the
  // dashboard reaches the same backend paths through /api/pai/ha/...) did not,
  // so a page the owner happened to open could POST a CORS-simple request
  // (Content-Type: text/plain, no preflight) and turn on a light with the
  // appliance's own key. Being a route is now enough to be covered; nobody has
  // to remember.
  //
  // BEFORE the pairing gate on purpose. The pairing cookie is SameSite=Lax, so
  // it does ride along on some cross-site requests -- refusing here means a
  // hostile page gets 403 whether or not the victim's browser is paired, which
  // also means the response leaks nothing about pairing state.
  //
  // Pages are NOT covered: a cross-site top-level navigation to /dashboard is
  // just somebody following a link, and refusing it would break that with no
  // security gain -- the API calls the page then makes are same-origin and are
  // gated on their own.
  //
  // A request with no Origin and no Sec-Fetch-Site (curl, a script, a probe) is
  // not browser-initiated and passes this check by design. It still has to
  // satisfy the pairing gate below. See app/_lib/server/crossSite.ts.
  if (pathname.startsWith("/api/") && isCrossSiteRequest(request.headers, request.url)) {
    return crossSiteResponse();
  }

  // ── pairing gate ────────────────────────────────────────────────────
  // gateKindFor() is default-deny: everything that is not an exempt API route
  // (/api/pair/*, /api/health), a static asset, or the pair
  // page itself is gated. A page added next month is protected by existing.
  //
  //   "api"  -> 401 JSON. A fetch() gets a status it can act on.
  //   "page" -> 302 to /pair?next=<where they were going>, so the owner lands
  //             back where they meant to be once the cookie is set. `next` is
  //             taken from this request's own pathname, never from user input,
  //             so it cannot be turned into an open redirect.
  const gate = gateKindFor(pathname);
  if (gate !== "none" && !isPaired(request)) {
    if (gate === "api") {
      return new NextResponse(JSON.stringify({ error: "pairing_required" }), {
        status: 401,
        headers: {
          "Content-Type": "application/json",
          "Cache-Control": "no-store",
        },
      });
    }
    const url = request.nextUrl.clone();
    url.pathname = PAIR_PAGE;
    url.search = "";
    // Only worth preserving a destination that is not the default landing page.
    if (pathname !== "/" && pathname !== "/dashboard") {
      url.searchParams.set("next", pathname);
    }
    const redirect = NextResponse.redirect(url);
    redirect.headers.set("Cache-Control", "no-store");
    return redirect;
  }

  const response = NextResponse.next();

  // ── sliding cookie renewal ─────────────────────────────────────────
  // Runs on every matched request that got past the gate, including plain page
  // loads. A cookie older than 30 days is re-issued with the same device id, so
  // a screen that is used daily but never re-paired does not eventually fall off
  // its 365-day cliff. Only possible for a device still in the registry, since
  // membership is part of verification -- a revoked device cannot renew its way
  // back in, and renewalCookieFor() returns null for anything that does not
  // verify, so no Set-Cookie is ever attached to an unpaired request.
  //
  // Wrapped so a failure here is a no-op, never a 500. Renewal is housekeeping:
  // the request has already been authorised, and an unwritable registry must not
  // turn a good request into an error page.
  try {
    const renewal = renewalCookieFor(request);
    if (renewal) response.cookies.set(renewal);
  } catch {
    /* no renewal this request */
  }

  // ── security headers ───────────────────────────────────────────────
  // Rendered pages only: a path whose last segment has no file extension. That
  // is /, /dashboard/*, /onboarding, /first-run and /pair -- a superset of the
  // /dashboard/* and /onboarding the previous matcher covered, so nothing that
  // had these headers loses them.
  //
  // The .html app pages in public/ are DELIBERATELY excluded, and it is not an
  // oversight. /dashboard/apps/trivia/play iframes /trivia-app.html from the
  // dashboard's own origin; X-Frame-Options: DENY on that file would render the
  // browser's "refused to connect" box where the game should be, and every one
  // of the five app pages is framed the same way. The fix is not to weaken DENY
  // to SAMEORIGIN for everything -- that would loosen the header on the whole
  // dashboard to accommodate five files -- so those files keep the header
  // posture they have always had (none), which is a known and accepted gap:
  // the files are static game pages holding no household data. What DOES
  // change for them is that they are now behind the pairing gate.
  //
  // Adding a CSP to a JSON API response is noise, and the CSP is about what a
  // page is allowed to load, so /api/* is excluded too.
  //
  // /mcp-app-host is excluded on purpose: it is the locked document an MCP
  // App's screen runs inside (app/mcp-app-host/route.ts), framed by thing
  // pages, and it sends its own, stricter policy (no network, frame-src
  // 'none', sandbox). The dashboard's policy here would replace it.
  const isRenderedPage =
    !pathname.startsWith("/api/") &&
    pathname !== MCP_APP_HOST &&
    !/\.[A-Za-z0-9]+$/.test(pathname.slice(pathname.lastIndexOf("/") + 1));
  if (isRenderedPage) {
    response.headers.set("X-Frame-Options", "DENY");
    response.headers.set("X-Content-Type-Options", "nosniff");
    response.headers.set("Referrer-Policy", "strict-origin-when-cross-origin");
    response.headers.set("X-XSS-Protection", "1; mode=block");

    // CSP for local-first app.
    // A Jellyfin media server (LAN-only) is allowed as a frame source (embedded
    // Media page) and image source (Now Playing album art). localhost:8096 is
    // the media-proxy (instrumentation.ts) -- the preferred iframe target, since
    // browsers' Local Network Access policy blocks framing .local/private-IP
    // origins directly. Set MEDIA_SERVER_URL to also allow a direct LAN origin
    // for clients that reach the media server without the proxy.
    const MEDIA_SERVER_URL = process.env.MEDIA_SERVER_URL ?? "";
    const JELLYFIN = ["http://localhost:8096", MEDIA_SERVER_URL].filter(Boolean).join(" ");
    // A browser app's own page frames exactly that app's address; no other
    // page names any app address (see webAppFrameSrc).
    const APP_FRAME = webAppFrameSrc(pathname, process.env.PUFFPAI_WEBAPPS_PORT ?? "4271");
    // An assembled app's own page and its video host, on its thing page only.
    const ASSEMBLED_FRAME = assembledFrameSrc(pathname);
    response.headers.set(
      "Content-Security-Policy",
      "default-src 'self'; " +
        "script-src 'self' 'unsafe-eval' 'unsafe-inline'; " +
        "style-src 'self' 'unsafe-inline'; " +
        `img-src 'self' data: blob: ${JELLYFIN}; ` +
        `frame-src 'self' ${JELLYFIN}${APP_FRAME ? " " + APP_FRAME : ""}${ASSEMBLED_FRAME ? " " + ASSEMBLED_FRAME : ""}; ` +
        // Sound and video the dashboard plays itself: its own address and
        // blob: (a stream player hands the media element a blob address).
        "media-src 'self' blob:; " +
        // Standards-track twin of X-Frame-Options: DENY above, for browsers
        // that honour CSP over XFO. Nothing may embed this dashboard, including
        // itself -- a redirect to /pair inside a hostile frame is exactly the
        // shape a credential-phishing overlay wants.
        "frame-ancestors 'none'; " +
        `connect-src 'self' blob: http://localhost:* ws://localhost:* ${JELLYFIN}; ` +
        "font-src 'self'; worker-src 'self' blob:;",
    );
  }

  return response;
}

export const config = {
  runtime: "nodejs",
  // Everything except Next's own build output and the favicon. The gate is
  // default-deny, so the matcher is deliberately broad: a page or API route
  // added later is covered without anyone remembering to widen this list.
  // gateKindFor() then decides, and lets static assets and the exempt API
  // routes through.
  //
  // _next/static and _next/image are excluded here rather than inside the gate
  // because they are served on the hot path for every page load, pairing screen
  // included -- a /pair that redirected its own stylesheet to /pair would be
  // unreadable.
  matcher: ["/((?!_next/static|_next/image|favicon.ico|favicon.png).*)"],
};
