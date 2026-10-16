/**
 * Which paths the pairing gate covers, and which it deliberately does not.
 *
 *   npm run test:pairing   (this file, then pairing-cookie.test.mjs)
 *
 * Plain Node -- this repo has no test runner, and gateKindFor() is pure
 * path logic that never touches the state file. Requires Node >=22.6 for native
 * TypeScript type stripping. Exits non-zero on any failure.
 *
 * Four properties are asserted:
 *   1. the API surface is DEFAULT-DENY -- every /api path is gated except the
 *      exempt spellings, including a path with no route file behind it;
 *   2. the exempt list is exactly what it claims to be -- /api/pair/*,
 *      /api/health, static assets, and /pair;
 *   3. every route handler that reaches off this process (any process.env read,
 *      either upstream URL, or a fetch) invokes guardCredentialedRoute(), and
 *      the pairing routes that skip it check isPaired()/isSameOrigin()
 *      themselves;
 *   4. property 3 can actually FAIL -- a negative control drops an unguarded
 *      backend proxy into a temp copy of app/api and requires the same scan to
 *      name it, then requires the violation to clear once the guard is added.
 *
 * Properties 1 and 4 exist because a gate that is default-ALLOW outside a few
 * prefixes, or a coverage scan that matches only three literal secret names,
 * lets an unguarded app/api/vaultfiles/route.ts proxying the backend be reached
 * unpaired while every other check in this file still passes.
 *
 * Lives under app/_lib/ (underscore-private), so Next never routes it.
 */
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const { gateKindFor, pathRequiresPairing } = await import("../pairing.ts");

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "..", "..", "..");

let pass = 0;
let fail = 0;

