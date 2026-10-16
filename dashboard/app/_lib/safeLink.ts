/** A link a backend row carries, or undefined: only an https address with no
 *  spaces or quotes. The backend already keeps only links on a card's own
 *  service (src/puffpai/feeds.py, item_link); this is the second check. */
export function safeLink(link?: string): string | undefined {
  return link && /^https:\/\/[^\s"<>]+$/.test(link) ? link : undefined;
}

/** Path parts that mark an address for programs (an API), not a page. */
const API_PARTS = new Set(["api", "apis", "rest", "graphql", "ws", "json", "xml", "rpc"]);
const API_VERSION = /^v\d+(?:\.\d+)*$|^\d+(?:\.\d+)+$/;
const DATA_FILE = /\.(?:json|xml|rss|atom|csv)$/i;
const DATA_QUERY = /(?:^|&)(?:format|fmt|output|f)=(?:json|xml)(?:&|$)/i;

/** Does `url` look like an address for programs (an API record) rather than
 *  a page a person reads? The same rule as the backend's feeds.api_address:
 *  an `api` host, a version or `api` part in the path
 *  (`/2.3.0/launches/<id>/`), a data file, or a format=json query. Anything
 *  that cannot be read counts as one. */
export function isApiAddress(link: string): boolean {
  let u: URL;
  try {
    u = new URL(link);
  } catch {
    return true;
  }
  const host = u.hostname.toLowerCase();
  const first = host.split(".")[0] || "";
  if (/(?:^|[.-])api(?:[.-]|$)/.test(first) || host.startsWith("api.")) return true;
  const bits = u.pathname
    .split("/")
    .filter(Boolean)
    .map((b) => b.toLowerCase());
  if (bits.some((b) => API_PARTS.has(b) || API_VERSION.test(b))) return true;
  if (bits.length && DATA_FILE.test(bits[bits.length - 1])) return true;
  return DATA_QUERY.test(u.search.replace(/^\?/, ""));
}

/** The page one item opens, or undefined: an item opens its human page or
 *  nothing, never the raw data it came from (such as
 *  an API's JSON). */
export function itemLink(link?: string): string | undefined {
  const ok = safeLink(link);
  return ok && !isApiAddress(ok) ? ok : undefined;
}
