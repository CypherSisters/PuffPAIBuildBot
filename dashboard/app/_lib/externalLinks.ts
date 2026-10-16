/**
 * Link containment policy — shared by the CSP in middleware.ts and by the
 * client-side ExternalLinkGuard, so the two can never drift apart.
 *
 * WHY IT EXISTS
 * -------------
 * The appliance is a wall-mounted kiosk. Chromium runs with --kiosk: no tab
 * strip, no back button, no window controls, touchscreen only. A link that
 * opens a new window is a dead end the household cannot escape without SSHing
 * into the Orin. So every link that would leave the app is classified here and
 * routed into an always-escapable overlay instead.
 *
 * Isomorphic on purpose: no node: imports, no DOM access at module scope.
 *
 * puff puff pass
 */

/**
 * Hosts the overlay is allowed to put in an <iframe> — and, identically, the
 * CSP frame-src allowlist.
 *
 * Deliberately an ALLOWLIST of the appliance itself plus the one known LAN
 * service the dashboard links to, not "any private address". CSP has no
 * syntax for IP ranges, and a blanket `frame-src http:` would let any injected
 * markup embed an arbitrary LAN or internet page inside the dashboard's chrome
 * — a ready-made phishing surface. Two hosts, http only:
 *
 *   localhost / 127.0.0.1   the appliance itself: Jellyfin :8096 (already
 *                           framed today), Node-RED :1880, the media proxy
 *
 * These were already hardcoded in middleware.ts for Jellyfin; this keeps
 * that convention in one place. If the appliance's LAN address changes, this
 * list and the CSP move together. Anything NOT listed renders as an honest
 * "can't be shown here" panel rather than a blank iframe.
 */
export const FRAME_HOSTS = ["localhost", "127.0.0.1"];

/** CSP frame-src fragment. Any port on the listed hosts, http only. */
export function frameSrcHosts(): string {
  return FRAME_HOSTS.map((h) => `http://${h}:*`).join(" ");
}

/** RFC1918 / loopback / link-local / CGNAT / mDNS, as a client-side hint. */
export function isPrivateHostname(hostname: string): boolean {
  const h = String(hostname || "").toLowerCase().replace(/^\[|\]$/g, "");
  if (!h) return true;
  if (h === "localhost" || /(^|\.)localhost$/.test(h)) return true;
  if (/\.(local|localdomain|internal|intranet|home|lan|corp)$/.test(h)) return true;
  if (h.indexOf(".") < 0 && h.indexOf(":") < 0) return true; // bare LAN short name
  if (h === "::1" || h.indexOf("fe80:") === 0 || /^f[cd][0-9a-f]{2}:/.test(h)) return true;
  const m = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/.exec(h);
  if (!m) return false;
  const a = +m[1], b = +m[2];
  return (
    a === 0 ||
    a === 10 ||
    a === 127 ||
    (a === 100 && b >= 64 && b <= 127) ||
    (a === 169 && b === 254) ||
    (a === 172 && b >= 16 && b <= 31) ||
    (a === 192 && b === 168) ||
    a >= 224
  );
}

/** True if this host is on the iframe/CSP allowlist. */
export function isFrameHost(hostname: string): boolean {
  const h = String(hostname || "").toLowerCase().replace(/^\[|\]$/g, "");
  return FRAME_HOSTS.indexOf(h) >= 0;
}

export type ContainMode =
  /** Public web page → server-side reader, plain text only. */
  | "reader"
  /** Known LAN app → sandboxed iframe inside the overlay. */
  | "frame"
  /** Private address we are not allowed to frame → honest dead-end panel. */
  | "unavailable";

/** Which overlay mode a contained URL gets. */
export function containModeFor(url: URL): ContainMode {
  if (isFrameHost(url.hostname)) return "frame";
  if (isPrivateHostname(url.hostname)) return "unavailable";
  return "reader";
}

/**
 * Opt-out attribute. Put `data-external="allow"` on an anchor (or any ancestor)
 * that genuinely must open a real browser tab. Nothing in the app carries it
 * today except the overlay's own "Read on the web" button, which is hidden in
 * kiosk mode.
 */
export const ALLOW_ATTR = "data-external";
export const ALLOW_VALUE = "allow";
