/**
 * End-to-end proof, against a real `next start`.
 *
 *   npm run build && npm run test:pairing:runtime
 *
 * The unit tests prove the gate logic and the cookie maths. This one proves the
 * thing that actually matters: that a stranger who can reach the port gets
 * nothing, that the owner who types the code gets everything, and that the
 * appliance's credential reaches the backend only for the second one.
 *
 * WHAT IT STANDS UP
 * -----------------
 *   - a stub upstream on 127.0.0.1 that answers every path and reports back
 *     which path it saw and whether the Authorization header held the fixture
 *     key. It stands in for BOTH the PuffPAI backend and Home Assistant.
 *   - `next start` against that stub, with the pairing state pointed at a
 *     throwaway temp directory (PUFF_PAIRING_STATE / PUFF_PAIRED_DEVICES). No
 *     real pairing state is read or written and no real credential is used --
 *     the key and the HA token are fixture strings that exist only in this file.
 *
 * Requires a production build to exist (`npm run build`). Exits non-zero on any
 * failure and always tears down the two servers.
 *
 * Lives under app/_lib/ (underscore-private), so Next never routes it.
 */
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DASHBOARD = path.resolve(HERE, "..", "..", "..", "..");

const DASH_PORT = 3199;
const STUB_PORT = 3198;
const BASE = `http://127.0.0.1:${DASH_PORT}`;

/** Fixture credentials. Not real, never were, and never leave this file. */
const FIXTURE_KEY = "FIXTUREKEY0000000000NOTREAL";
const FIXTURE_HA_TOKEN = "FIXTUREHATOKEN0000000NOTREAL";

const SANDBOX = fs.mkdtempSync(path.join(os.tmpdir(), "puff-pair-rt-"));

let pass = 0;
let fail = 0;
function check(label, got, want) {
  const ok = got === want;
  if (ok) pass++;
  else fail++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}  -> ${got} (want ${want})`);
}
function ok(label, cond, detail = "") {
  check(`${label}${detail ? " " + detail : ""}`, !!cond, true);
}

/* ── the stub upstream ─────────────────────────────────────────────── */

const seen = [];
const stub = http.createServer((req, res) => {
  const auth = req.headers["authorization"] || "";
  const keyed =
    auth === `Bearer ${FIXTURE_KEY}` || auth === `Bearer ${FIXTURE_HA_TOKEN}`;
  seen.push({ url: req.url, keyed });
  res.writeHead(200, { "Content-Type": "application/json" });
  res.end(JSON.stringify({ upstream: true, path: req.url, keyed }));
});

/* ── the dashboard ─────────────────────────────────────────────────── */

let dash;

/**
 * Refuse to run if something is already listening on our port.
 *
 * This is not tidiness. Without it, a leftover server from a previous run
 * answers the readiness probe, every request in this run goes to THAT process
 * -- which is pointed at a temp directory this run has never heard of -- and the
 * failures that come back are noise about a state file rather than anything
 * about pairing. A security test that can silently grade the wrong process is
 * worse than no test.
 */
async function assertPortFree(port, what) {
  try {
    const r = await fetch(`http://127.0.0.1:${port}/api/health`, {
      signal: AbortSignal.timeout(1500),
    });
    throw new Error(
      `something is already listening on ${port} (${what}); it answered ${r.status}. ` +
        `Stop it and re-run -- this test must not grade another process.`,
    );
  } catch (err) {
    if (err instanceof Error && err.message.includes("already listening")) throw err;
    /* connection refused / timeout: the port is free, which is what we want */
  }
}

async function startDashboard() {
  dash = spawn(
    process.platform === "win32" ? "npx.cmd" : "npx",
    ["next", "start", "-p", String(DASH_PORT)],
    {
      cwd: DASHBOARD,
      env: {
        ...process.env,
        NODE_ENV: "production",
        PUFFPAI_BACKEND_URL: `http://127.0.0.1:${STUB_PORT}`,
        PUFFPAI_API_KEY: FIXTURE_KEY,
        HA_BASE_URL: `http://127.0.0.1:${STUB_PORT}`,
        HA_ACCESS_TOKEN: FIXTURE_HA_TOKEN,
        PUFF_PAIRING_STATE: path.join(SANDBOX, "pairing.json"),
        PUFF_PAIRED_DEVICES: path.join(SANDBOX, "devices.json"),
        // Deliberately UNSET, to prove the default is "all".
        PUFF_PAIRING_ENFORCE: undefined,
      },
      stdio: ["ignore", "pipe", "pipe"],
      shell: process.platform === "win32",
    },
  );
  dash.stdout.on("data", () => {});
  dash.stderr.on("data", (d) => process.stderr.write(`[next] ${d}`));

  const deadline = Date.now() + 60_000;
  for (;;) {
    try {
      const r = await fetch(`${BASE}/api/health`);
      if (r.ok) return;
    } catch {
      /* not up yet */
    }
    if (Date.now() > deadline) throw new Error("dashboard did not start");
    await new Promise((r) => setTimeout(r, 400));
  }
}

