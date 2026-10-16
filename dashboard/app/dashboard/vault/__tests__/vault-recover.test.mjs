/**
 * "Forgot your PIN? Use your recovery code" on the Vault page.
 *
 *   node app/dashboard/vault/__tests__/vault-recover.test.mjs
 *
 * Plain Node, no test runner, no network, no build. Reads the sources for the
 * wiring (comments stripped first), and runs the real shared PIN rule
 * (_lib/vaultPin.ts, loaded with Node's type stripping). Exits non-zero on any
 * failure.
 *
 * WHAT THIS LOCKS DOWN
 *  - the page offers the flow while the vault is locked;
 *  - the code and the new PIN go in the request BODY of POST /vault/recover,
 *    never in the address, and nothing keeps them (no storage, no console);
 *  - the new PIN is checked with the shared rule before anything is sent;
 *  - on success the NEW code goes through RecoveryCodeCeremony, and the vault
 *    is marked open without posting the PIN a second time;
 *  - a cooldown (429) is shown as a wait using the backend's retry_after.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, "..", "..", "..");
const read = (p) => readFileSync(join(app, p), "utf8");
const code = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

let pass = 0;
let fail = 0;
function ok(name, cond) {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
}

const page = code(read("dashboard/vault/page.tsx"));
const service = code(read("_lib/services/vaultService.ts"));
const slice = code(read("_stores/vaultSlice.ts"));

/** The source of one top-level function in a file, up to the next export. */
function fn(src, name) {
  const start = src.indexOf(`export async function ${name}(`);
  if (start < 0) return "";
  const next = src.indexOf("\nexport ", start + 1);
  return src.slice(start, next < 0 ? undefined : next);
}

// The offer
ok("locked view offers the recovery code", /Forgot your PIN\? Use your recovery code/.test(page));
ok("the form asks for the code and the new PIN twice",
  /id="vault-recover-code"/.test(page) && /id="vault-recover-pin"/.test(page)
  && /id="vault-recover-pin-again"/.test(page));
ok("code field is not autofilled or spell-checked",
  /id="vault-recover-code"[\s\S]{0,400}autoComplete="off"[\s\S]{0,200}spellCheck=\{false\}/.test(page));

// The call
const recover = fn(service, "recoverVault");
ok("service has recoverVault", recover.length > 0);
ok("recoverVault posts to /vault/recover", /\/vault\/recover`/.test(recover) && /method: 'POST'/.test(recover));
ok("code and new PIN go in the body", /body: JSON\.stringify\(\{ recovery_code: recoveryCode, new_pin: newPin \}\)/.test(recover));
ok("nothing secret in the address (no query string)", !/\/vault\/recover\?/.test(recover) && !/URLSearchParams/.test(recover));
ok("the call goes through the paired proxy (API_URL)", /\$\{API_URL\}\/vault\/recover/.test(recover));
ok("nothing is kept: no storage and no console in the service or page",
  !/localStorage|sessionStorage|console\./.test(recover) && !/localStorage|sessionStorage|console\./.test(page));

// Before sending
const handler = page.slice(page.indexOf("const handleRecover"), page.indexOf("const finishRecovery"));
ok("handler exists", handler.length > 0);
ok("new PIN checked with the shared rule before sending",
  /pinRefusal\(recoverPin\)/.test(handler) && handler.indexOf("pinRefusal(recoverPin)") < handler.indexOf("recoverVault("));
ok("the two new PINs must match", /recoverPin !== recoverPinAgain/.test(handler));
ok("an empty code is refused in the browser", /normalizeRecoveryCode\(recoverCode\)/.test(handler));

// After the answer
ok("success shows the NEW code with the ceremony",
  /setRecoveryCode\(data\.recovery_code\)/.test(handler) && /<RecoveryCodeCeremony/.test(page));
ok("success marks the vault open without posting the PIN again",
  /dispatch\(sessionOpened\(\)\)/.test(handler) && !/dispatch\(unlock\(/.test(handler));
ok("slice has sessionOpened, which sends nothing",
  /sessionOpened: \(state\) => \{ state\.isUnlocked = true;/.test(slice));
ok("a cooldown waits the backend's seconds", /status === 429/.test(handler) && /data\.retry_after/.test(handler));
ok("PIN fields are cleared on every answer",
  handler.indexOf('setRecoverPin("")') > -1 && handler.indexOf('setRecoverPin("")') < handler.indexOf("await recoverVault("));
ok("a refused PIN says the code still works", /your recovery code still works/.test(handler));

// The shared rule, run for real
const pinUrl = pathToFileURL(join(app, "_lib", "vaultPin.ts")).href;
const { pinRefusal } = await import(pinUrl);
ok("rule refuses a short PIN", pinRefusal("abc12") !== "");
ok("rule refuses nine digits", pinRefusal("583920174") !== "");
ok("rule refuses a vault-floor common password (1234567890)", pinRefusal("1234567890") !== "");
ok("rule refuses a vault-floor common password (password1)", pinRefusal("password1") !== "");
ok("rule refuses spaces", pinRefusal("correct horse") !== "");
ok("rule accepts a good PIN", pinRefusal("Tulip-9-Harbor") === "");

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
