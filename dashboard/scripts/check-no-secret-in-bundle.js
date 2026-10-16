#!/usr/bin/env node
/**
 * Prove that no server-side secret reaches the browser.
 *
 *   npm run test:bundle
 *
 * HOW IT WORKS, AND WHY IT IS NOT A NAME GREP
 * -------------------------------------------
 * Grepping the build output for the NAME of an env var proves nothing: Next
 * replaces `process.env.NEXT_PUBLIC_X` with its VALUE at build time, so the name
 * disappears and the secret is what is left behind. So this builds with known
 * SENTINEL values and greps for those, which is the only thing that actually
 * answers the question.
 *
 * It also greps for a POSITIVE CONTROL -- the value of NEXT_PUBLIC_API_URL,
 * which is supposed to be inlined. If the control is not found, the search
 * itself is broken and the "0 hits" result for the secrets means nothing. A
 * proof that cannot fail is not a proof.
 *
 * WHAT IS CHECKED
 * ---------------
 *   .next/static   served to every browser. Any hit here is a live exposure.
 *   .next/server   not served, but a hit means the value was baked into a
 *                  build artifact rather than read from the environment.
 *   .next/cache    NOT checked and deliberately so -- turbopack retains
 *                  built-with env values there. It is neither served nor
 *                  deployed, so a hit there is not an exposure.
 *
 * This rebuilds, which is why it is its own script rather than part of the unit
 * tests. It REMOVES .next when it is done, always, including on failure: leaving
 * a build made with fake credentials in the tree is how somebody ends up running
 * `npm start` on it and spending an hour on a dashboard that cannot talk to its
 * own backend. A missing .next is loud (`npm start` refuses); a poisoned one is
 * silent, and silent is worse.
 *
 * puff puff pass
 */
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const DASHBOARD = path.resolve(__dirname, "..");
const NEXT = path.join(DASHBOARD, ".next");

/** Values that must NOT appear. Distinctive enough to be unambiguous. */
const SECRETS = {
  PUFFPAI_API_KEY: "SENTINELSERVERKEY4d2b8e1aNOTREAL",
  HA_ACCESS_TOKEN: "SENTINELHATOKEN7c1f9a3eNOTREAL",
  // The name that leaked the key once. If anyone reintroduces a read of it,
  // this value lands in the client bundle and this script fails.
  NEXT_PUBLIC_API_KEY: "SENTINELPUBLICKEY9f3a1c7eNOTREAL",
};
/**
 * Browser-side key handling that must NOT come back. The dashboard's API client
 * has no getter/setter for the backend key any more (app/_lib/api.ts): the
 * same-origin /api/pai proxy attaches it server-side. If someone reintroduces
 * those helpers, their names land in the client chunks and this fails.
 *
 * CAVEAT, so nobody over-reads a pass: a minifier is free to rename these, so
 * absence here is corroboration, not proof. The load-bearing check is the
 * source-level one in app/_lib/__tests__/no-browser-api-key.test.mjs
 * (`npm run test:nokey`), which cannot be fooled by mangling.
 */
const FORBIDDEN_SYMBOLS = ["getApiKey", "setApiKey"];

/**
 * Must appear where stated, or the search is not working.
 *   API_BASE       — a client-source literal, proves the .next/static grep
 *                    reaches the JavaScript actually served to browsers.
 *   NEXT_PUBLIC_*  — read by app/api/health/route.ts, so its VALUE must be
 *                    inlined into .next/server. This is what keeps the
 *                    sentinel checks above meaningful: it proves Next still
 *                    inlines NEXT_PUBLIC_* values, so "0 hits" for a secret
 *                    means the secret is absent, not that inlining silently
 *                    stopped happening.
 */
const CONTROL = {
  API_BASE: "/api/pai",
  NEXT_PUBLIC_VERSION: "SENTINELVERSION5e8b2d4aNOTREAL",
};

function walk(dir, out = []) {
  if (!fs.existsSync(dir)) return out;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) walk(full, out);
    else out.push(full);
  }
  return out;
}

function hits(dir, needle) {
  const found = [];
  for (const f of walk(dir)) {
    let buf;
    try {
      buf = fs.readFileSync(f);
    } catch {
      continue;
    }
    if (buf.includes(needle)) found.push(path.relative(DASHBOARD, f));
  }
  return found;
}

console.log("Building with sentinel values (this takes a while)...");
// .next is removed first: a stale artifact from a build that did NOT carry the
// sentinels would be searched and found clean, which is a false pass.
fs.rmSync(NEXT, { recursive: true, force: true });
execFileSync(process.platform === "win32" ? "npx.cmd" : "npx", ["next", "build"], {
  cwd: DASHBOARD,
  stdio: "inherit",
  shell: process.platform === "win32",
  env: {
    ...process.env,
    ...SECRETS,
    NEXT_PUBLIC_VERSION: CONTROL.NEXT_PUBLIC_VERSION,
  },
});

let fail = 0;
function report(label, found, want) {
  const ok = want === "none" ? found.length === 0 : found.length > 0;
  if (!ok) fail++;
  console.log(
    `${ok ? "PASS" : "FAIL"}  ${label}: ${found.length} file(s)` +
      (found.length ? `  e.g. ${found[0]}` : ""),
  );
}

console.log("");
for (const [name, value] of Object.entries(SECRETS)) {
  report(`${name} value in .next/static (served to browsers)`,
    hits(path.join(NEXT, "static"), value), "none");
  report(`${name} value in .next/server`,
    hits(path.join(NEXT, "server"), value), "none");
}
for (const sym of FORBIDDEN_SYMBOLS) {
  report(`browser-side key helper "${sym}" in .next/static`,
    hits(path.join(NEXT, "static"), sym), "none");
}
report("POSITIVE CONTROL: client API base literal in .next/static",
  hits(path.join(NEXT, "static"), CONTROL.API_BASE), "some");
report("POSITIVE CONTROL: NEXT_PUBLIC_* value inlined into .next/server",
  hits(path.join(NEXT, "server"), CONTROL.NEXT_PUBLIC_VERSION), "some");
report('the literal name "NEXT_PUBLIC_API_KEY" in .next/static',
  hits(path.join(NEXT, "static"), "NEXT_PUBLIC_API_KEY"), "none");

// Always, including on failure. The sentinel build has served its purpose
// the moment the greps above have run, and every second it stays on disk is
// a second somebody can start it by accident.
fs.rmSync(NEXT, { recursive: true, force: true });
console.log(
  "\nRemoved the sentinel .next. Run `npm run build` before " +
    "starting the dashboard for real.",
);
process.exit(fail === 0 ? 0 : 1);