/* ── request helpers ───────────────────────────────────────────────── */

function get(p, { cookie, origin, method = "GET", body, ct } = {}) {
  const headers = {};
  if (cookie) headers.cookie = `puff_device=${cookie}`;
  if (origin) {
    headers.origin = origin;
    headers["sec-fetch-site"] =
      origin === BASE || origin === `http://127.0.0.1:${DASH_PORT}`
        ? "same-origin"
        : "cross-site";
  }
  if (ct) headers["content-type"] = ct;
  return fetch(`${BASE}${p}`, {
    method,
    headers,
    body,
    redirect: "manual",
  });
}

function cookieFrom(res) {
  const sc = res.headers.getSetCookie?.() ?? [];
  for (const line of sc) {
    const m = /^puff_device=([^;]*)/.exec(line);
    if (m && m[1]) return { value: m[1], line };
  }
  return null;
}

/** Read the code the way the owner does: off the box, not over HTTP. */
function codeFromDisk() {
  const st = JSON.parse(
    fs.readFileSync(path.join(SANDBOX, "pairing.json"), "utf8"),
  );
  return `${st.code.slice(0, 4)}-${st.code.slice(4)}`;
}

/**
 * Every route handler that reads a secret from the environment.
 * Derived by hand from `grep -rn "process.env." app/api` and asserted against
 * that grep in pairing-gate.test.mjs, which fails if a new one appears.
 */
const CREDENTIALLED = [
  ["/api/pai/health", "GET", "PUFFPAI_API_KEY"],
  ["/api/pai/ha/service/light/turn_on", "POST", "PUFFPAI_API_KEY"],
  ["/api/pai/vault/contents", "GET", "PUFFPAI_API_KEY"],
  // The Home Assistant reads, by the path the dashboard uses (haService.ts).
  // These replace the old /api/ha/status and /api/ha/entities rows: those
  // route files were removed, and REMOVED below proves what is left there.
  ["/api/pai/ha/status", "GET", "PUFFPAI_API_KEY"],
  ["/api/pai/ha/entities", "GET", "PUFFPAI_API_KEY"],
  ["/api/camera/camera.front_door", "GET", "HA_ACCESS_TOKEN"],
  ["/api/knowledge/asset?collection=c&path=x", "GET", "(backend reach, keyless)"],
  ["/api/knowledge/source?name=c", "GET", "(backend reach, keyless)"],
];

/**
 * Route files that were removed because nothing called them. Their addresses
 * must stay closed: 401 unpaired (the middleware gate, before any routing),
 * 403 cross-site, 404 paired, and the backend never hears of any of them.
 */
const REMOVED = [
  ["/api/ha/status", "GET"],
  ["/api/ha/entities", "GET"],
  ["/api/ha/service", "POST"],
];

/* ── the run ───────────────────────────────────────────────────────── */

