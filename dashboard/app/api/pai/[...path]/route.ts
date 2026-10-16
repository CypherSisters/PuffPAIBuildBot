/**
 * /api/pai/[...path]: generic server-side proxy to the PuffPAI backend.
 *
 * The API key is not in the client bundle (no NEXT_PUBLIC_ env var). The
 * browser calls this same-origin route with NO
 * key; the key lives server-side only (PUFFPAI_API_KEY in .env.local) and is
 * attached here. Bonus: the dashboard now works from any LAN device, not
 * just the host (clients no longer need to reach localhost:8000 themselves).
 *
 * Streaming (e.g. /chat/stream SSE) passes through untouched — the upstream
 * body is returned directly without buffering.
 *
 * WHAT GUARDS THIS ROUTE, AND WHAT DOES NOT
 * -----------------------------------------
 * Attaching the appliance's key to an arbitrary path for an arbitrary caller
 * is the whole hazard here, so the route narrows both halves:
 *   - WHICH PATHS: isAllowedProxyPath() — the backend prefixes the dashboard
 *     actually calls, and nothing else (404). Without it this route is a
 *     universal key-bearing tunnel to every endpoint the backend has.
 *   - WHICH CALLERS: isCrossSiteRequest() — a browser request stamped by
 *     another site is refused (403), which is what stops a random web page the
 *     household opens from driving the house with the owner's credential.
 *
 *   - WHO IS ASKING: requirePairing() — the caller must carry a valid device
 *     cookie, obtained once by typing the pairing code printed on the box.
 *     Unpaired callers get 401 whatever they asked for. This is the actual
 *     authentication; the two guards above are narrowing, not
 *     authentication, and must not be described as such on their own.
 *
 *   - WHAT IT SAYS ABOUT THE CALLER: upstreamHeaders() — the client contributes
 *     an allowlist of headers (one, today) and nothing else, and the device
 *     identity the backend keys sessions on is derived here from the verified
 *     pairing cookie rather than accepted from the caller. See
 *     app/_lib/server/proxyHeaders.ts for why this is necessary.
 *
 * The middleware gates this path too, and is the primary enforcement point. The
 * check here is the second layer: a narrowed matcher or a Next upgrade that
 * changes middleware semantics must not be able to leave this route open.
 *
 * puff puff pass
 */
import { NextRequest } from "next/server";
import { isAllowedProxyPath, notAllowedResponse } from "@/app/_lib/server/proxyAllowlist";
import { guardCredentialedRoute } from "@/app/_lib/server/routeGuard";
import { upstreamHeaders } from "@/app/_lib/server/proxyHeaders";
import { deviceSessionIdFor } from "@/app/_lib/server/pairing";
import { shouldBufferBody } from "@/app/_lib/server/proxyBody";
import { RANGE_RESPONSE_HEADERS, isThingMediaFile, rangeToForward } from "@/app/_lib/server/proxyRange";

const BACKEND = process.env.PUFFPAI_BACKEND_URL ?? "http://localhost:8000";
// Server-side only, and deliberately the ONLY spelling accepted. A
// NEXT_PUBLIC_ fallback used to live here; Next inlines those into the client
// bundle, so keeping one would have quietly re-shipped the key to every browser
// the moment someone set it. Empty means no Authorization is attached and the
// backend refuses the call — the safe direction.
const API_KEY = process.env.PUFFPAI_API_KEY ?? "";

/**
 * Hard ceiling on a forwarded request body — document and photo uploads go
 * through here. 50 MB covers those with room to spare while stopping a LAN
 * client from using the appliance's own credential to shove an arbitrary
 * amount of data at the backend. Only Content-Length can be checked cheaply;
 * a chunked body with no declared length streams through to the backend's own
 * limit, which is the backstop for that case.
 */
const MAX_BODY_BYTES = 50 * 1024 * 1024;

export const dynamic = "force-dynamic";

async function proxy(req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  // A browser request that another site initiated never gets the key, whatever
  // it asked for. Checked before the path so a cross-site prober cannot use the
  // 404-vs-403 difference to map the allowlist.
  // WHO is asking, and from where. Cross-site browser requests are refused
  // (403) whatever they carry; unpaired callers are refused (401) whatever they
  // asked for. Both run BEFORE the allowlist so an unauthorised prober cannot
  // map the backend surface by comparing 401/403 against 404.
  const denied = guardCredentialedRoute(req);
  if (denied) return denied;

  const { path } = await params;

  // Next has already decoded each catch-all segment, so an encoded separator
  // (%2f, %5c) arrives as a segment that literally contains "/" or "\" — the
  // only reason to spell a path that way is to make the reconstructed upstream
  // path differ from the one inspected here. isAllowedProxyPath rejects that,
  // along with traversal segments and any first segment not on the list.
  if (!isAllowedProxyPath(path)) return notAllowedResponse();

  const declared = req.headers.get("content-length");
  if (declared) {
    const len = Number(declared);
    if (!Number.isFinite(len) || len < 0) {
      return Response.json({ error: "invalid content-length" }, { status: 400 });
    }
    if (len > MAX_BODY_BYTES) {
      return Response.json(
        { error: "payload too large", maxBytes: MAX_BODY_BYTES },
        { status: 413 },
      );
    }
  }

  const url = `${BACKEND}/${path.join("/")}${req.nextUrl.search}`;

  // Header policy lives in app/_lib/server/proxyHeaders.ts: an ALLOWLIST of the
  // client headers the dashboard genuinely sends, plus the server-derived device
  // identity and the appliance credential. It used to be a relay of every X-*
  // header minus a denylist, which is how a client-chosen X-Client-Id became the
  // key for the backend's vault-unlocked chat session. Read that file before
  // adding a header here.
  // deviceSessionIdFor() re-verifies the cookie (HMAC + registry) and returns an
  // HMAC-derived, per-device value; null when there is no valid cookie, in which
  // case no identity header is sent at all. The caller's own copy of that header
  // is never consulted — upstreamHeaders only relays FORWARDED_CLIENT_HEADERS.
  const headers = upstreamHeaders(req, API_KEY, deviceSessionIdFor(req));
  // A thing's media file only: a byte range, so its video can seek
  // (proxyRange.ts). No other route sends or gets range headers.
  const range = rangeToForward(req.method, path, req.headers.get("range"));
  if (range) headers["Range"] = range;

  const hasBody = req.method !== "GET" && req.method !== "HEAD";
  // A small declared body is read whole: a streamed body turns any 401 from
  // the backend into a fetch network error, which reached the browser as 502
  // "unreachable" (wrong vault PIN, wrong recovery code). See proxyBody.ts.
  // Larger uploads keep streaming.
  const buffered = hasBody && shouldBufferBody(req.method, declared);
  const init: RequestInit & { duplex?: "half" } = {
    method: req.method,
    headers,
    cache: "no-store",
    body: !hasBody ? undefined : buffered ? await req.arrayBuffer() : req.body,
    duplex: hasBody && !buffered ? "half" : undefined, // required for streamed request bodies
  };

  try {
    const upstream = await fetch(url, init);
    const respHeaders = new Headers();
    const passed = ["content-type", "cache-control", "content-disposition"];
    if (req.method === "GET" && isThingMediaFile(path)) passed.push(...RANGE_RESPONSE_HEADERS);
    for (const h of passed) {
      const v = upstream.headers.get(h);
      if (v) respHeaders.set(h, v);
    }
    return new Response(upstream.body, {
      status: upstream.status,
      headers: respHeaders,
    });
  } catch {
    return Response.json({ error: "PuffPAI backend unreachable" }, { status: 502 });
  }
}

export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
