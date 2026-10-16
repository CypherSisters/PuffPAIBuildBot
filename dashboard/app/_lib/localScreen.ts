/** A local screen a command started, or undefined: only http(s) on
 *  127.0.0.1 or localhost with a port and a plain path (the backend sends
 *  nothing else, see brain_commands.local_addresses; this is the second check). */
export function localScreen(url?: string): string | undefined {
  return url && /^https?:\/\/(127\.0\.0\.1|localhost):\d{2,5}(\/[A-Za-z0-9._~\/%-]*)?$/.test(url)
    ? url
    : undefined;
}
