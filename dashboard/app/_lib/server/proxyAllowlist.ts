/**
 * Which backend paths /api/pai is allowed to carry the appliance's key to.
 *
 * WHY THIS EXISTS
 * ---------------
 * app/api/pai/[...path] is a catch-all: it takes whatever segments the browser
 * hands it, joins them onto the backend origin, and attaches
 * `Authorization: Bearer <PUFFPAI_API_KEY>`. Without a list, that route is a
 * universal key-bearing tunnel — every endpoint the backend will ever have,
 * including ones added later that nobody thought of as reachable from a
 * browser, is one URL away for anything that can reach port 3003.
 *
 * The list below is the surface the dashboard ACTUALLY uses, derived from the
 * source (see app/_lib/server/__tests__/proxy-allowlist.test.mjs, which
 * re-derives it from the files and fails if the dashboard starts calling
 * something that is not listed). Anything else gets 404 — not 403, so the
 * proxy does not confirm to a prober which backend paths exist.
 *
 * WHAT THIS IS NOT
 * ----------------
 * This is not authentication. It narrows what an unauthenticated LAN client can
 * reach through the key; it does not stop an AUTHORISED client from reaching the
 * allowed surface. Authentication is a separate control (the device-pairing
 * cookie in app/_lib/server/pairing.ts), and the two answer
 * different questions: pairing bounds WHO is asking, this list bounds WHAT they
 * can reach. A paired device still reaches every prefix below, so adding one is
 * still a deliberate act. SECURITY.md describes both controls.
 *
 * ADDING A PREFIX
 * ---------------
 * Add the first backend path segment, alphabetically, with a comment naming the
 * caller. Adding one widens what the key can reach, so it is a deliberate act,
 * not a reflex — if a test fails because a new feature calls something new, the
 * question to ask first is whether that call belongs in the browser at all.
 *
 * Pure module: no next/server import, so the plain-node test can import it
 * directly under Node's TypeScript type stripping.
 *
 * puff puff pass
 */

/**
 * First backend path segment → allowed. Exact match, case-sensitive: every
 * backend route is lowercase, so accepting "HA" would only ever be a way to
 * spell a blocked path differently on a case-insensitive comparison.
 */
