/**
 * Privacy words on the dashboard stay true, and the recovery code is not lost.
 *
 *   node app/dashboard/vault/__tests__/privacy-truth.test.mjs
 *
 * Plain Node, no test runner, no network, no build: reads the page sources and
 * checks the load-bearing lines. Comments are stripped first so a note that
 * quotes an old false claim does not count as the page making it.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, "..", "..", "..");
const read = (p) => readFileSync(join(app, p), "utf8");
const code = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

let pass = 0;
let fail = 0;
function ok(name, cond) {
  if (cond) {
    pass++;
    console.log(`PASS  ${name}`);
  } else {
    fail++;
    console.log(`FAIL  ${name}`);
  }
}

const vault = code(read("dashboard/vault/page.tsx"));
const ceremony = code(read("dashboard/vault/RecoveryCodeCeremony.tsx"));
const settings = code(read("dashboard/settings/page.tsx"));
const chat = code(read("dashboard/chat/page.tsx"));
const service = code(read("_lib/services/chatService.ts"));

// Vault page
ok("vault page no longer claims AES-256-GCM", !/AES-256/.test(vault));
ok("vault page no longer says the PIN cannot be recovered", !/cannot be\s+recovered/.test(vault));
ok("vault page no longer says Zone 1", !/Zone 1/.test(vault));
ok("vault create reads the recovery code it is given", /data\.recovery_code/.test(vault));
ok("vault page shows the ceremony", /<RecoveryCodeCeremony/.test(vault));
// The page's PIN rules are the shared ones (_lib/vaultPin.ts), whose floors
// are the vault's: 8 characters, or 10 when it is only numbers.
const vaultPin = code(read("_lib/vaultPin.ts"));
ok("PIN floor matches the vault (8, or 10 numbers)",
  /from "\.\.\/\.\.\/_lib\/vaultPin"/.test(vault) && /pinRefusal\(pin\)/.test(vault)
  && /const MIN_PIN = 8;/.test(vaultPin) && /const MIN_NUMERIC_PIN = 10;/.test(vaultPin));
ok("no 8-character cap on PIN fields", !/maxLength=\{8\}/.test(vault));
ok("an open vault can make a new recovery code", /\/vault\/recovery-code/.test(vault) && /replace: true/.test(vault));
ok("ceremony checks the typed code with the shared helper", /recoveryCodeMatches\(code, typed\)/.test(ceremony));
ok("ceremony never stores the code", !/localStorage|sessionStorage|console\./.test(ceremony));

// Settings page
ok("settings no longer says encrypted at rest", !/Encrypted at rest/i.test(settings));
ok("settings no longer says stripped of identity", !/stripped of identity/i.test(settings));
ok("settings reads the generated privacy lines", /\/system\/privacy/.test(settings));
ok("settings has no Zone titles", !/title="Zone/.test(settings));

// Chat page + service
ok("chat service reads the privacy notice from the stream", /privacy: readPrivacy\(parsed\.puffpai_meta\.pii\)/.test(service));
ok("chat saves the redacted copy when there is one", /saveChat\(convId, privacy\?\.redactedMessage \?\? text/.test(chat));
ok("chat shows the notice", /msg\.privacyNotice/.test(chat));
ok("chat welcome no longer says your data never leaves", !/data never leaves/.test(chat));
ok("outside-services row is not hard-coded", !/stat="not used"/.test(chat));
ok("upload message only claims the vault when it was used", /j\?\.pii_vaulted/.test(chat));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
