/**
 * SSRF guard for the reader route — server-side only, no network I/O in the
 * pure half so it is unit-testable.
 *
 * WHY THIS EXISTS
 * ---------------
 * /api/reader takes a URL from the browser and makes the APPLIANCE fetch it.
 * The appliance sits on the household LAN with a lot of unauthenticated things
 * within reach: the PuffPAI backend on :8000 (holds the API key surface), the
 * dashboard itself on :3003, Home Assistant on 192.168.50.20:8123, Jellyfin on
 * :8096, Node-RED on :1880, the router. A naive fetcher is a pivot straight
 * into all of them. So the rule here is absolute: **the reader may only ever
 * touch public unicast internet addresses.**
 *
 * THREAT MODEL
 *  - Protects against: a link (in a news feed, a chat answer, a knowledge
 *    source, a rendered document) that points at a LAN service; a public host
 *    that 302s to a LAN service; decimal/octal/hex/IPv6-mapped spellings of
 *    loopback; a hostname whose DNS record points at RFC1918 space
 *    ("DNS pinning" attacks like 127.0.0.1.nip.io).
 *  - Does NOT protect against: a remote host serving different content to the
 *    appliance than to the user (it is a fetcher, not an oracle), or an
 *    attacker who already has code execution on the appliance.
 *  - Residual: see resolveAndCheck() on the TOCTOU / DNS-rebinding window.
 *
 * Nothing here is generic "URL validation" — it is deliberately narrow to
 * "public web article the household asked to read".
 *
 * puff puff pass
 */
import dns from "node:dns";
import net from "node:net";

/* ── policy constants ──────────────────────────────────────────────── */

/**
 * Ports the reader may connect to.
 *
 * DELIBERATELY 80/443 ONLY — stricter than it strictly has to be.
 * Reader mode exists for public web articles, and public articles live on the
 * default web ports essentially without exception. Every LAN app the dashboard
 * links to (8123 HA, 8096 Jellyfin, 1880 Node-RED, 8000 backend, 3003 the
 * dashboard) is handled by the overlay's FRAME mode and never reaches this
 * route at all. Narrowing the port set removes the reader's usefulness as a
 * port-scanning oracle against public hosts and costs the household nothing.
 * Widen this list only with a concrete broken link to point at.
 */
export const ALLOWED_PORTS = [80, 443];

/** Schemes. No file:, no gopher:, no data:, no blob:, no javascript:. */
const ALLOWED_PROTOCOLS = ["http:", "https:"];

/** Reason codes. Stable strings — the UI shows a friendly form of these. */
export const REASON = {
  EMPTY: "empty_url",
  UNPARSEABLE: "unparseable_url",
  SCHEME: "blocked_scheme",
  USERINFO: "blocked_userinfo",
  PORT: "blocked_port",
  HOSTNAME: "blocked_hostname",
  DNS: "dns_failed",
  PRIVATE_IP: "blocked_private_address",
} as const;

/* ── IP literal parsing (no BigInt — tsconfig targets es5) ─────────── */

/** Dotted-quad → 4 bytes, or null. Strict: no leading zeros games, 0-255. */
function parseIPv4(s: string): number[] | null {
  const parts = s.split(".");
  if (parts.length !== 4) return null;
  const out: number[] = [];
  for (let i = 0; i < 4; i++) {
    if (!/^\d{1,3}$/.test(parts[i])) return null;
    const n = parseInt(parts[i], 10);
    if (!Number.isFinite(n) || n > 255) return null;
    out.push(n);
  }
  return out;
}

/**
 * IPv6 text → 16 bytes, or null. Handles "::" compression, an embedded
 * dotted-quad tail (::ffff:127.0.0.1) and a %zone suffix.
 */
