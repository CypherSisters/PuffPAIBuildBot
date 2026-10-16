/**
 * "No identity" must be SAID, not implied — and a display hint must never gate.
 *
 *   npm run test:anon
 *   node app/_lib/server/__tests__/anon-identity.test.mjs
 *
 * Requires Node >=22.6 for native TypeScript type stripping. Exits non-zero on
 * any failure.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The backend keys its 300-second vault-unlocked chat session — protected
 * questions answered out of the DECRYPTED vault with no further PIN — on a
 * per-caller id. Through this proxy every request reaches the backend from
 * loopback, so when the proxy could not derive a device identity and therefore
 * sent NO identity header, the backend keyed the request on the socket peer:
 * `peer:127.0.0.1`, the SAME key for every unidentified proxied caller. One
 * caller's unlock was inheritable by the next for the rest of the window.
 *
 * BuildBot has never issued a v1 cookie, so it does not have HomeBot's
 * identity-less-but-paired population. It reaches the state two other ways:
 * `PUFF_PAIRING_ENFORCE=off` lets an unpaired browser reach this proxy at all,
 * and a device revoked between the pairing gate and the header build derives no
 * id. Either way the request now carries `X-Puff-Anonymous: 1` and the backend
 * gives it a single-use key with no vault access.
 *
 * Section 2 is the isApplianceDisplay() question: there is no such function in
 * this repo, and this asserts that no server-side module makes an access
 * decision from a client-controlled display hint either — so the day one is
 * added, the test is already waiting for it.
 *
 * SAFETY: PUFF_PAIRING_STATE / PUFF_PAIRED_DEVICES are pointed at a fresh temp
 * directory BEFORE pairing.ts is imported, so no real household state is read or
 * written and no real secret is involved.
 *
 * Lives under app/_lib/ (underscore-private), so Next never routes it.
 *
 * puff puff pass
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const DASHBOARD = path.resolve(HERE, "..", "..", "..", "..");

/* ── sandbox ───────────────────────────────────────────────────────── */

const DIR = fs.mkdtempSync(path.join(os.tmpdir(), "puff-anonid-"));
process.env.PUFF_PAIRING_STATE = path.join(DIR, "pairing.json");
process.env.PUFF_PAIRED_DEVICES = path.join(DIR, "devices.json");

const { upstreamHeaders, FORWARDED_CLIENT_HEADERS, DEVICE_ID_HEADER, ANONYMOUS_HEADER } =
  await import("../proxyHeaders.ts");
const P = await import("../pairing.ts");

/* ── harness ───────────────────────────────────────────────────────── */

let pass = 0;
let fail = 0;
function check(label, got, want) {
  const ok_ = Object.is(got, want);
  if (ok_) pass++;
  else fail++;
  console.log(`${ok_ ? "PASS" : "FAIL"}  ${label}  -> ${got} (want ${want})`);
}
function ok(label, cond) {
  check(label, !!cond, true);
}
function section(name) {
  console.log(`\n--- ${name} ---`);
}

/** Exactly what app/api/pai/[...path]/route.ts does. */
const buildHeaders = (req, key = "K") =>
  upstreamHeaders(req, key, P.deviceSessionIdFor(req));

function reqWith(headerObj, cookieValue) {
  return {
    headers: new Headers(headerObj),
    cookies: {
      get: (name) =>
        name === P.PAIRING_COOKIE && cookieValue ? { value: cookieValue } : undefined,
    },
  };
}
function hdr(out, name) {
  const want = name.toLowerCase();
  for (const k of Object.keys(out)) if (k.toLowerCase() === want) return out[k];
  return undefined;
}

const device = P.addPairedDevice("Workshop laptop");
const goodCookie = P.issueDeviceCookieValueV2(device.id);
const expectedSession = P.deviceSessionId(device.id);

/* ── 1. the anonymous marker ────────────────────────────────────────── */