async function run() {
  console.log("\n--- UNPAIRED: nothing credentialled answers ---");
  for (const [p, method] of CREDENTIALLED) {
    const before = seen.length;
    const r = await get(p, { method, ct: "application/json", body: method === "POST" ? "{}" : undefined });
    check(`unpaired ${method} ${p}`, r.status, 401);
    ok(`unpaired ${method} ${p} never reached upstream`, seen.length === before);
  }
  const j = await (await get("/api/pai/health")).json();
  check("the 401 body names the reason", j.error, "pairing_required");

  console.log("\n--- UNPAIRED: the removed /api/ha/* addresses are still gated ---");
  for (const [p, method] of REMOVED) {
    const before = seen.length;
    const r = await get(p, { method, ct: "application/json", body: method === "POST" ? "{}" : undefined });
    check(`unpaired ${method} ${p}`, r.status, 401);
    ok(`unpaired ${method} ${p} never reached upstream`, seen.length === before);
  }

  console.log("\n--- UNPAIRED: pages redirect to the pair page ---");
  for (const p of ["/", "/dashboard", "/dashboard/vault", "/onboarding", "/trivia-app.html"]) {
    const r = await get(p);
    check(`unpaired GET ${p}`, r.status, 307);
    const loc = r.headers.get("location") || "";
    ok(`... redirects to /pair`, new URL(loc, BASE).pathname === "/pair", `(${loc})`);
  }
  const withNext = await get("/dashboard/vault");
  ok("the redirect preserves where they were going",
    (withNext.headers.get("location") || "").includes("next=%2Fdashboard%2Fvault"));

  console.log("\n--- UNPAIRED: the pair page and its API are reachable ---");
  check("/pair renders", (await get("/pair")).status, 200);
  check("/api/pair/status answers", (await get("/api/pair/status")).status, 200);
  check("/api/health answers (probe, no cookie)", (await get("/api/health")).status, 200);
  // /api/media/status used to be asserted here. It is a HomeBot route the port
  // dropped, so the assertion failed on a 404 and aborted every check after it.
  // Its exemption is gone too, so the live expectation is the gate: 401.
  check("/api/media/status is gated, not exempt",
    (await get("/api/media/status")).status, 401);
  const statusBody = await (await get("/api/pair/status")).json();
  check("status says unpaired", statusBody.paired, false);
  check("status says enforcement defaults to all", statusBody.enforce, "all");
  check("the code is NOT readable unpaired", (await get("/api/pair/code")).status, 401);
  check("the device list is NOT readable unpaired",
    (await get("/api/pair/devices")).status, 401);
  const asset = await get("/puffpai-mascot.svg");
  check("a static asset still loads (so /pair can be styled)", asset.status, 200);

  console.log("\n--- WRONG CODE: 401, then rate limited ---");
  for (let i = 1; i <= 5; i++) {
    const r = await get("/api/pair", {
      method: "POST",
      origin: BASE,
      ct: "application/json",
      body: JSON.stringify({ code: "AAAA-AAAA" }),
    });
    check(`wrong code attempt ${i}`, r.status, 401);
    check(`... names the reason`, (await r.json()).error, "invalid_code");
  }
  const limited = await get("/api/pair", {
    method: "POST",
    origin: BASE,
    ct: "application/json",
    body: JSON.stringify({ code: "AAAA-AAAA" }),
  });
  check("6th attempt is rate limited", limited.status, 429);
  check("... names the reason", (await limited.json()).error, "rate_limited");
  ok("... and says how long to wait", !!limited.headers.get("Retry-After"));
  // A rate-limited client cannot pair even WITH the right code -- the limiter
  // runs before any comparison, so it cannot be used as an oracle.
  const rightButLimited = await get("/api/pair", {
    method: "POST",
    origin: BASE,
    ct: "application/json",
    body: JSON.stringify({ code: codeFromDisk() }),
  });
  check("the RIGHT code is refused while limited", rightButLimited.status, 429);
  ok("... and set no cookie", cookieFrom(rightButLimited) === null);

  console.log("\n--- waiting out the rate-limit window (61s) ---");
  await new Promise((r) => setTimeout(r, 61_000));

  console.log("\n--- CROSS-SITE: refused before anything else ---");
  const xs = await get("/api/pair", {
    method: "POST",
    origin: "http://evil.example",
    ct: "application/json",
    body: JSON.stringify({ code: codeFromDisk() }),
  });
  check("cross-site pair attempt", xs.status, 403);
  ok("... set no cookie", cookieFrom(xs) === null);

  console.log("\n--- RIGHT CODE: cookie set, and it works ---");
  const paired = await get("/api/pair", {
    method: "POST",
    origin: BASE,
    ct: "application/json",
    body: JSON.stringify({ code: codeFromDisk(), name: "test device" }),
  });
  check("pairing succeeds", paired.status, 200);
  check("... says so", (await paired.json()).paired, true);
  const jar = cookieFrom(paired);
  ok("... and sets a cookie", jar !== null);
  ok("the cookie is HttpOnly", /HttpOnly/i.test(jar.line));
  ok("the cookie is SameSite=Lax", /SameSite=Lax/i.test(jar.line));
  ok("the cookie is path=/", /Path=\//i.test(jar.line));
  ok("the cookie is the v2 five-field format",
    /^v2\.[A-Za-z0-9_-]{12}\.\d+\.\d+\.[0-9a-f]{64}$/.test(jar.value));
  const cookie = jar.value;

  console.log("\n--- PAIRED: every credentialled route forwards, keyed ---");
  for (const [p, method, credential] of CREDENTIALLED) {
    const before = seen.length;
    const r = await get(p, {
      cookie,
      origin: BASE,
      method,
      ct: "application/json",
      body: method === "POST" ? JSON.stringify({ domain: "light", service: "turn_on" }) : undefined,
    });
    ok(`paired ${method} ${p} is not refused`, r.status !== 401 && r.status !== 403,
      `(status ${r.status})`);
    ok(`paired ${method} ${p} reached upstream`, seen.length > before);
    const last = seen[seen.length - 1];
    // The two knowledge proxies reach the backend WITHOUT a credential on
    // purpose -- the upstream asset/source endpoints are unauthenticated. They
    // are gated all the same, because they reach the backend on the server's
    // behalf, and the unpaired block above proved that they refuse a stranger.
    // Asserting "keyed" for them would be asserting a credential we
    // deliberately do not send.
    const expectsKey = credential.startsWith("PUFFPAI") || credential.startsWith("HA_");
    if (last && expectsKey) {
      ok(`... carrying the appliance credential`, last.keyed);
    } else if (last) {
      ok(`... reaching upstream unkeyed, as designed`, last.keyed === false);
    }
  }

  console.log("\n--- PAIRED but CROSS-SITE: still 403 on every one ---");
  for (const [p, method] of CREDENTIALLED) {
    const before = seen.length;
    const r = await get(p, {
      cookie,
      origin: "http://evil.example",
      method,
      // text/plain is CORS-simple: no preflight, the browser just sends it.
      ct: "text/plain",
      body: method === "POST" ? "{}" : undefined,
    });
    check(`cross-site paired ${method} ${p}`, r.status, 403);
    ok(`... never reached upstream`, seen.length === before);
  }

  console.log("\n--- PAIRED: the removed /api/ha/* addresses are gone, not open ---");
  for (const [p, method] of REMOVED) {
    const before = seen.length;
    const r = await get(p, {
      cookie,
      origin: BASE,
      method,
      ct: "application/json",
      // The body that used to walk /api/ha/service out of the allowlist.
      body: method === "POST"
        ? JSON.stringify({ domain: "..", service: "../vault/contents" })
        : undefined,
    });
    check(`paired ${method} ${p}`, r.status, 404);
    ok(`... never reached upstream`, seen.length === before);
    const xr = await get(p, {
      cookie,
      origin: "http://evil.example",
      method,
      ct: "text/plain",
      body: method === "POST" ? "{}" : undefined,
    });
    check(`cross-site paired ${method} ${p}`, xr.status, 403);
    ok(`... never reached upstream`, seen.length === before);
  }

  console.log("\n--- PAIRED: pages render, the code and device list open up ---");
  check("/dashboard renders", (await get("/dashboard", { cookie })).status, 200);
  check("/api/pair/code answers", (await get("/api/pair/code", { cookie })).status, 200);
  const devices = await (await get("/api/pair/devices", { cookie })).json();
  ok("the device we just paired is listed",
    devices.devices.some((d) => d.name === "test device"));
  ok("and it is marked as this device", devices.self === devices.devices.at(-1).id);

  console.log("\n--- ROTATION: the old code dies, the paired device does not ---");
  const oldCode = codeFromDisk();
  const rot = await get("/api/pair/rotate", { method: "POST", cookie, origin: BASE });
  check("rotate succeeds for a paired caller", rot.status, 200);
  const newCode = (await rot.json()).code;
  ok("the code changed", newCode !== oldCode);
  const withOld = await get("/api/pair", {
    method: "POST",
    origin: BASE,
    ct: "application/json",
    body: JSON.stringify({ code: oldCode }),
  });
  check("the OLD code no longer pairs", withOld.status, 401);
  const withNew = await get("/api/pair", {
    method: "POST",
    origin: BASE,
    ct: "application/json",
    body: JSON.stringify({ code: newCode }),
  });
  check("the NEW code does", withNew.status, 200);
  check("and the already-paired device is unaffected",
    (await get("/api/pai/health", { cookie, origin: BASE })).status, 200);
  check("an unpaired caller still cannot rotate",
    (await get("/api/pair/rotate", { method: "POST", origin: BASE })).status, 401);

  console.log("\n--- REVOCATION: the cookie stops working on the next request ---");
  const second = cookieFrom(withNew).value;
  check("the second device works", (await get("/api/pai/health", { cookie: second, origin: BASE })).status, 200);
  const list = await (await get("/api/pair/devices", { cookie })).json();
  const victim = list.devices.find((d) => d.id !== list.self);
  const rev = await get("/api/pair/devices/revoke", {
    method: "POST",
    cookie,
    origin: BASE,
    ct: "application/json",
    body: JSON.stringify({ id: victim.id }),
  });
  check("revoke succeeds", rev.status, 200);
  check("the revoked cookie is now refused",
    (await get("/api/pai/health", { cookie: second, origin: BASE })).status, 401);
  check("the revoking device is unaffected",
    (await get("/api/pai/health", { cookie, origin: BASE })).status, 200);
  check("an unpaired caller cannot revoke",
    (await get("/api/pair/devices/revoke", {
      method: "POST", origin: BASE, ct: "application/json",
      body: JSON.stringify({ id: list.self }),
    })).status, 401);

  console.log("\n--- security headers: on pages, off the framed app files ---");
  for (const p of ["/dashboard", "/pair", "/onboarding"]) {
    const h = (await get(p, { cookie })).headers;
    check(`${p} X-Frame-Options`, h.get("x-frame-options"), "DENY");
    ok(`${p} CSP says frame-ancestors 'none'`,
      (h.get("content-security-policy") || "").includes("frame-ancestors 'none'"));
    check(`${p} nosniff`, h.get("x-content-type-options"), "nosniff");
  }
  // REGRESSION GUARD. /dashboard/apps/trivia/play iframes this file from the
  // dashboard's own origin. A framing header here renders the browser's
  // "refused to connect" box where the game should be, and all five app pages
  // are framed the same way. It is still pairing-gated -- proved above.
  const appPage = await get("/trivia-app.html", { cookie });
  check("the framed app page is reachable when paired", appPage.status, 200);
  ok("... and carries NO framing header, so the iframe still renders",
    appPage.headers.get("x-frame-options") === null,
    `(got ${appPage.headers.get("x-frame-options")})`);

  console.log("\n--- no page anywhere asks for a password ---");
  // This assertion used to demand a 404: the template login page, which accepted
  // any username/password and pushed the browser to /dashboard, had been DELETED.
  // The port replaced it with an explainer (app/login/page.tsx) that says this
  // appliance has no accounts and points at pairing -- because a bookmarked
  // /login silently 404ing teaches nobody, while a convincing login box invites a
  // household to type a real password into a box that checks nothing.
  //
  // So the property under test is not "the path is gone", it is "no page asks for
  // a credential". Assert THAT, on the page body, for paired and unpaired alike.
  const loginRes = await get("/login", { cookie });
  const loginHtml = await loginRes.text();
  ok("/login renders an explainer or is gone entirely",
    loginRes.status === 200 || loginRes.status === 404,
    `(status ${loginRes.status})`);
  if (loginRes.status === 200) {
    ok("/login has no password input", !/type=["']password["']/.test(loginHtml));
    ok("/login has no form to submit a credential to", !/<form[\s>]/i.test(loginHtml));
    ok("/login has no text input at all", !/<input[\s>]/i.test(loginHtml));
    ok("/login says there is no account to log into",
      /no\s+(account|logins?|password)/i.test(loginHtml));
  }
  const loginUnpaired = await get("/login");
  const unpairedHtml = await loginUnpaired.text();
  ok("/login is gated, 404, or an explainer for an unpaired caller -- never a form",
    loginUnpaired.status === 404 ||
    loginUnpaired.status === 307 ||
    (loginUnpaired.status === 200 && !/<input[\s>]/i.test(unpairedHtml)),
    `(status ${loginUnpaired.status})`);
  for (const p of ["/pair", "/dashboard/profile"]) {
    const html = await (await get(p, { cookie })).text();
    ok(`${p} has no type="password" input`, !/type=["']password["']/.test(html));
  }
}

/* ── go ────────────────────────────────────────────────────────────── */

let exitCode = 1;
try {
  await assertPortFree(DASH_PORT, "a previous run's next start?");
  await new Promise((r) => stub.listen(STUB_PORT, "127.0.0.1", r));
  await startDashboard();
  await run();
  console.log(`\n${pass} passed, ${fail} failed`);
  exitCode = fail === 0 ? 0 : 1;
} catch (err) {
  console.error("\nFATAL", err);
  exitCode = 1;
} finally {
  if (dash) {
    // AWAIT the teardown. `next start` under npx on Windows is a process tree,
    // so an un-awaited kill leaves the port held and the NEXT run silently
    // grades the corpse (which assertPortFree now refuses to do, loudly).
    const exited = new Promise((r) => dash.once("exit", r));
    try {
      if (process.platform === "win32") {
        spawn("taskkill", ["/pid", String(dash.pid), "/T", "/F"], { stdio: "ignore" });
      } else {
        dash.kill("SIGTERM");
      }
    } catch {
      /* already gone */
    }
    await Promise.race([exited, new Promise((r) => setTimeout(r, 10_000))]);
  }
  stub.close();
  fs.rmSync(SANDBOX, { recursive: true, force: true });
}
assert.equal(exitCode, 0, "runtime pairing proof failed");
process.exit(exitCode);