function parseIPv6(input: string): number[] | null {
  let s = String(input || "");
  const pct = s.indexOf("%");
  if (pct >= 0) s = s.slice(0, pct);
  if (!s) return null;

  // Embedded dotted-quad tail → rewrite as two hex groups so the rest of the
  // parser only ever sees hex groups.
  if (s.indexOf(".") >= 0) {
    const colon = s.lastIndexOf(":");
    if (colon < 0) return null;
    const v4 = parseIPv4(s.slice(colon + 1));
    if (!v4) return null;
    const g1 = ((v4[0] << 8) | v4[1]).toString(16);
    const g2 = ((v4[2] << 8) | v4[3]).toString(16);
    s = s.slice(0, colon + 1) + g1 + ":" + g2;
  }

  let headStr: string;
  let tailStr: string;
  const dbl = s.indexOf("::");
  if (dbl >= 0) {
    // Exactly one "::" is legal.
    if (s.indexOf("::", dbl + 1) >= 0) return null;
    headStr = s.slice(0, dbl);
    tailStr = s.slice(dbl + 2);
  } else {
    headStr = s;
    tailStr = "";
  }

  const head = headStr ? headStr.split(":") : [];
  const tail = tailStr ? tailStr.split(":") : [];
  if (dbl < 0 && head.length !== 8) return null;
  if (head.length + tail.length > 8) return null;

  const groups: number[] = [];
  const pushGroup = (g: string): boolean => {
    if (!/^[0-9a-fA-F]{1,4}$/.test(g)) return false;
    groups.push(parseInt(g, 16));
    return true;
  };
  for (let i = 0; i < head.length; i++) if (!pushGroup(head[i])) return null;
  const zeros = 8 - head.length - tail.length;
  for (let i = 0; i < zeros; i++) groups.push(0);
  for (let i = 0; i < tail.length; i++) if (!pushGroup(tail[i])) return null;
  if (groups.length !== 8) return null;

  const bytes: number[] = [];
  for (let i = 0; i < 8; i++) {
    bytes.push((groups[i] >> 8) & 0xff, groups[i] & 0xff);
  }
  return bytes;
}

/* ── range classification ──────────────────────────────────────────── */

/**
 * Reason an IPv4 address is off-limits, or null if it is public unicast.
 * Blocks every non-globally-routable range, not just the famous three — the
 * appliance has no business reaching any of them.
 */
function ipv4Reason(b: number[]): string | null {
  const [a, c, d] = [b[0], b[1], b[2]];
  if (a === 0) return "this-network 0.0.0.0/8";
  if (a === 10) return "private 10.0.0.0/8";
  if (a === 127) return "loopback 127.0.0.0/8";
  if (a === 100 && c >= 64 && c <= 127) return "CGNAT 100.64.0.0/10";
  if (a === 169 && c === 254) return "link-local 169.254.0.0/16";
  if (a === 172 && c >= 16 && c <= 31) return "private 172.16.0.0/12";
  if (a === 192 && c === 168) return "private 192.168.0.0/16";
  if (a === 192 && c === 0 && d === 0) return "IETF protocol 192.0.0.0/24";
  if (a === 192 && c === 0 && d === 2) return "TEST-NET-1 192.0.2.0/24";
  if (a === 192 && c === 88 && d === 99) return "6to4 relay 192.88.99.0/24";
  if (a === 198 && (c === 18 || c === 19)) return "benchmark 198.18.0.0/15";
  if (a === 198 && c === 51 && d === 100) return "TEST-NET-2 198.51.100.0/24";
  if (a === 203 && c === 0 && d === 113) return "TEST-NET-3 203.0.113.0/24";
  if (a >= 224) return "multicast/reserved 224.0.0.0/3";
  return null;
}

function allZero(b: number[], from: number, to: number): boolean {
  for (let i = from; i < to; i++) if (b[i] !== 0) return false;
  return true;
}

/**
 * Reason an IPv6 address is off-limits, or null if it is public unicast.
 * Every v4-embedding form (mapped, compatible, NAT64, 6to4, Teredo) is either
 * unwrapped and re-checked as v4 or blocked outright — that is the whole point
 * of doing this in bytes rather than string matching.
 */