export const PROXY_ALLOWED_PREFIXES: readonly string[] = [
  "add", //        app/dashboard/add/addService.ts         /add, /add/find, /add/<id>/confirm|approve, /add/added, /add/notes
  //                (a yes needs a device-bound consent ticket: tests/test_add_more_security.py)
  "agents", //     app/_lib/services/agentService.ts       /agents/status|activity|task
  "announcements", // app/_lib/services/routinesService.ts /announcements
  "assemble", //   app/_lib/services/assembleService.ts    /assemble/status|report|run|yes (putting apps together, her Start)
  "api", //        WidgetBoard.tsx                         /api/media/control (backend's own /api/* routes)
  "apps", //       public/*-app.html, appService.ts        /apps/<app>/data|action|updated, /apps/generate|ocr
  "avatar", //     app/_lib/services/avatarService.ts      /avatar/custom
  "bot", //        app/_lib/services/botPersonaService.ts  /bot/persona, /bot/persona/refresh|woke (its personality)
  "box", //        locked-box pages                        /box/* (setting it up needs a device-bound box_setup ticket)
  "build", //      app/_lib/services/buildService.ts       /build/self|prove|status|job/<id>, /build/yes|knowledge|check/<job>|more/<job>|project|setup/<row> (the jobs, the one setup list, her Yes)
  "chat", //       app/_lib/services/chatService.ts        /chat/history|send|save|approve
  "dashboard", //  dashboard/page.tsx, WidgetBoard.tsx     /dashboard/widgets|cameras|weather|layout
  "developer", // app/dashboard/developer/developerService.ts /developer/cli/<page>, /developer/vault|brain (read-only),
  //                /developer/terminal, /developer/terminal/open|input|output|interrupt|close
  //                (a shell session bound to this paired device: tests/test_developer_terminal.py)
  "devices", //    DeviceDiscoveryToast.tsx, haService.ts  /devices/pending
  "first-run", //  app/_lib/services/firstRunService.ts    /first-run/*
  "fitness", //    app/dashboard/page.tsx                  /fitness/program
  "fix", //        app/_lib/services/chatService.ts        /fix/apply (the chat's fix-it-now cards; switching on
  //                spends a device-bound outside_on ticket: src/puffpai/fix_api.py)
  "front", //      app/_lib/services/frontService.ts       /front (read-only: the home page)
  "github", //   app/_lib/services/githubService.ts      /github/status|token|token-help|disconnect, /github/device/start|poll
  //                (the token goes to the vault, never back to the browser)
  "ha", //         app/_lib/services/haService.ts          /ha/*
  "health", //     app/_lib/api.ts                         /health
  "home", //       app/_lib/services/homeService.ts        /home (the town, for the weather card)
  //                (switching the town lookup on needs a device-bound outside_on ticket: routines_api.home_set)
  "ingest", //     app/dashboard/chat|documents            /ingest/*
  "files", //      app/dashboard/developer/addYourOwnService.ts /files/open (one of her files, in its own program; her data folder, My files or a linked folder only)
  "programs", //   app/dashboard/developer/addYourOwnService.ts /programs, /programs/<id>, /programs/<id>/launch (her own programs and desktop apps)
  "installed", //  app/_lib/services/layoutService.ts     /installed (read-only: the apps, skills and tools on this bot)
  "integrations", // dashboard/apps/[id]/AppHeader.tsx   /integrations/<id>/remove
  "keys", //       app/dashboard/keys/keysService.ts       /keys, /keys/<id>, /keys/<id>/confirm|check|locked|remove
  //                (ON needs a device-bound key_on ticket; no key in any address: tests/test_keys_routes.py)
  "offers", //     app/_lib/services/routinesService.ts    /offers, /offers/<id>/accept|decline
  "outside", //    app/_lib/services/outsideService.ts     /outside, /outside/log, /outside/<id>/off|confirm|on
  //                (ON needs a device-bound consent ticket: tests/test_outside_routes.py)
  "knowledge", //  app/_lib/services/knowledgeService.ts   /knowledge/*
  "looks", //      app/_lib/services/looksService.ts       /looks, /looks/find|use|undo|reset
  "mcp", //        app/_lib/services/mcpService.ts         /mcp/servers, /mcp/home-assistant, /mcp/learn (tool servers)
  "persona", //    app/_lib/services/personaService.ts     /persona/preview|scaffold|list, /persona/<id>
  "sentinel", //   app/_lib/services/consentService.ts     /sentinel/consent/requests
  "profile", //    app/_lib/services/profileService.ts     /profile, /profile/ai
  "routines", //   app/_lib/services/routinesService.ts    /routines/speak
  "setup", //      app/_lib/services/setupService.ts       /setup/*
  "tour", //      app/_lib/services/tourService.ts        /tour, /tour/looks|look|done|again (welcome tour)
  "system", //     systemService.ts, trivia-app.html       /system/status
  "toolsmith", // settings/ToolsmithModelCard.tsx      /toolsmith/model, /toolsmith/model/keep, /toolsmith/model/forget-key
  //                (paired device only; the Anthropic key goes to the vault, never back to the browser)
  "things", //     myThingsService.ts, WelcomeTour.tsx     /things, /things/capabilities, /things/<id>, /things/<id>/home|run|ask|key|lookup|host|host/logs, /things/install-missing (paired device only)
  "v1", //         app/_lib/services/chatService.ts        /v1/chat/completions
  "vault", //      app/_lib/services/vaultService.ts       /vault/*
  "voice", //      app/_lib/services/voiceService.ts       /voice/*
  "webapps", //    dashboard/apps/[id]/WebAppFrame.tsx     /webapps/<id>/origin|abilities (browser apps)
  "wizard", //     the build wizard's card screen          /wizard/cards|apply|state
];

const ALLOWED = new Set(PROXY_ALLOWED_PREFIXES);

/**
 * Segment sanity, applied to EVERY segment, not just the first.
 *
 * Next has already percent-decoded each catch-all segment by the time we see
 * it, so this runs on the real bytes that would be re-joined into the upstream
 * URL. A segment containing "/" or "\" means the client encoded a separator to
 * make the reconstructed path differ from the one that was inspected here; "."
 * and ".." mean traversal; a control character or NUL means someone is probing
 * the upstream parser. None of those has a legitimate spelling in a backend
 * path, so all of them fail closed.
 */
const SAFE_SEGMENT = /^[A-Za-z0-9._~@:-]+$/;

function segmentIsSane(seg: string): boolean {
  if (!seg) return false; // empty segment: "//" collapsed, or a trailing slash
  if (seg === "." || seg === "..") return false;
  if (seg.length > 256) return false;
  return SAFE_SEGMENT.test(seg);
}

/**
 * True if the proxy may forward these already-decoded path segments.
 *
 * Fails closed on anything it does not positively recognise: no segments at
 * all, an unlisted first segment, or any segment that is not a plain path
 * component.
 */
export function isAllowedProxyPath(segments: readonly string[]): boolean {
  if (!Array.isArray(segments) || segments.length === 0) return false;
  for (const seg of segments) {
    if (typeof seg !== "string" || !segmentIsSane(seg)) return false;
  }
  return ALLOWED.has(segments[0]);
}

/** 404, deliberately: a prober learns nothing about the backend's real shape. */
export function notAllowedResponse(): Response {
  return new Response(JSON.stringify({ error: "not_found" }), {
    status: 404,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });
}
