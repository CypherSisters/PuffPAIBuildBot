// How the /api/pai proxy forwards a request body. Pure, no imports, so the
// test (app/_lib/server/__tests__/proxy-body.test.mjs) loads it with Node's
// type stripping.
//
// WHY. If the proxy forwards every body as a stream
// (`body: req.body, duplex: "half"`), then under the Fetch standard a 401 answer
// to a request whose body was a stream is a NETWORK ERROR (the body cannot be
// replayed for an authentication retry), and Node's fetch follows it. So every
// 401 from the backend to a POST with a body reaches the browser as 502
// "backend unreachable": a wrong vault PIN, a wrong recovery code. The Vault
// page then says the vault cannot be reached when the truth is "that code
// does not match". Small bodies (the JSON every form sends) are read
// whole first, which the standard lets through with the real status. Large
// uploads (documents, photos) keep streaming, so memory stays bounded; a 401
// on one of those still shows as 502, which only happens when the proxy's key
// is wrong for the backend.

/** Bodies up to this size (declared) are read whole before forwarding. */
export const BUFFER_BODY_MAX_BYTES = 1024 * 1024;

/**
 * True when the request body should be read whole and forwarded as bytes.
 * Only for a body whose Content-Length is declared, valid and small. No
 * declared length (a chunked upload) keeps streaming.
 */
export function shouldBufferBody(method: string, contentLength: string | null): boolean {
  const m = (method || "").toUpperCase();
  if (m === "GET" || m === "HEAD") return false;
  if (contentLength === null || contentLength.trim() === "") return false;
  const n = Number(contentLength);
  return Number.isInteger(n) && n >= 0 && n <= BUFFER_BODY_MAX_BYTES;
}