function ipv6Reason(b: number[]): string | null {
  if (allZero(b, 0, 16)) return "unspecified ::";
  if (allZero(b, 0, 15) && b[15] === 1) return "loopback ::1";

  // ::ffff:0:0/96 — IPv4-mapped. Unwrap and re-check as v4.
  if (allZero(b, 0, 10) && b[10] === 0xff && b[11] === 0xff) {
    const inner = ipv4Reason([b[12], b[13], b[14], b[15]]);
    return inner ? `IPv4-mapped ${inner}` : null;
  }
  // ::a.b.c.d — deprecated IPv4-compatible. Unwrap and re-check.
  if (allZero(b, 0, 12)) {
    const inner = ipv4Reason([b[12], b[13], b[14], b[15]]);
    return inner ? `IPv4-compatible ${inner}` : "IPv4-compatible ::/96";
  }
  // 64:ff9b::/96 and 64:ff9b:1::/48 — NAT64. Unwrap where we can, else block.
  if (b[0] === 0x00 && b[1] === 0x64 && b[2] === 0xff && b[3] === 0x9b) {
    if (allZero(b, 4, 12)) {
      const inner = ipv4Reason([b[12], b[13], b[14], b[15]]);
      return inner ? `NAT64 ${inner}` : null;
    }
    return "NAT64 64:ff9b:1::/48";
  }
  // 100::/64 discard-only.
  if (b[0] === 0x01 && b[1] === 0x00 && allZero(b, 2, 8)) {
    return "discard-only 100::/64";
  }
  // 2001:0000::/32 Teredo — embeds an arbitrary v4 address. Blocked outright.
  if (b[0] === 0x20 && b[1] === 0x01 && b[2] === 0x00 && b[3] === 0x00) {
    return "Teredo 2001::/32";
  }
  // 2001:db8::/32 documentation.
  if (b[0] === 0x20 && b[1] === 0x01 && b[2] === 0x0d && b[3] === 0xb8) {
    return "documentation 2001:db8::/32";
  }
  // 2002::/16 6to4 — embeds an arbitrary v4 address. Blocked outright.
  if (b[0] === 0x20 && b[1] === 0x02) return "6to4 2002::/16";
  // fc00::/7 unique-local.
  if ((b[0] & 0xfe) === 0xfc) return "unique-local fc00::/7";
  // fe80::/10 link-local.
  if (b[0] === 0xfe && (b[1] & 0xc0) === 0x80) return "link-local fe80::/10";
  // ff00::/8 multicast.
  if (b[0] === 0xff) return "multicast ff00::/8";
  return null;
}

/**
 * PURE. Returns a human-readable reason the address is off-limits, or null if
 * it is a public unicast address the reader may connect to.
 *
 * Fails closed: anything that does not parse as a v4 or v6 literal is refused.
 */
export function ipReason(ip: string): string | null {
  const s = String(ip || "").trim();
  // Bracketed literal from a URL host ("[::1]") — strip before parsing.
  const bare =
    s.length > 1 && s[0] === "[" && s[s.length - 1] === "]"
      ? s.slice(1, -1)
      : s;
  if (net.isIPv4(bare)) {
    const b = parseIPv4(bare);
    return b ? ipv4Reason(b) : "unparseable IPv4";
  }
  if (net.isIPv6(bare)) {
    const b = parseIPv6(bare);
    return b ? ipv6Reason(b) : "unparseable IPv6";
  }
  return "not an IP literal";
}

/* ── URL syntax check ──────────────────────────────────────────────── */

export interface UrlOk {
  ok: true;
  url: URL;
  /** hostname with any IPv6 brackets stripped — what DNS/net want. */
  hostname: string;
  port: number;
}
export interface UrlFail {
  ok: false;
  reason: string;
  detail?: string;
}

/**
 * PURE. Scheme / userinfo / port / hostname shape. No DNS, no network.
 *
 * Note on IP spellings: `new URL()` normalises IPv4 written as decimal
 * ("http://2130706433"), octal, hex or short forms into canonical dotted-quad
 * for http(s) URLs, so those spellings arrive here already unmasked and are
 * caught by the IP check downstream.
 */