section("the anonymous marker");
{
  const out = buildHeaders(reqWith({}));
  check("no cookie -> the marker is SET", hdr(out, ANONYMOUS_HEADER), "1");
  check("...and no identity rides with it", hdr(out, DEVICE_ID_HEADER), undefined);
}
{
  const out = buildHeaders(reqWith({}, goodCookie));
  check("a paired v2 device -> no marker", hdr(out, ANONYMOUS_HEADER), undefined);
  check("...just its own identity", hdr(out, DEVICE_ID_HEADER), expectedSession);
}
{
  // The marker must be OURS: a client that could set it would push a paired
  // browser onto the identity-less path, and a client that could clear it would
  // make an unidentified request look like a direct loopback caller again.
  const out = buildHeaders(
    reqWith({ [ANONYMOUS_HEADER]: "0", "x-puff-anonymous": "0" }),
  );
  check("a client-supplied marker is REPLACED with ours", hdr(out, ANONYMOUS_HEADER), "1");
}
{
  const out = buildHeaders(reqWith({ [ANONYMOUS_HEADER]: "1" }, goodCookie));
  check(
    "a client-supplied marker on a PAIRED request is dropped",
    hdr(out, ANONYMOUS_HEADER),
    undefined,
  );
  check("...and the derived identity still wins", hdr(out, DEVICE_ID_HEADER), expectedSession);
}
{
  // Lowercase client copies must not survive either: the relay loop writes a
  // forwarded header under the client's own spelling, so the purge in
  // upstreamHeaders is case-insensitive on purpose.
  const out = buildHeaders(reqWith({ "x-puff-device-id": "forged-value-0001" }));
  check("a lowercase client identity copy is dropped", hdr(out, DEVICE_ID_HEADER), undefined);
  check("...and the request is marked anonymous", hdr(out, ANONYMOUS_HEADER), "1");
}
ok(
  "the marker is not in the relay allowlist",
  !FORWARDED_CLIENT_HEADERS.some((h) => h.toLowerCase() === ANONYMOUS_HEADER.toLowerCase()),
);
ok(
  "the identity header is not in the relay allowlist either",
  !FORWARDED_CLIENT_HEADERS.some((h) => h.toLowerCase() === DEVICE_ID_HEADER.toLowerCase()),
);
{
  const bad = goodCookie.slice(0, -1) + (goodCookie.endsWith("a") ? "b" : "a");
  check("a bad-MAC cookie -> marked anonymous", hdr(buildHeaders(reqWith({}, bad)), ANONYMOUS_HEADER), "1");
}
{
  // The revocation race: paired at the gate, revoked by the time the header is
  // built. That request must not inherit anything either.
  const doomed = P.addPairedDevice("Revoked mid-request");
  const cookie = P.issueDeviceCookieValueV2(doomed.id);
  check("a still-paired device is identified", hdr(buildHeaders(reqWith({}, cookie)), DEVICE_ID_HEADER), P.deviceSessionId(doomed.id));
  P.revokePairedDevice(doomed.id);
  const out = buildHeaders(reqWith({}, cookie));
  check("a revoked device -> no identity", hdr(out, DEVICE_ID_HEADER), undefined);
  check("...and is marked anonymous", hdr(out, ANONYMOUS_HEADER), "1");
}
{
  // Two unidentified callers must not be told apart by the proxy — the proxy
  // says the same thing about both, and the BACKEND is what makes their session
  // keys differ (tests/test_release_chat_anonymous_session.py). Asserted here so
  // nobody "helpfully" adds a stable placeholder id to distinguish them, which
  // is exactly the shared bucket this removed.
  const a = buildHeaders(reqWith({ "User-Agent": "phone" }));
  const b = buildHeaders(reqWith({ "User-Agent": "laptop" }));
  check("the marker carries no per-caller value (a)", hdr(a, ANONYMOUS_HEADER), "1");
  check("the marker carries no per-caller value (b)", hdr(b, ANONYMOUS_HEADER), "1");
  ok(
    "neither anonymous request carries an identity of any kind",
    hdr(a, DEVICE_ID_HEADER) === undefined && hdr(b, DEVICE_ID_HEADER) === undefined,
  );
}

/* ── 2. display mode is presentation, never authentication ──────────── */

section("display mode is never an access decision");
{
  // There is no isApplianceDisplay() in this repo. What exists is the
  // client-side kiosk hint: ?puffMode=kiosk, persisted in localStorage and read
  // only by the pre-hydration script in app/layout.tsx to toggle CSS classes.
  // Every input to it (query string, localStorage, a Host/X-Puff-Display header
  // if one were added) is client-controlled, so nothing that grants access may
  // consult it. Checked across middleware, the API routes, and _lib/server.
  const roots = [
    path.join(DASHBOARD, "middleware.ts"),
    path.join(DASHBOARD, "app", "api"),
    path.join(DASHBOARD, "app", "_lib", "server"),
  ];
  const files = [];
  const walk = (p) => {
    let st;
    try {
      st = fs.statSync(p);
    } catch {
      return;
    }
    if (st.isDirectory()) {
      for (const e of fs.readdirSync(p)) {
        if (e === "__tests__" || e === "node_modules") continue;
        walk(path.join(p, e));
      }
      return;
    }
    if (/\.(ts|tsx|mjs|js)$/.test(p)) files.push(p);
  };
  roots.forEach(walk);
  ok("found server-side modules to check", files.length > 5);
  const offenders = files.filter((f) => {
    // Comments may discuss it; code may not.
    const code = fs
      .readFileSync(f, "utf8")
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/(^|[^:])\/\/[^\n]*/g, "$1");
    return /isApplianceDisplay|x-puff-display|puffMode/i.test(code);
  });
  ok(
    `no server-side module reads a display hint (${offenders.length ? offenders.map((f) => path.relative(DASHBOARD, f)).join(", ") : "none"})`,
    offenders.length === 0,
  );
}

/* ── done ──────────────────────────────────────────────────────────── */

try {
  fs.rmSync(DIR, { recursive: true, force: true });
} catch {
  /* best effort */
}

console.log(`\n${pass} passed, ${fail} failed`);
assert.equal(fail, 0, `${fail} anonymous-identity assertions failed`);