function check(label, got, want) {
  const ok = got === want;
  if (ok) pass++;
  else fail++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}  -> ${got} (want ${want})`);
}

/** Default posture: the gate is on because nothing set the variable. */
delete process.env.PUFF_PAIRING_ENFORCE;

console.log("\n--- default enforcement is ON with no env var set ---");
check("unset -> /dashboard is gated", gateKindFor("/dashboard"), "page");
check("unset -> /api/pai is gated", gateKindFor("/api/pai/health"), "api");
process.env.PUFF_PAIRING_ENFORCE = "ALL";
check("'ALL' (case) -> gated", gateKindFor("/api/pai/health"), "api");
process.env.PUFF_PAIRING_ENFORCE = "knowledge";
check("unrecognised value falls back to gated", gateKindFor("/api/pai/x"), "api");
process.env.PUFF_PAIRING_ENFORCE = "yes-please";
check("typo falls back to gated", gateKindFor("/dashboard"), "page");
process.env.PUFF_PAIRING_ENFORCE = "off";
check("'off' really is off (api)", gateKindFor("/api/pai/x"), "none");
check("'off' really is off (page)", gateKindFor("/dashboard"), "none");
delete process.env.PUFF_PAIRING_ENFORCE;

console.log("\n--- credential-bearing API prefixes are gated ---");
for (const p of [
  "/api/pai",
  "/api/pai/health",
  "/api/pai/ha/service/light/turn_on",
  "/api/pai/vault/contents",
  "/api/pai/v1/chat/completions",
  "/api/knowledge/asset",
  "/api/knowledge/source",
  // The three /api/ha/* route files were removed (nothing called them; the
  // dashboard uses /api/pai/ha/...). Their old addresses stay gated: an
  // unpaired caller still gets the 401, never a 404 that maps what exists.
  "/api/ha/status",
  "/api/ha/entities",
  "/api/ha/service",
  "/api/camera/camera.front_door",
]) {
  check(p, gateKindFor(p), "api");
}

console.log("\n--- encoded / cased / traversal spellings are gated too ---");
check("%2f separator", gateKindFor("/api/pai/knowledge%2fupload"), "api");
check("double-encoded %252f", gateKindFor("/api/pai/knowledge%252fupload"), "api");
check("uppercase", gateKindFor("/API/PAI/HA/status"), "api");
check("encoded first letter", gateKindFor("/api/%70ai/ha/status"), "api");
check("traversal out of /api/pair", gateKindFor("/api/pair/../pai/vault"), "api");
check("encoded traversal out of /api/pair", gateKindFor("/api/pair/%2e%2e/pai/vault"), "api");
check("malformed escape fails closed", gateKindFor("/api/pai/%zz"), "api");
check("lone percent fails closed", gateKindFor("/api/pai/%"), "api");
check("NUL byte fails closed", gateKindFor("/api/pai/x%00"), "api");
check("NUL in a page path fails closed", gateKindFor("/dashboard%00"), "api");

console.log("\n--- pages redirect rather than 401 ---");
for (const p of [
  "/",
  "/dashboard",
  "/dashboard/vault",
  "/dashboard/apps/calendar",
  "/onboarding",
  "/first-run",
  "/trivia-app.html",
  "/calendar-app.html",
  "/something-nobody-has-written-yet",
]) {
  check(p, gateKindFor(p), "page");
}

console.log("\n--- the exempt list, and nothing beyond it ---");
check("/api/pair", gateKindFor("/api/pair"), "none");
check("/api/pair/status", gateKindFor("/api/pair/status"), "none");
check("/api/pair/code", gateKindFor("/api/pair/code"), "none");
check("/api/pair/rotate", gateKindFor("/api/pair/rotate"), "none");
check("/api/pair/devices", gateKindFor("/api/pair/devices"), "none");
check("/api/pair/devices/revoke", gateKindFor("/api/pair/devices/revoke"), "none");
check("/api/health", gateKindFor("/api/health"), "none");
// /api/media/status is a HomeBot route this build does not have. Its exemption
// was removed, so a caller now meets the API gate (401) rather than a 404 that
// would confirm the route is missing -- and a future route file dropped at that
// path does not inherit an unauthenticated exemption nobody argued for.
check("/api/media/status", gateKindFor("/api/media/status"), "api");
check("/pair", gateKindFor("/pair"), "none");
// A path that merely STARTS with an exempt spelling must not inherit it. These
// are gated because EVERY /api path is gated now -- which is also the point:
// "/api/pair" + more characters is not "/api/pair/".
check("/api/pairing-not-a-thing", gateKindFor("/api/pairing-not-a-thing"), "api");
check("/api/paired", gateKindFor("/api/paired"), "api");
check("/api/healthcheck (not /api/health)", gateKindFor("/api/healthcheck"), "api");
check("/pairless (page, not the pair page)", gateKindFor("/pairless"), "page");

console.log("\n--- static assets stay reachable so /pair can render ---");
for (const p of [
  "/puffpai-mascot.svg",
  "/favicon.png",
  "/playback-worklet.js",
  "/vendor/fonts/x.woff2",
  // Nested two levels deep and not at the root: the extension decides, not
  // the directory.
  "/_next/static/media/a.png",
  "/manifest.json",
]) {
  check(p, gateKindFor(p), "none");
}
check("an .html asset is a PAGE, not an asset", gateKindFor("/alarm-app.html"), "page");
check("a dotfile is a page, not an asset", gateKindFor("/.env"), "page");

console.log("\n--- pathRequiresPairing agrees with gateKindFor ---");
check("proxy requires pairing", pathRequiresPairing("/api/pai/health"), true);
check("pair route does not", pathRequiresPairing("/api/pair"), false);
check("health does not", pathRequiresPairing("/api/health"), false);
check("dashboard does", pathRequiresPairing("/dashboard"), true);


/* ── 3. handler coverage: every backend-reaching route must call the guard ── */

console.log("\n--- every API route is gated by default-deny ---");

/** Walk app/api and find every route.ts. */
function walk(dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(full, out);
    else if (entry.name === "route.ts") out.push(full);
  }
  return out;
}

/** app/api/camera/[entityId]/route.ts -> /api/camera/x  (dynamic segments collapse) */
function routePathFor(apiRoot, file) {
  const rel = path
    .relative(path.dirname(apiRoot), path.dirname(file))
    .split(path.sep)
    .join("/");
  return (
    "/" +
    rel
      .split("/")
      .map((seg) => (seg.startsWith("[") ? "x" : seg))
      .join("/")
  );
}

/**
 * The ONLY route paths allowed to skip guardCredentialedRoute().
 *
 * Deliberately spelled out here rather than derived, because "which routes may
 * be open" is exactly the decision a human should have to type out. Two of them
 * carry nothing. The /api/pair/* family carries the pairing endpoints, which
 * cannot require a cookie in order to hand out a cookie, and instead perform
 * their own isPaired() / isSameOrigin() checks -- asserted separately below.
 */
const GUARD_EXEMPT = new Set([
  "/api/health",
  "/api/pair",
  "/api/pair/status",
  "/api/pair/code",
  "/api/pair/rotate",
  "/api/pair/devices",
  "/api/pair/devices/revoke",
]);

/**
 * Does this handler reach outside itself for something that needs protecting?
 *
 * WHY THIS IS NOT A LIST OF LITERAL SECRET NAMES
 * ----------------------------------------------
 * Matching only PUFFPAI_API_KEY / HA_ACCESS_TOKEN / Authorization misses a route
 * that reads some other env var, or simply fetches the backend with no
 * credential at all: an unguarded app/api/vaultfiles/route.ts proxying the
 * backend gets past such a check. Reading ANY environment variable, naming
 * either upstream, or calling fetch() is enough to require the guard. The
 * question a route has to answer is "do you reach off this process", not "do
 * you use one of a few known strings".
 *
 * The false-positive direction is deliberate and cheap: a route that reaches out
 * and is genuinely fine either gets the guard (one line, no behaviour change for
 * a paired caller) or gets added to GUARD_EXEMPT by a human who had to think
 * about it first.
 */
function reachesOutside(src) {
  return (
    /process\.env\./.test(src) ||
    /PUFFPAI_BACKEND_URL|PUFFPAI_API_URL|HA_BASE_URL|HA_URL/.test(src) ||
    /\bfetch\s*\(/.test(src)
  );
}

/** Route files that reach outside but never invoke the shared guard. */
function findUnguardedRoutes(apiRoot) {
  const violations = [];
  for (const file of walk(apiRoot)) {
    const routePath = routePathFor(apiRoot, file);
    if (GUARD_EXEMPT.has(routePath)) continue;
    const src = fs.readFileSync(file, "utf8");
    if (!reachesOutside(src)) continue;
    if (/guardCredentialedRoute\s*\(/.test(src)) continue;
    violations.push(routePath);
  }
  return violations.sort();
}

const API_ROOT = path.join(APP, "api");

// 3a. Default-deny: every route file that exists is gated unless exempt by name.
let gatedCount = 0;
for (const file of walk(API_ROOT)) {
  const routePath = routePathFor(API_ROOT, file);
  const exempt =
    routePath === "/api/health" ||
    routePath === "/api/pair" ||
    routePath.startsWith("/api/pair/");
  check(`${routePath} gate`, gateKindFor(routePath), exempt ? "none" : "api");
  if (!exempt) gatedCount++;
}
check("found routes to check", gatedCount > 0, true);

console.log("\n--- an API path with no route file at all is STILL gated ---");
for (const p of [
  "/api/vaultfiles",
  "/api/admin/keys",
  "/api/anything-added-next-month",
  "/api/health/subpath",
  "/api/media/status/subpath",
  "/api",
]) {
  check(p, gateKindFor(p), "api");
}

// The removed /api/ha/* routes stay removed. /api/ha/service built its backend
// URL from body fields ({"domain":"..","service":"../vault/contents"} came out
// as /vault/contents with the appliance key), so it passed the guard scan
// below while walking around proxyAllowlist.ts. /api/pai/ha/... is the way in.
check("app/api/ha has no route files (removed surface)",
  fs.existsSync(path.join(API_ROOT, "ha")) && walk(path.join(API_ROOT, "ha")).length > 0,
  false);

console.log("\n--- every backend-reaching handler invokes the shared guard ---");
const unguarded = findUnguardedRoutes(API_ROOT);
check(
  `unguarded backend-reaching routes: ${unguarded.join(", ") || "(none)"}`,
  unguarded.length,
  0,
);

console.log("\n--- the pairing routes check pairing themselves ---");
// They skip guardCredentialedRoute because a device with no cookie must be able
// to reach them. That is not a licence to check nothing: the four that read or
// change state verify isPaired() in their own handler, and the three that
// change state verify isSameOrigin().
for (const [rel, needle] of [
  ["pair/code/route.ts", "isPaired"],
  ["pair/devices/route.ts", "isPaired"],
  ["pair/rotate/route.ts", "isPaired"],
  ["pair/devices/revoke/route.ts", "isPaired"],
  ["pair/route.ts", "isSameOrigin"],
  ["pair/rotate/route.ts", "isSameOrigin"],
  ["pair/devices/revoke/route.ts", "isSameOrigin"],
]) {
  const src = fs.readFileSync(path.join(API_ROOT, rel), "utf8");
  check(`${rel} calls ${needle}`, new RegExp(`\\b${needle}\\s*\\(`).test(src), true);
}

/* ── 4. prove the coverage check can actually fail ─────────────────── */

console.log("\n--- NEGATIVE CONTROL: the scratch route the verifier used ---");

/**
 * A check that has never been seen to fail is a check nobody has tested.
 *
 * This copies app/api into a temp directory, drops in the shape a name-matching
 * scan misses (an unguarded route that proxies the backend), and re-runs the
 * SAME findUnguardedRoutes() over the copy. It must name the
 * scratch route. The real tree is never written to.
 */
const scratchRoot = fs.mkdtempSync(path.join(os.tmpdir(), "puff-gate-neg-"));
try {
  fs.cpSync(API_ROOT, path.join(scratchRoot, "api"), { recursive: true });
  const scratchDir = path.join(scratchRoot, "api", "vaultfiles");
  fs.mkdirSync(scratchDir, { recursive: true });

  const UNGUARDED = [
    'import { NextRequest } from "next/server";',
    'const API_URL = process.env.PUFFPAI_BACKEND_URL ?? "http://localhost:8000";',
    "export async function GET(req: NextRequest) {",
    "  const r = await fetch(`${API_URL}/vault/contents`);",
    "  return new Response(await r.text());",
    "}",
    "",
  ].join("\n");
  fs.writeFileSync(path.join(scratchDir, "route.ts"), UNGUARDED, "utf8");

  check(
    "the scratch unguarded proxy IS caught",
    findUnguardedRoutes(path.join(scratchRoot, "api")).includes("/api/vaultfiles"),
    true,
  );
  check("and its path is gated too", gateKindFor("/api/vaultfiles"), "api");

  // Adding the guard to that same file must clear the violation. Without this,
  // the check could be "any new file fails", which is not a check.
  const GUARDED = [
    'import { NextRequest } from "next/server";',
    'import { guardCredentialedRoute } from "@/app/_lib/server/routeGuard";',
    'const API_URL = process.env.PUFFPAI_BACKEND_URL ?? "http://localhost:8000";',
    "export async function GET(req: NextRequest) {",
    "  const denied = guardCredentialedRoute(req);",
    "  if (denied) return denied;",
    "  const r = await fetch(`${API_URL}/vault/contents`);",
    "  return new Response(await r.text());",
    "}",
    "",
  ].join("\n");
  fs.writeFileSync(path.join(scratchDir, "route.ts"), GUARDED, "utf8");

  check(
    "the same route WITH the guard is not flagged",
    findUnguardedRoutes(path.join(scratchRoot, "api")).includes("/api/vaultfiles"),
    false,
  );
} finally {
  fs.rmSync(scratchRoot, { recursive: true, force: true });
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
