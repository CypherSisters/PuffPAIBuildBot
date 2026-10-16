/**
 * The browser never holds the backend API key.
 *
 *   npm run test:nokey
 *
 * Plain Node, no test runner, no network, no build. Exits non-zero on any
 * failure.
 *
 * REGRESSION UNDER TEST: the API client that shipped with this dashboard
 * template carried getApiKey()/setApiKey(), which wrote the appliance's backend
 * credential into localStorage, plus a Profile > API Key card that invited the
 * owner to paste it in. Several pages then read it back and sent it as
 * `Authorization: Bearer ...` from the browser. That put a key good for every
 * backend endpoint into storage any script on the page can read, on every device
 * in the house that ever opened the dashboard. The correct arrangement — already
 * in place — is the same-origin /api/pai proxy, which attaches the key
 * server-side behind the pairing gate.
 *
 * This is the load-bearing assertion, checked against SOURCE, because the
 * equivalent grep of the built bundle (scripts/check-no-secret-in-bundle.js) can
 * be defeated by a minifier renaming the symbols.
 *
 * puff puff pass
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "../..");
const API_TS = path.join(APP, "_lib/api.ts");

let pass = 0;
let fail = 0;
function ok(label, cond, detail = "") {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${label}${detail ? `  ${detail}` : ""}`);
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name === ".next") continue;
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else if (/\.(ts|tsx|js|jsx|mjs)$/.test(e.name)) out.push(full);
  }
  return out;
}

const FILES = walk(APP).filter((f) => f !== path.join(HERE, "no-browser-api-key.test.mjs"));
ok("found application sources to scan", FILES.length > 50, `${FILES.length} file(s)`);

/* ── 1. The removed helpers stay removed, anywhere under app/ ───────────────
   Spelled out nowhere, not even in a comment, so this grep is an unambiguous
   tripwire rather than something a reader has to triage. */
for (const sym of ["getApiKey", "setApiKey"]) {
  const hits = FILES.filter((f) => fs.readFileSync(f, "utf8").includes(sym)).map((f) =>
    path.relative(APP, f),
  );
  ok(`no "${sym}" anywhere under app/`, hits.length === 0, hits.join(", "));
}

const api = fs.readFileSync(API_TS, "utf8");
/** Comments explain WHY there is no Authorization header, so strip them first. */
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
const apiCode = stripComments(api);
ok("api.ts exports no key getter/setter", !/export\s+(async\s+)?function\s+(get|set)ApiKey/.test(api));
ok("api.ts code sets no Authorization header", !/Authorization/i.test(apiCode));

/* Every fetch in the client must be same-origin-relative. An absolute http(s)
   target in application code is the shape of a call that skips the proxy.
   Test harnesses are excluded: they legitimately drive a loopback server. */
const absolute = [];
for (const f of FILES.filter((f) => !/[\\/]__tests__[\\/]/.test(f))) {
  const code = stripComments(fs.readFileSync(f, "utf8"));
  for (const m of code.matchAll(/fetch\(\s*[`'"]https?:\/\//g)) {
    absolute.push(`${path.relative(APP, f)}: ${m[0]}`);
  }
}
ok("no client fetch to an absolute http(s) origin", absolute.length === 0, absolute.join(", "));

/* ── 2. No env-supplied API base ────────────────────────────────────────────
   An absolute base from NEXT_PUBLIC_API_URL would let the browser talk past the
   proxy, and therefore past the pairing gate, the path allowlist and the
   cross-site check — while still sending X-Vault-Token to whatever host was
   configured. The base must be the same-origin relative path. */
const envBase = FILES.filter((f) =>
  fs.readFileSync(f, "utf8").includes("NEXT_PUBLIC_API_URL"),
).map((f) => path.relative(APP, f));
ok("no NEXT_PUBLIC_API_URL read under app/", envBase.length === 0, envBase.join(", "));
ok("api.ts pins the base to the same-origin proxy", /const API_URL = ['"]\/api\/pai['"]/.test(api));

/* ── 3. Nothing writes a backend key into browser storage ───────────────────
   The one permitted mention of the old storage entry is the purge on module
   load, which cleans up the copy already sitting in browsers that used the old
   card. A setItem for it would be the bug coming back. */
const writers = [];
for (const f of FILES) {
  const src = fs.readFileSync(f, "utf8");
  for (const m of src.matchAll(
    /(?:local|session)Storage\.setItem\(\s*(['"`])([^'"`]*)\1/g,
  )) {
    if (/api[_-]?key|token|secret|password|pin/i.test(m[2])) {
      writers.push(`${path.relative(APP, f)}: ${m[2]}`);
    }
  }
}
ok("no credential-shaped key written to browser storage", writers.length === 0, writers.join(", "));

const purgers = FILES.filter((f) =>
  /(?:local|session)Storage\.removeItem\(\s*['"`]puffpai_api_key['"`]/.test(
    fs.readFileSync(f, "utf8"),
  ),
).map((f) => path.relative(APP, f));
ok(
  "the stale localStorage copy is still purged on load",
  purgers.includes(path.join("_lib", "api.ts")),
  purgers.join(", "),
);

console.log(`\n${pass} passed, ${fail} failed`);
assert.equal(fail, 0, `${fail} browser-key assertion(s) failed`);
