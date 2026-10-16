"use client";

/**
 * "Send from your phone" — QR + household pairing code.
 *
 * Shows a QR encoding <this-host>/send?code=<CODE> so a phone can scan, land
 * on the send page with the code prefilled, and pair with one tap. The host is
 * read from window.location, never hardcoded: the appliance's LAN IP differs
 * per household.
 *
 * GET /api/pair/code answers only already-paired devices. On 401 this renders a
 * single quiet line, not an error — an unpaired kiosk is a normal state, and
 * the operator's escape route is `node scripts/show-pairing-code.js`.
 *
 * QR generation is fully offline: the `qrcode` package is bundled at build
 * time. Nothing is fetched at runtime — the appliance must work with no WAN.
 *
 * CODES EXPIRE (one hour). This card re-fetches itself when the displayed code
 * dies, which matters most on the wall kiosk: that screen can sit untouched for
 * days, and a QR nobody can redeem is worse than no QR at all. The server
 * rotates lazily on fetch, so the re-fetch is what produces the replacement.
 * The timer is the only mechanism — the client never decides a code is valid,
 * it just asks again; expiry is always adjudicated server-side by verifyCode().
 */

import { useCallback, useEffect, useState } from "react";
import QRCode from "qrcode";

/**
 * Never re-fetch faster than this, however stale the server says the code is.
 * A clock skew between the appliance and a browser could otherwise put
 * expiresAt permanently in the past and turn this card into a request loop.
 */
const MIN_REFETCH_MS = 15_000;
/** setTimeout saturates above 2^31-1 ms and fires immediately. Clamp under it. */
const MAX_TIMEOUT_MS = 2_000_000_000;

export default function PairDeviceCard() {
  const [code, setCode] = useState<string | null>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [locked, setLocked] = useState(false);
  /** Bumped to force a re-fetch when the displayed code expires. */
  const [round, setRound] = useState(0);

  const scheduleRefetch = useCallback((expiresAt: unknown): number => {
    const at = typeof expiresAt === "string" ? Date.parse(expiresAt) : NaN;
    if (!Number.isFinite(at)) return MIN_REFETCH_MS;
    return Math.min(
      MAX_TIMEOUT_MS,
      Math.max(MIN_REFETCH_MS, at - Date.now() + 1000),
    );
  }, []);

  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    (async () => {
      try {
        const res = await fetch("/api/pair/code", { cache: "no-store" });
        if (!res.ok) {
          if (live) setLocked(true);
          return;
        }
        const data = (await res.json()) as {
          code?: string;
          expiresAt?: string;
        };
        if (!live || !data?.code) return;
        setCode(data.code);

        const url = `${window.location.origin}/send?code=${encodeURIComponent(data.code)}`;
        // Fixed black-on-white: a QR needs contrast and a quiet zone, and the
        // day/night palettes would otherwise change scan reliability.
        const markup = await QRCode.toString(url, {
          type: "svg",
          margin: 1,
          width: 132,
          errorCorrectionLevel: "M",
          color: { dark: "#000000", light: "#ffffff" },
        });
        if (!live) return;
        setSvg(markup);

        timer = setTimeout(() => {
          if (live) setRound((n) => n + 1);
        }, scheduleRefetch(data.expiresAt));
      } catch {
        if (live) setLocked(true);
      }
    })();

    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [round, scheduleRefetch]);

  if (locked) {
    return (
      <p className="mt-4 text-xs" style={{ color: "var(--text3)" }}>
        Pair this screen to show the phone code.
      </p>
    );
  }

  if (!code) return null;

  return (
    <div
      className="mt-4 flex items-center gap-4 p-3"
      style={{
        background: "var(--card2)",
        border: "1px solid var(--line)",
        borderRadius: 18,
      }}
    >
      <div
        className="shrink-0 overflow-hidden"
        style={{ background: "#fff", borderRadius: 12, padding: 6, lineHeight: 0 }}
        aria-hidden="true"
        // Markup is produced locally by the bundled qrcode encoder from a URL
        // we build ourselves — no user or network input reaches it.
        dangerouslySetInnerHTML={{ __html: svg ?? "" }}
      />
      <div className="min-w-0">
        <div
          className="text-sm font-semibold"
          style={{ color: "var(--text)", fontFamily: "var(--font-fredoka)" }}
        >
          Send from your phone
        </div>
        <div className="mt-0.5 text-xs" style={{ color: "var(--text2)" }}>
          Scan, or open /send and type
        </div>
        <div
          className="mt-1"
          style={{
            fontFamily: "var(--font-jbmono)",
            fontSize: 17,
            letterSpacing: "0.14em",
            color: "var(--pink)",
          }}
        >
          {/* Already grouped as XXXX-XXXX by the server's formatCode(). */}
          {code}
        </div>
        <div className="mt-1 text-xs" style={{ color: "var(--text3)" }}>
          This code expires in an hour, and a fresh one appears automatically.
        </div>
      </div>
    </div>
  );
}
