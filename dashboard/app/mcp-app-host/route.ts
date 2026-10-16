/**
 * GET /mcp-app-host: the locked document an MCP App's screen runs inside.
 *
 * This is the MCP Apps (SEP-1865) "sandbox proxy". A thing page frames this
 * address (things/[id]/McpAppFrame.tsx, sandbox="allow-scripts"); this
 * document then puts the untrusted screen in its own srcdoc frame and passes
 * messages between the two.
 *
 * WHY A DOCUMENT OF ITS OWN: a thing page reached by a client-side
 * navigation (a Link, router.push) keeps the policy of the page the browser
 * first loaded, so a guard in the PAGE's policy depends on how she got there.
 * This response always carries its own policy, and a srcdoc frame inside it
 * inherits that policy, so the screen is locked the same way every time:
 *
 *   - no network at all: connect, images, fonts and media only from inline
 *     data:/blob:, no workers, plugins, base address or form targets;
 *   - frame-src 'none' / child-src 'none': the screen cannot navigate its
 *     own frame anywhere (a sandboxed frame may always navigate itself; its
 *     parent's frame-src, this one, is what decides where);
 *   - `sandbox allow-scripts`: this document is an opaque origin even if it
 *     is opened on its own, so it never reads the dashboard's cookies or
 *     storage; only a dashboard page may frame it (frame-ancestors 'self').
 *
 * The screen cannot navigate this document either: a sandboxed frame may not
 * navigate an ancestor other than the top page, and the top page needs
 * allow-top-navigation, which is never given. This document never navigates.
 *
 * Behind the pairing gate like every page (middleware.ts). The middleware
 * leaves this one path's headers to this route.
 *
 * puff puff pass
 */

/** The policy every MCP App screen runs under. Keep in step with APP_CSP in
 *  McpAppFrame.tsx (the screen's own meta tag, a second copy of the same rule). */
const HOST_CSP =
  "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; " +
  "img-src data: blob:; media-src data: blob:; font-src data:; connect-src 'none'; " +
  "frame-src 'none'; child-src 'none'; worker-src 'none'; object-src 'none'; " +
  "manifest-src 'none'; base-uri 'none'; form-action 'none'; " +
  "frame-ancestors 'self'; sandbox allow-scripts";

/** At most this much HTML is put in the inner frame (the backend caps at 1 MB). */
const MAX_HTML = 1_500_000;

const PROXY = `<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="referrer" content="no-referrer">
<style>html,body{margin:0;padding:0;height:100%;overflow:hidden;background:#fff}iframe{border:0;width:100%;height:100%;display:block}</style>
</head><body><script>
(function () {
  "use strict";
  var inner = null;
  var loaded = false;
  var SANDBOX = "ui/notifications/sandbox-";
  function isRpc(m) { return m && typeof m === "object" && m.jsonrpc === "2.0"; }
  function reserved(m) { return typeof m.method === "string" && m.method.indexOf(SANDBOX) === 0; }
  window.addEventListener("message", function (e) {
    var m = e.data;
    if (!isRpc(m)) return;
    if (e.source === window.parent && window.parent !== window) {
      if (m.method === SANDBOX + "resource-ready") {
        var html = m.params && m.params.html;
        if (loaded || typeof html !== "string" || html.length > ${MAX_HTML}) return;
        loaded = true;
        inner = document.createElement("iframe");
        inner.setAttribute("sandbox", "allow-scripts");
        inner.setAttribute("referrerpolicy", "no-referrer");
        inner.setAttribute("title", "screen");
        inner.srcdoc = html;
        document.body.appendChild(inner);
        return;
      }
      if (reserved(m)) return;
      if (inner && inner.contentWindow) inner.contentWindow.postMessage(m, "*");
      return;
    }
    if (inner && e.source === inner.contentWindow && !reserved(m)) {
      window.parent.postMessage(m, "*");
    }
  });
  window.parent.postMessage({ jsonrpc: "2.0", method: SANDBOX + "proxy-ready", params: {} }, "*");
})();
</script></body></html>`;

export function GET(): Response {
  return new Response(PROXY, {
    headers: {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Security-Policy": HOST_CSP,
      "X-Content-Type-Options": "nosniff",
      "Referrer-Policy": "no-referrer",
      "Cross-Origin-Resource-Policy": "same-origin",
      "Cache-Control": "no-store",
    },
  });
}