export function checkUrlSyntax(raw: unknown): UrlOk | UrlFail {
  if (typeof raw !== "string" || !raw.trim()) {
    return { ok: false, reason: REASON.EMPTY };
  }
  if (raw.length > 2048) {
    return { ok: false, reason: REASON.UNPARSEABLE, detail: "url too long" };
  }
  let url: URL;
  try {
    url = new URL(raw.trim());
  } catch {
    return { ok: false, reason: REASON.UNPARSEABLE };
  }

  if (ALLOWED_PROTOCOLS.indexOf(url.protocol) < 0) {
    return { ok: false, reason: REASON.SCHEME, detail: url.protocol };
  }
  // http://user:pass@host — a classic way to make a URL look like it points
  // somewhere it does not, and a way to smuggle credentials outward.
  if (url.username || url.password) {
    return { ok: false, reason: REASON.USERINFO };
  }

  const port = url.port
    ? parseInt(url.port, 10)
    : url.protocol === "https:"
      ? 443
      : 80;
  if (!Number.isFinite(port) || ALLOWED_PORTS.indexOf(port) < 0) {
    return { ok: false, reason: REASON.PORT, detail: String(port) };
  }

  let hostname = url.hostname;
  if (!hostname) return { ok: false, reason: REASON.HOSTNAME };
  if (hostname[0] === "[" && hostname[hostname.length - 1] === "]") {
    hostname = hostname.slice(1, -1);
  }
  // Names that never resolve to anything public. ".local"/".internal" are
  // mDNS/LAN-only; a bare single label is a LAN short name.
  const lower = hostname.toLowerCase();
  if (!net.isIP(hostname)) {
    if (
      /\.(local|localdomain|internal|intranet|home|lan|corp|localhost)$/.test(
        lower,
      ) ||
      lower === "localhost" ||
      lower.indexOf(".") < 0
    ) {
      return { ok: false, reason: REASON.HOSTNAME, detail: lower };
    }
  }

  return { ok: true, url, hostname, port };
}

/* ── DNS + address check ───────────────────────────────────────────── */

export interface TargetOk {
  ok: true;
  url: URL;
  hostname: string;
  port: number;
  /** The single address the caller MUST connect to. */
  ip: string;
  family: 4 | 6;
}

/**
 * Resolve a hostname and refuse it unless EVERY address it maps to is public
 * unicast. All-or-nothing on purpose: a name that returns one public and one
 * private A record would otherwise be a coin-flip pivot.
 *
 * dns.lookup (not dns.resolve) is deliberate — it goes through the OS
 * resolver, so an /etc/hosts entry mapping a public-looking name onto
 * 127.0.0.1 is seen and refused. dns.resolve would miss it entirely.
 *
 * RESIDUAL RISK — TOCTOU / DNS rebinding: between this lookup and the socket
 * connect there is a window in which the record could change. It is closed at
 * the transport layer, not here: readerFetch connects to the exact `ip`
 * returned below and passes the hostname only as a Host header / TLS SNI, so
 * the connection can never land on an address this function did not approve.
 */
export async function resolveAndCheck(
  hostname: string,
): Promise<{ ok: true; ip: string; family: 4 | 6 } | UrlFail> {
  if (net.isIP(hostname)) {
    const reason = ipReason(hostname);
    if (reason) {
      return { ok: false, reason: REASON.PRIVATE_IP, detail: reason };
    }
    return {
      ok: true,
      ip: hostname,
      family: net.isIPv6(hostname) ? 6 : 4,
    };
  }

  let addrs: dns.LookupAddress[];
  try {
    addrs = await dns.promises.lookup(hostname, { all: true, verbatim: true });
  } catch {
    return { ok: false, reason: REASON.DNS, detail: hostname };
  }
  if (!addrs || addrs.length === 0) {
    return { ok: false, reason: REASON.DNS, detail: hostname };
  }

  for (let i = 0; i < addrs.length; i++) {
    const reason = ipReason(addrs[i].address);
    if (reason) {
      return { ok: false, reason: REASON.PRIVATE_IP, detail: reason };
    }
  }
  const first = addrs[0];
  return {
    ok: true,
    ip: first.address,
    family: first.family === 6 ? 6 : 4,
  };
}

/**
 * Full check: syntax, then DNS, then address ranges. This is THE function the
 * reader route and every redirect hop must pass through.
 */
export async function validateTarget(
  raw: unknown,
): Promise<TargetOk | UrlFail> {
  const syn = checkUrlSyntax(raw);
  if (!syn.ok) return syn;
  const res = await resolveAndCheck(syn.hostname);
  if (!res.ok) return res;
  return {
    ok: true,
    url: syn.url,
    hostname: syn.hostname,
    port: syn.port,
    ip: res.ip,
    family: res.family,
  };
}

/** The type of validateTarget — so callers can inject a stub in tests. */
export type TargetValidator = (raw: unknown) => Promise<TargetOk | UrlFail>;

/**
 * PURE. Resolve a Location header against the URL it came from, refusing
 * anything that is not an absolute http(s) URL after resolution.
 */
export function resolveRedirect(current: URL, location: string): string | null {
  const loc = String(location || "").trim();
  if (!loc) return null;
  try {
    return new URL(loc, current).toString();
  } catch {
    return null;
  }
}
