"use client";

/**
 * The containment overlay: where every link that would otherwise leave the
 * dashboard ends up.
 *
 * NON-NEGOTIABLE (this is a touchscreen bolted to a wall, with no keyboard and
 * no browser chrome): there is ALWAYS a way out. Three of them, in fact — a
 * 52px close button in the top-right, tapping the backdrop, and Esc.
 *
 * Two content modes, chosen by containModeFor():
 *   reader       public web page → /api/reader returns EXTRACTED PLAIN TEXT,
 *                which is rendered as text nodes. No dangerouslySetInnerHTML
 *                anywhere in this file, no remote HTML, CSS, script or image
 *                is ever loaded. A hostile page cannot get anything executable
 *                into the dashboard's origin through this path.
 *   frame        a known LAN app (Jellyfin / Node-RED / Home Assistant) whose
 *                whole point is being an app — a text reader would be useless.
 *                Sandboxed iframe INSIDE the panel, so the close button always
 *                stays on top of it.
 *   unavailable  a private address that is not on the frame allowlist. Honest
 *                dead-end panel rather than a blank box.
 *
 * Motion governor: one 180 ms open transition, no persistent animation, no
 * .puff-motion / .puff-live hooks — nothing here runs when idle.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useRef, useState } from "react";
import MsIcon from "./MsIcon";
import { containModeFor, type ContainMode } from "../_lib/externalLinks";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBM = "var(--font-jbmono), monospace";

/** Slightly longer than the server's own 10 s cap, so the server's honest
 *  reason wins the race whenever it can. */
const CLIENT_TIMEOUT_MS = 13_000;
/**
 * An iframe on the LAN that has not fired `load` by now is not coming. Kept
 * short because these are all same-appliance or same-subnet apps.
 *
 * HONEST LIMIT: this catches a frame that never loads (CSP refusal, host down,
 * connection hang). It does NOT catch X-Frame-Options refusal, because Chrome
 * fires `load` on its own "refused to connect" error page. Home Assistant
 * sends `X-Frame-Options: SAMEORIGIN`, so its frame renders Chrome's error
 * page inside the panel. That is not a trap — the header, the source URL and
 * the close button all stay on top of it — but it is why the source strip
 * below is always rendered in frame mode.
 */
const FRAME_LOAD_TIMEOUT_MS = 4_500;

interface Article {
  title: string;
  host: string;
  url: string;
  paragraphs: string[];
  truncated: boolean;
}

type Phase =
  | { s: "loading" }
  | { s: "ok"; article: Article }
  | { s: "error"; reason: string; detail?: string };

/** Reason code → something a human on a wall display can act on. */
function humanReason(reason: string, detail?: string, host?: string): string {
  switch (reason) {
    case "blocked_private_address":
      return "That link points at a device on your home network. PuffPAI won't fetch those.";
    case "blocked_scheme":
    case "blocked_port":
    case "blocked_userinfo":
    case "blocked_hostname":
      return "That isn't an ordinary web address, so PuffPAI won't open it.";
    case "empty_url":
    case "unparseable_url":
      return "That link is malformed.";
    case "dns_failed":
      return "Couldn't find that site.";
    case "timeout":
      return "That site took too long to answer.";
    case "network_error":
      return "Couldn't reach that site.";
    case "http_error":
      return `That site answered with an error${detail ? ` (${detail})` : ""}.`;
    case "unsupported_content_type":
      return `That link is a file${detail ? ` (${detail})` : ""}, not an article.`;
    case "response_too_large":
      return "That page is too big to read here.";
    case "too_many_redirects":
    case "bad_redirect":
      return "That link bounced through too many redirects.";
    case "no_readable_text":
      return `No readable article text at ${host || "that page"}.`;
    case "not_a_page":
      return "That link is data for programs, not a page to read, so it is not shown here.";
    case "rate_limited":
      return "Too many pages at once. Try again in a minute.";
    case "offline":
      return "PuffPAI couldn't start the request.";
    default:
      return "Couldn't fetch that page.";
  }
}

export interface ContainedTarget {
  href: string;
  /** Best-effort label from the link text, shown until the real title lands. */
  label?: string;
}

