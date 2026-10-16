/**
 * The one guard every credential-bearing API route calls.
 *
 * WHY IT EXISTS
 * -------------
 * Before this, /api/pai carried both checks (cross-site, then the allowlist) and
 * the other keyed routes carried neither. `POST /api/ha/service` (since
 * removed: nothing called it, and it built the backend URL from body fields
 * that no allowlist checked; /api/pai/ha/... is the one way in now) with
 * `Origin: http://evil.example` and `Content-Type: text/plain` is a CORS-simple
 * request -- no preflight, the browser just sends it -- so any page the owner
 * opened could turn on a light, or read /api/camera/<entity> and get a picture
 * of the inside of the house, using credentials it never had to see. Three
 * routes were open because three separate handlers each had to remember.
 *
 * So there are now two enforcement points and neither is per-handler judgement:
 *   1. middleware.ts, which matches every path and is where a route added next
 *      month is covered without anyone doing anything;
 *   2. this function, called at the top of each credentialled handler, so a
 *      narrowed matcher or a change in Next's middleware semantics cannot
 *      silently open one.
 *
 * ORDER IS DELIBERATE
 * -------------------
 * Cross-site first, pairing second. A cross-site browser request is refused
 * whether or not it carries a valid cookie -- that is the whole point, since the
 * cookie is SameSite=Lax and rides along on some cross-site requests. Refusing
 * it before the cookie is consulted also means the 403 carries no information
 * about whether the victim's browser is paired.
 *
 * WHAT EACH CHECK IS AND IS NOT
 * -----------------------------
 * isCrossSiteRequest() is a browser guard: it reads headers the browser stamps
 * and page script cannot forge, and it says nothing at all about a request that
 * is not from a browser (curl sends no Origin and is allowed through it).
 * requirePairing() is the authentication: no valid device cookie, no entry,
 * browser or not. Neither replaces the other and neither should ever be
 * described as doing the other's job.
 *
 * puff puff pass
 */
import type { NextRequest } from "next/server";
import { crossSiteResponse, isCrossSiteRequest } from "./crossSite";
import { requirePairing } from "./pairing";

/**
 * Returns a Response to return immediately, or null to continue.
 *
 *   const denied = guardCredentialedRoute(req);
 *   if (denied) return denied;
 */
export function guardCredentialedRoute(req: NextRequest): Response | null {
  if (isCrossSiteRequest(req.headers, req.url)) return crossSiteResponse();
  return requirePairing(req);
}
