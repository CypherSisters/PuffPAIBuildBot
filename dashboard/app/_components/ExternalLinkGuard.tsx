"use client";

/**
 * Global link containment. Mounted once in app/dashboard/layout.tsx.
 *
 * THE PROBLEM
 * -----------
 * The appliance is a wall-mounted kiosk (`chromium --kiosk`, launched by
 * ~/.local/bin/puffpai-kiosk.sh): no tab strip, no back button, no window
 * controls, touchscreen only. Any `target="_blank"` — an NPR headline in the
 * News widget, "Open HA", "Open Node-RED", a Spotify tile, a link inside a
 * chat answer or a knowledge source — spawns a chrome-less window with no way
 * back. The household is stuck until somebody SSHes into the Orin.
 *
 * WHY A CATCH-ALL AND NOT PER-CALL-SITE FIXES
 * -------------------------------------------
 * Ten known call sites were only the ones we could grep for. Links also arrive
 * at runtime inside rendered chat answers, knowledge source cards and RSS
 * items, so any list of call sites is stale the moment it is written. This
 * intercepts at the document, in the CAPTURE phase, before React or next/link
 * ever sees the event — so a link added tomorrow is contained for free.
 *
 * WHAT IS DELIBERATELY LEFT ALONE
 *   - Same-origin in-app navigation (every next/link href). Untouched.
 *   - mailto:, tel:, #anchors, javascript: — not ours to intercept.
 *   - Anything carrying `data-external="allow"` (see externalLinks.ts). The
 *     only element in the app with it is the overlay's own "Read on the web"
 *     button, which is not rendered on the kiosk.
 *
 * KIOSK VS LAPTOP — a documented difference
 * On the KIOSK every path is contained, including ctrl/middle/shift-click,
 * because there is no browser chrome to escape a new window with. On a NORMAL
 * browser (a laptop on the LAN at http://192.168.1.50:3003) a modified click —
 * ctrl/cmd/shift/alt or middle button — is allowed through to a real tab: a
 * normal window has a back button and a tab strip, so a new tab is not a trap
 * there, and silently swallowing a deliberate "open in new tab" would be
 * user-hostile. A PLAIN click is still contained everywhere, so the default
 * experience is identical on both.
 *
 * Kiosk-ness is read from `html.puff-kiosk` AT EVENT TIME, not at mount: the
 * Settings page toggles that class live without a remount.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useState } from "react";
import ContainedLinkOverlay, { type ContainedTarget } from "./ContainedLinkOverlay";
import { ALLOW_ATTR, ALLOW_VALUE } from "../_lib/externalLinks";

function isKiosk(): boolean {
  try {
    return document.documentElement.classList.contains("puff-kiosk");
  } catch {
    return false;
  }
}

/** Only http(s) is ours. mailto:/tel:/javascript:/blob: pass straight through. */
function webUrl(href: string): URL | null {
  try {
    const u = new URL(href, window.location.href);
    if (u.protocol !== "http:" && u.protocol !== "https:") return null;
    return u;
  } catch {
    return null;
  }
}

export default function ExternalLinkGuard() {
  const [target, setTarget] = useState<ContainedTarget | null>(null);
  const [kiosk, setKiosk] = useState(false);

  const open = useCallback((href: string, label?: string) => {
    setKiosk(isKiosk());
    setTarget({ href, label });
  }, []);

  useEffect(() => {
    /**
     * Returns true if the event was handled (and already suppressed).
     * `modified` covers ctrl/cmd/shift/alt-click and middle-click.
     */
    const handle = (e: MouseEvent, modified: boolean): boolean => {
      if (e.defaultPrevented) return false;
      const start = e.target;
      if (!start || !(start instanceof Element)) return false;
      const a = start.closest("a[href]") as HTMLAnchorElement | null;
      if (!a) return false;

      // Documented opt-out, honoured on the anchor or any ancestor.
      if (a.closest(`[${ALLOW_ATTR}="${ALLOW_VALUE}"]`)) return false;

      const raw = a.getAttribute("href");
      if (!raw) return false;
      const u = webUrl(raw);
      if (!u) return false;

      const sameOrigin = u.origin === window.location.origin;
      const wantsWindow = a.target === "_blank" || a.target === "_new";
      const kioskNow = isKiosk();

      if (sameOrigin) {
        // In-app navigation: never touched.
        if (!wantsWindow) return false;
        // A same-origin new tab is perfectly escapable in a real browser.
        if (!kioskNow) return false;
        // On the kiosk it is a chrome-less trap — and the destination is one of
        // our own pages, so just go there. The app shell is the way back.
        e.preventDefault();
        e.stopPropagation();
        window.location.assign(u.toString());
        return true;
      }

      // Cross-origin. Contained on the kiosk unconditionally; on a laptop a
      // deliberate modified click still gets a real tab (see header comment).
      if (!kioskNow && modified) return false;

      e.preventDefault();
      e.stopPropagation();
      const label = (a.textContent || "").trim().slice(0, 140);
      open(u.toString(), label || undefined);
      return true;
    };

    const onClick = (e: MouseEvent) => {
      const modified = e.metaKey || e.ctrlKey || e.shiftKey || e.altKey;
      handle(e, modified);
    };
    // Middle-click fires auxclick, not click; without this it would still
    // spawn a window on the kiosk.
    const onAux = (e: MouseEvent) => {
      if (e.button !== 1) return;
      handle(e, true);
    };

    // Capture phase: runs before next/link's own handler and before any
    // stopPropagation() a page component might do.
    document.addEventListener("click", onClick, true);
    document.addEventListener("auxclick", onAux, true);

    /**
     * window.open shim — belt and braces for code that bypasses anchors
     * entirely (app/dashboard/knowledge/page.tsx:466 does exactly this).
     * Returns a stub Window so callers that poke at the result do not throw.
     */
    const originalOpen = window.open;
    const stub = {
      closed: false,
      close() {},
      focus() {},
      blur() {},
      postMessage() {},
      document: null,
      location: null,
    } as unknown as Window;

    const shimmed = function (
      url?: string | URL,
      windowName?: string,
      windowFeatures?: string,
    ): Window | null {
      try {
        const href = typeof url === "string" ? url : url ? url.toString() : "";
        const u = href ? webUrl(href) : null;
        if (u) {
          if (u.origin === window.location.origin) {
            if (isKiosk()) {
              window.location.assign(u.toString());
              return stub;
            }
          } else {
            open(u.toString());
            return stub;
          }
        }
      } catch {
        /* fall through to the real window.open */
      }
      return originalOpen.call(window, url as string, windowName, windowFeatures);
    };
    window.open = shimmed as typeof window.open;

    return () => {
      document.removeEventListener("click", onClick, true);
      document.removeEventListener("auxclick", onAux, true);
      // Only restore if nobody else replaced it after us.
      if (window.open === (shimmed as typeof window.open)) {
        window.open = originalOpen;
      }
    };
  }, [open]);

  if (!target) return null;
  return (
    <ContainedLinkOverlay
      target={target}
      kiosk={kiosk}
      onClose={() => setTarget(null)}
    />
  );
}
