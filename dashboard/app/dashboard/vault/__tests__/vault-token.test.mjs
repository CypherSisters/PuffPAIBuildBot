/**
 * Every vault mutation carries the unlock session token.
 *
 *   node app/dashboard/vault/__tests__/vault-token.test.mjs
 *
 * Plain Node, no test runner, no network, no build — the same shape as
 * app/_lib/__tests__/no-browser-api-key.test.mjs. Exits non-zero on any failure.
 *
 * REGRESSIONS UNDER TEST (all three were live before this test existed):
 *
 *  1. `storeVaultItem` and `deleteVaultItem` went through apiPost/apiDelete,
 *     which send no X-Vault-Token header. The backend requires it on both
 *     (api_server.py: POST /vault/store, DELETE /vault/item/<key> each answer
 *     403 "Vault is locked. Unlock first." without a valid token), so storing
 *     and deleting could only ever fail while the vault was open.
 *
 *  2. The vault page refreshed its list after a successful store with a BARE
 *     `fetchContents()`. That thunk only sends the header when handed a token,
 *     so the refresh 403'd and the item the owner had just encrypted did not
 *     appear — indistinguishable from the vault having dropped it.
 *
 *  3. `vaultSlice`'s `removeItem` thunk took a bare key and forwarded no token,
 *     so dispatching it always 403'd. It now takes { key, token }.
 *
 * Checked against SOURCE rather than behaviour on purpose: these are .ts/.tsx
 * modules with React and Redux imports, and the assertion worth locking down is
 * "the token is threaded through", which is a property of the code, not of a
 * particular HTTP round trip.
 *
 * puff puff pass
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const APP = path.resolve(HERE, "../../../"); // dashboard/app
const SERVICE = path.join(APP, "_lib/services/vaultService.ts");
const SLICE = path.join(APP, "_stores/vaultSlice.ts");
const PAGE = path.join(APP, "dashboard/vault/page.tsx");

let pass = 0;
let fail = 0;
function ok(label, cond, detail = "") {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${label}${detail ? `  ${detail}` : ""}`);
}

/** Comments describe the bugs by name, so strip them before grepping code. */
const stripComments = (s) =>
  s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

const read = (p) => stripComments(fs.readFileSync(p, "utf8"));
const service = read(SERVICE);
const slice = read(SLICE);
const page = read(PAGE);

/**
 * The source of a named top-level declaration: from its name to the next line
 * that closes at column 0 (`}` or `);`). Brace counting is not used on purpose —
 * these declarations open braces in their TYPE POSITION (`Promise<{ ... }>`, a
 * `{ key, token }` destructure), so a naive depth counter closes the "body"
 * before reaching any code and the greps below all vacuously pass.
 */
function block(src, startMarker) {
  const i = src.indexOf(startMarker);
  if (i === -1) return "";
  const rest = src.slice(i);
  const end = rest.search(/\n\}|\n\);/);
  return end === -1 ? rest : rest.slice(0, end + 3);
}

/* ── 1. The service sends the header on both mutations ─────────────────── */
for (const fn of ["storeVaultItem", "deleteVaultItem"]) {
  const body = block(service, `export async function ${fn}`);
  ok(`${fn} exists`, body.length > 0);
  ok(`${fn} takes a token parameter`, /token\?:\s*string\s*\|\s*null/.test(body));
  ok(`${fn} sets X-Vault-Token`, /headers\['X-Vault-Token'\]\s*=\s*token/.test(body));
  // apiPost/apiDelete accept no headers argument, so using them here is the bug.
  ok(`${fn} does not route through the headerless helpers`, !/\bapi(Post|Delete)\s*\(/.test(body));
}

/* ── 2. The slice threads the token into the delete thunk ──────────────── */
const remove = block(slice, "export const removeItem");
ok("removeItem exists", remove.length > 0);
ok("removeItem takes { key, token }", /\{\s*key\s*,\s*token\s*\}/.test(remove));
ok("removeItem forwards the token", /deleteVaultItem\(\s*key\s*,\s*token\s*\)/.test(remove));

/* ── 3. The page never refreshes contents without a token ──────────────── */
const bareFetchContents = /fetchContents\(\s*\)/.test(page);
ok("page never calls fetchContents() with no token", !bareFetchContents);
ok(
  "page dispatches removeItem with a token",
  /removeItem\(\s*\{\s*key\s*,\s*token:\s*vaultToken\s*\}\s*\)/.test(page),
);
ok("page stores through the tokened service call", /storeVaultItem\([^)]*vaultToken/.test(page));

/* ── 4. The PIN is never echoed back to the screen ─────────────────────── */
ok("page contains no alert()", !/\balert\s*\(/.test(page));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
