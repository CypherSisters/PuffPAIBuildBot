// Byte ranges through the /api/pai proxy, for ONE route only: a thing's media
// file (GET /things/<id>/media/file), so its video and sound players can seek.
// Pure, no imports, so the test (app/_lib/server/__tests__/proxy-range.test.mjs)
// loads it with Node's type stripping.
//
// WHY. Without this the proxy forwards neither the browser's Range header nor
// the backend's 206 / Content-Range answer, so a video always comes back whole
// from byte 0 and the player cannot jump ahead. The backend already answers
// ranges (Flask send_file, conditional=True) behind the same path checks as a
// whole-file read. Every other route keeps today's header policy
// (proxyHeaders.ts): no Range in, no range headers out.

/** True for GET /things/<id>/media/file and nothing else. */
export function isThingMediaFile(path: readonly string[]): boolean {
  return (
    path.length === 4 &&
    path[0] === "things" &&
    path[1].length > 0 &&
    path[2] === "media" &&
    path[3] === "file"
  );
}

/** One plain byte range ("bytes=0-", "bytes=100-199", "bytes=-500"). */
const RANGE_RE = /^bytes=(\d{1,15}-\d{0,15}|-\d{1,15})$/;

/** The Range header to forward, or null (not that route, or not one plain range). */
export function rangeToForward(
  method: string,
  path: readonly string[],
  range: string | null,
): string | null {
  if ((method || "").toUpperCase() !== "GET" || !isThingMediaFile(path) || !range) return null;
  const value = range.trim();
  return RANGE_RE.test(value) ? value : null;
}

/** Answer headers passed back for that route, beside the usual three. */
export const RANGE_RESPONSE_HEADERS: readonly string[] = [
  "accept-ranges",
  "content-range",
  "content-length",
];
