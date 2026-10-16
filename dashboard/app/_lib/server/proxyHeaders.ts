/**
 * Which headers the keyed proxy sends upstream, and where each one comes from.
 *
 * Extracted from app/api/pai/[...path]/route.ts so the policy is a pure
 * function with a test, instead of a loop inside a Next route handler that
 * nothing could exercise without a running server.
 *
 * THE RULE: the client contributes almost nothing. Everything that carries
 * authority is either injected here from server-side state or derived from the
 * pairing cookie the route guard already verified.
 *
 * WHAT WENT WRONG BEFORE
 * ----------------------
 * The route relayed EVERY `X-*` header a client sent, minus a denylist of the
 * X-Forwarded-* family. The backend keyed its 300-second vault-unlocked chat
 * session -- the one that answers protected questions with decrypted vault
 * content and no further PIN -- on `X-Client-Id`, whose default value was the
 * caller's LAN IP. So any paired device could send a household member's
 * `X-Client-Id` and inherit that member's unlock. No denylist could have caught
 * it: the header was not known to be dangerous until it was.
 *
 * Hence an allowlist. A relay default makes every backend header a
 * client-controlled input forever; an allowlist makes each one a decision.
 *
 * puff puff pass
 */

/**
 * Client headers relayed upstream verbatim, lowercased. Add a line here only
 * with a reason, and only for a header the dashboard's own code sends.
 *
 *   x-vault-token -- the vault UNLOCK SESSION token, set by
 *     app/_lib/services/vaultService.ts and app/dashboard/vault/page.tsx.
 *     Without the relay every /vault/* call 403s "Vault is locked" through the
 *     proxy even after a good unlock. It is a session token the backend minted
 *     for this browser, not an identity claim, so relaying the client's copy is
 *     the point of it.
 *
 * Content-Type and Accept are handled separately (they are not X-*, and they
 * are transport rather than application semantics).
 *
 * Deliberately NOT here:
 *   x-client-id        the hole above. Nothing reads it now; relaying it would
 *                      let a future reader become an authorisation hole again.
 *   x-puff-device-id   asserted below from the cookie. A client copy must never
 *                      reach the backend, or the derivation is decorative.
 *   x-puff-anonymous   also asserted below, server-side, and the exact inverse:
 *                      it tells the backend "this proxied caller has NO verified
 *                      identity". A client able to set it could push a paired
 *                      device onto the identity-less path; relaying it in either
 *                      direction would make the marker the client's statement
 *                      instead of ours.
 *   x-api-key          the appliance credential. Injected server-side.
 *   x-forwarded-*      Next/runtime-injected; carry no meaning upstream and
 *   x-real-ip          would misinform the backend's per-IP limiter.
 */
export const FORWARDED_CLIENT_HEADERS: readonly string[] = ["x-vault-token"];

const FORWARD = new Set(FORWARDED_CLIENT_HEADERS);

/** The header the backend keys chat/vault sessions on. Server-set only. */
export const DEVICE_ID_HEADER = "X-Puff-Device-Id";

/**
 * Marker for "this request came through the proxy and has NO verified device
 * identity". Server-set only, and never relayed from a client.
 *
 * WHY A MARKER IS NEEDED AT ALL. Omitting DEVICE_ID_HEADER is ambiguous at the
 * backend: it is what a proxied request with no identity looks like, and ALSO
 * what a direct loopback caller (a script on the box, the CLI) looks like. The
 * backend resolved that ambiguity by keying both on the socket peer, i.e. on
 * `peer:127.0.0.1` — one shared vault-unlock bucket for every unidentified
 * proxied caller, so one caller's 300-second unlock was inheritable by the next.
 *
 * BuildBot issues v2 cookies only, so it does not have HomeBot's legacy-cookie
 * population. It reaches the identity-less state two other ways: with
 * `PUFF_PAIRING_ENFORCE=off` an unpaired browser reaches this proxy at all, and
 * a device revoked between the pairing gate and this call derives no id.
 *
 * With the marker the backend can be explicit instead: a trusted proxy peer that
 * says "anonymous" gets a single-use session key and no vault answers, while a
 * direct caller keeps its per-peer behaviour.
 */
export const ANONYMOUS_HEADER = "X-Puff-Anonymous";

/** Minimal shape read off a request. NextRequest satisfies it. */
export interface HeaderCarrier {
  headers: { get(name: string): string | null; forEach(cb: (v: string, k: string) => void): void };
}

/**
 * Build the upstream header set for a request that has ALREADY passed
 * guardCredentialedRoute() (cross-site refused, pairing cookie verified).
 *
 * @param req            the incoming request; only headers are read
 * @param apiKey         PUFFPAI_API_KEY, or "" to attach no Authorization.
 *                       Empty means the backend refuses the call, which is the
 *                       safe direction for a misconfigured box.
 * @param deviceSession  the value from pairing.deviceSessionIdFor(req), or null
 *                       for "assert no identity". Passed in rather than derived
 *                       here so this function stays pure over its inputs and so
 *                       the caller cannot accidentally hand it something read
 *                       off the request: the only way to get a non-null value is
 *                       to verify a cookie.
 *
 * Authorization is written LAST so a client-sent Authorization can never win;
 * it is not in the allowlist either, so it never gets that far.
 */
export function upstreamHeaders(
  req: HeaderCarrier,
  apiKey: string,
  deviceSession: string | null,
): Record<string, string> {
  const headers: Record<string, string> = {};

  const ct = req.headers.get("content-type");
  if (ct) headers["Content-Type"] = ct;
  const accept = req.headers.get("accept");
  if (accept) headers["Accept"] = accept;

  req.headers.forEach((value: string, key: string) => {
    if (FORWARD.has(key.toLowerCase())) headers[key] = value;
  });

  // WHO the backend should treat this as. Derived server-side from the verified
  // pairing cookie -- an HMAC of the device id under the pairing secret, so it
  // is not the cookie (which must not land in a backend log) and not the raw
  // device id (which any paired caller can read from /api/pair/devices).
  //
  // No valid cookie -> the identity header is absent AND the anonymous marker is
  // present. Exactly one of the two is ever written, both server-side, both
  // after the relay loop so no client copy of either can survive.
  //
  // The marker is not a placeholder identity: the backend turns it into a key
  // unique to that one request, so unidentified callers are isolated from each
  // other as well as from paired devices. A constant placeholder here would have
  // reproduced the shared bucket it exists to remove.
  //
  // The relay loop above writes a forwarded header under the CLIENT's spelling,
  // so the purge is case-insensitive rather than a delete of the canonical name:
  // neither header is on the allowlist today, and if one ever were, a lowercase
  // client copy must still not survive alongside the value we write.
  for (const k of Object.keys(headers)) {
    const lower = k.toLowerCase();
    if (
      lower === DEVICE_ID_HEADER.toLowerCase() ||
      lower === ANONYMOUS_HEADER.toLowerCase()
    ) {
      delete headers[k];
    }
  }
  if (deviceSession) headers[DEVICE_ID_HEADER] = deviceSession;
  else headers[ANONYMOUS_HEADER] = "1";

  if (apiKey) headers["Authorization"] = `Bearer ${apiKey}`;

  return headers;
}