export default function ContainedLinkOverlay({
  target,
  kiosk,
  onClose,
}: {
  target: ContainedTarget;
  kiosk: boolean;
  onClose: () => void;
}) {
  const [phase, setPhase] = useState<Phase>({ s: "loading" });
  const [frameFailed, setFrameFailed] = useState(false);
  const [shown, setShown] = useState(false);
  const closeRef = useRef<HTMLButtonElement | null>(null);
  const frameLoaded = useRef(false);

  let url: URL | null = null;
  try {
    url = new URL(target.href);
  } catch {
    url = null;
  }
  const mode: ContainMode = url ? containModeFor(url) : "unavailable";
  const host = url ? url.hostname : target.href;

  /* Escape route #3: the keyboard, when there is one. */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        onClose();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  /* One-shot entrance transition; nothing keeps animating afterwards. */
  useEffect(() => {
    const id = requestAnimationFrame(() => setShown(true));
    return () => cancelAnimationFrame(id);
  }, []);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  /* Reader fetch. A body that is data for programs (an API's JSON, even
   * dressed as a web page) is never shown: the reader answers not_a_page. */
  useEffect(() => {
    if (mode !== "reader" || !url) return;
    let cancelled = false;
    setPhase({ s: "loading" });
    fetch("/api/reader", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: url.toString() }),
      cache: "no-store",
      // Belt and braces against a hung server — the UI must never spin forever.
      signal: AbortSignal.timeout(CLIENT_TIMEOUT_MS),
    })
      .then(async (r) => {
        const body = await r.json().catch(() => null);
        if (cancelled) return;
        if (body && body.ok) {
          setPhase({ s: "ok", article: body as Article });
        } else {
          setPhase({
            s: "error",
            reason: (body && body.reason) || `http_${r.status}`,
            detail: body && body.detail,
          });
        }
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        const timedOut = e instanceof Error && e.name === "TimeoutError";
        setPhase({ s: "error", reason: timedOut ? "timeout" : "offline" });
      });
    return () => {
      cancelled = true;
    };
  }, [mode, target.href]); // eslint-disable-line react-hooks/exhaustive-deps

  /* An iframe that never loads is being refused; say so rather than sit blank. */
  useEffect(() => {
    if (mode !== "frame") return;
    // A CSP frame-src refusal is reported to the page, so we can fail fast and
    // honestly instead of waiting out the timeout.
    const onViolation = (e: SecurityPolicyViolationEvent) => {
      if (e.violatedDirective && e.violatedDirective.indexOf("frame-src") === 0) {
        setFrameFailed(true);
      }
    };
    document.addEventListener("securitypolicyviolation", onViolation);
    const id = setTimeout(() => {
      if (!frameLoaded.current) setFrameFailed(true);
    }, FRAME_LOAD_TIMEOUT_MS);
    return () => {
      clearTimeout(id);
      document.removeEventListener("securitypolicyviolation", onViolation);
    };
  }, [mode, target.href]);

  const stop = useCallback((e: React.MouseEvent) => e.stopPropagation(), []);

  const title =
    phase.s === "ok" && phase.article.title
      ? phase.article.title
      : target.label || host;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`${title}, from ${host}`}
      onClick={onClose}
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: 16,
        background: "rgba(11, 7, 20, 0.55)",
        backdropFilter: "blur(10px)",
        WebkitBackdropFilter: "blur(10px)",
        opacity: shown ? 1 : 0,
        transition: "opacity 180ms ease",
      }}
    >
      <div
        onClick={stop}
        style={{
          // Wall-display sizing: the kiosk is ~2560px wide and read from across
          // the room, so a 900px column stranded a lot of screen. Still clearly
          // a contained panel — never full-bleed, backdrop always visible.
          width: "min(1180px, 92vw)",
          height: "min(84vh, 1100px)",
          display: "flex",
          flexDirection: "column",
          minHeight: 0,
          background: "var(--card)",
          backdropFilter: "blur(24px)",
          WebkitBackdropFilter: "blur(24px)",
          border: "1px solid var(--line)",
          borderRadius: 30,
          boxShadow: "var(--shadow)",
          overflow: "hidden",
          transform: shown ? "translateY(0) scale(1)" : "translateY(12px) scale(0.985)",
          transition: "transform 180ms ease",
        }}
      >
        {/* ── header: what you're looking at, where it came from, and OUT ── */}
        <div
          style={{
            display: "flex",
            alignItems: "flex-start",
            gap: 14,
            padding: "18px 18px 14px 24px",
            borderBottom: "1px solid var(--line)",
            flexShrink: 0,
          }}
        >
          <div style={{ minWidth: 0, flex: 1 }}>
            <div
              style={{
                fontFamily: JBM,
                fontWeight: 600,
                fontSize: 10,
                letterSpacing: "0.12em",
                textTransform: "uppercase",
                color: "var(--text3)",
                marginBottom: 4,
              }}
            >
              {mode === "frame" ? "On your network" : "From the web"} · {host}
            </div>
            <div
              style={{
                fontFamily: FREDOKA,
                fontWeight: 600,
                fontSize: "clamp(20px, 1vw, 26px)",
                lineHeight: 1.25,
                color: "var(--text)",
                overflow: "hidden",
                display: "-webkit-box",
                WebkitLineClamp: 2,
                WebkitBoxOrient: "vertical",
              }}
            >
              {title}
            </div>
          </div>

          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label="Close"
            style={{
              flexShrink: 0,
              width: 52,
              height: 52,
              display: "grid",
              placeItems: "center",
              borderRadius: 999,
              border: "1px solid var(--line)",
              background: "var(--card2)",
              color: "var(--text)",
              cursor: "pointer",
            }}
          >
            <MsIcon name="close" size={26} />
          </button>
        </div>

        {/* ── body ── */}
        <div
          style={{
            flex: 1,
            minHeight: 0,
            overflowY: mode === "frame" && !frameFailed ? "hidden" : "auto",
            WebkitOverflowScrolling: "touch",
            display: "flex",
            flexDirection: "column",
          }}
        >
          {mode === "unavailable" && (
            <DeadEnd
              headline="This can't be shown inside PuffPAI"
              body="It's a device on your home network that PuffPAI isn't set up to embed. Open it from a phone or laptop instead."
              url={target.href}
            />
          )}

          {mode === "frame" &&
            (frameFailed ? (
              <DeadEnd
                headline="That app refused to open in here"
                body="It only allows itself to be opened in its own window. Open it from a phone or laptop on the same network."
                url={target.href}
                reason="x_frame_options_or_csp"
              />
            ) : (
              <iframe
                src={target.href}
                title={title}
                onLoad={() => {
                  frameLoaded.current = true;
                }}
                // allow-same-origin is needed for these apps to function at all
                // (they use their own cookies/storage). It does NOT weaken our
                // origin: the frame is cross-origin by construction — same-origin
                // links never reach frame mode, they navigate in-app.
                sandbox="allow-scripts allow-forms allow-same-origin"
                referrerPolicy="no-referrer"
                style={{ flex: 1, width: "100%", border: 0, background: "var(--card2)" }}
              />
            ))}

          {mode === "reader" && phase.s === "loading" && (
            <div style={{ padding: "28px 26px" }}>
              <p style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text2)" }}>
                Fetching the page…
              </p>
              <p
                style={{
                  fontFamily: JBM,
                  fontSize: 11,
                  color: "var(--text3)",
                  marginTop: 8,
                }}
              >
                PuffPAI reads it for you and shows the text only: no ads, no
                trackers, no cookies sent.
              </p>
            </div>
          )}

          {mode === "reader" && phase.s === "error" && (
            <DeadEnd
              headline="Couldn't fetch that page"
              body={humanReason(phase.reason, phase.detail, host)}
              url={target.href}
              reason={phase.reason}
            />
          )}

          {mode === "reader" && phase.s === "ok" && (
            <div style={{ padding: "22px 26px 30px" }}>
              {phase.article.paragraphs.map((p, i) => (
                <p
                  key={i}
                  style={{
                    fontFamily: NUNITO,
                    fontWeight: 400,
                    // Scales up on the wall display, stays sane on a laptop.
                    fontSize: "clamp(15.5px, 0.78vw, 20px)",
                    lineHeight: 1.72,
                    color: "var(--text2)",
                    marginBottom: 16,
                  }}
                >
                  {p}
                </p>
              ))}
              {phase.article.truncated && (
                <p style={{ fontFamily: JBM, fontSize: 11, color: "var(--text3)" }}>
                  (text truncated)
                </p>
              )}
            </div>
          )}
        </div>

        {/* ── footer: only on a real browser, where a tab is escapable ── */}
        {!kiosk && mode === "reader" && (
          <div
            style={{
              flexShrink: 0,
              borderTop: "1px solid var(--line)",
              padding: "12px 20px",
              display: "flex",
              justifyContent: "flex-end",
            }}
          >
            {/* data-external="allow" is the documented opt-out: this is the one
                link in the app that is meant to reach a real browser tab, and
                it is not rendered at all on the kiosk. */}
            <a
              href={target.href}
              target="_blank"
              rel="noreferrer noopener"
              data-external="allow"
              style={{
                fontFamily: NUNITO,
                fontWeight: 600,
                fontSize: 13,
                color: "var(--sky)",
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                minHeight: 44,
              }}
            >
              Read on the web
              <MsIcon name="open_in_new" size={16} />
            </a>
          </div>
        )}
      </div>
    </div>
  );
}

/** Honest dead end: what happened, why, and the raw URL as selectable text. */
function DeadEnd({
  headline,
  body,
  url,
  reason,
}: {
  headline: string;
  body: string;
  url: string;
  reason?: string;
}) {
  return (
    <div style={{ padding: "34px 28px", maxWidth: 620 }}>
      <p
        style={{
          fontFamily: FREDOKA,
          fontWeight: 600,
          fontSize: 19,
          color: "var(--text)",
          marginBottom: 10,
        }}
      >
        {headline}
      </p>
      <p
        style={{
          fontFamily: NUNITO,
          fontSize: 15,
          lineHeight: 1.6,
          color: "var(--text2)",
          marginBottom: 18,
        }}
      >
        {body}
      </p>
      <p
        style={{
          fontFamily: JBM,
          fontSize: 11,
          lineHeight: 1.6,
          color: "var(--text3)",
          wordBreak: "break-all",
        }}
      >
        {url}
        {reason ? ` · ${reason}` : ""}
      </p>
    </div>
  );
}
