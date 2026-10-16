/**
 * The status→message mapping for the first-run setup check, and the two-shell
 * command hint.
 *
 *   npm run test:setup-gate
 *
 * Plain Node, no test runner, no network, no server. Requires Node >=22.6 for
 * native TypeScript type stripping (same as the pairing tests). Exits non-zero
 * on any failure.
 *
 * REGRESSION UNDER TEST: a 401 from /setup/status (dashboard PUFFPAI_API_KEY not
 * matching the backend's) used to be treated as a usable status, so the wizard
 * threw on `data.ollama.selected` and sat on "Checking your setup…" forever.
 * Nothing may map to "no problem" unless it is a real status.
 */
import assert from "node:assert/strict";

const G = await import("../setupGate.ts");
const S = await import("../shellHint.ts");

let pass = 0;
let fail = 0;
function check(label, got, want) {
  const ok = got === want;
  if (ok) pass++;
  else fail++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}  -> ${JSON.stringify(got)} (want ${JSON.stringify(want)})`);
}
function ok(label, cond) {
  check(label, !!cond, true);
}

const GOOD = {
  ollama: { reachable: true, models: [], selected: "qwen3:8b", url: "" },
  vault: { initialized: false },
  ha: { configured: false, url: "" },
  knowledge: { zim_dir: "", auto_index: true },
  complete: false,
};
const reply = (status, data, okFlag) => ({ ok: okFlag ?? (status >= 200 && status < 300), status, data });

/* ── a usable status is not a problem ──────────────────────────────── */

check("200 + full status -> no problem", G.classifySetupReply(reply(200, GOOD)), null);

/* ── backend down ──────────────────────────────────────────────────── */

for (const s of [0, 502, 504]) {
  check(`${s} -> backend-down`, G.classifySetupReply(reply(s, {})).kind, "backend-down");
}
ok(
  "backend-down tells you to start the backend",
  G.classifySetupReply(reply(502, {})).commands.includes("puffpai serve"),
);

/* ── key mismatch: the bug that hung the wizard ────────────────────── */

for (const s of [401, 403]) {
  const p = G.classifySetupReply(reply(s, { detail: "Invalid API key" }));
  check(`${s} -> auth`, p.kind, "auth");
  ok(`${s} names the key mismatch`, /key doesn’t match the backend/.test(p.title));
  ok(`${s} points at dashboard/.env.local`, p.body.includes("dashboard/.env.local"));
  ok(`${s} points at PUFFPAI_API_KEY`, p.body.includes("PUFFPAI_API_KEY"));
  ok(`${s} says how to print the key`, p.commands.includes("puffpai apikey"));
  ok(`${s} says to restart the dashboard`, p.commands.includes("npm start"));
}

/* ── any other non-OK status, and unusable bodies ──────────────────── */

const p500 = G.classifySetupReply(reply(500, { detail: "boom" }));
check("500 -> bad-status", p500.kind, "bad-status");
ok("500 shows the status", p500.title.includes("500"));

for (const [label, body] of [
  ["empty object", {}],
  ["error envelope", { detail: "nope" }],
  ["null", null],
  ["a string (HTML/parse fallback)", "<html>"],
  ["a status missing ollama", { ...GOOD, ollama: undefined }],
  ["a status missing complete", { ...GOOD, complete: undefined }],
]) {
  const p = G.classifySetupReply(reply(200, body));
  ok(`200 + ${label} -> a problem, not a status`, p !== null && p.kind === "bad-status");
}

// The invariant: every reply either yields a status the wizard can read, or a
// problem to render. There is no third outcome, so no spinner can survive.
for (const s of [0, 200, 302, 400, 401, 403, 404, 418, 500, 502, 504]) {
  const r = reply(s, s === 200 ? GOOD : { detail: "x" });
  const p = G.classifySetupReply(r);
  ok(`status ${s} is classified`, p === null ? G.isUsableStatus(r.data) : typeof p.title === "string");
}

/* ── shell hints: no && in a PowerShell one-liner ──────────────────── */

// A two-step line of the kind this helper exists for. (The pairing screen no
// longer shows one: it says "puffpai pair", see app/pair/pairWords.ts.)
const STEPS = ["cd dashboard", "npm run pair:code"];
const forms = S.commandForms(STEPS);
check("posix form", forms.posix, "cd dashboard && npm run pair:code");
check("powershell form", forms.powershell, "cd dashboard; npm run pair:code");
ok("powershell form has no &&", !forms.powershell.includes("&&"));

check("windows UA detected", S.isWindowsClient({ platform: "Win32" }), true);
check("windows in userAgent detected", S.isWindowsClient({ userAgent: "Mozilla/5.0 (Windows NT 10.0)" }), true);
check("mac not windows", S.isWindowsClient({ platform: "MacIntel", userAgent: "Macintosh" }), false);
check("unknown not windows", S.isWindowsClient(undefined), false);

const onWin = S.shellHint(STEPS, true);
check("windows leads with powershell", onWin.primary, forms.powershell);
check("windows still shows posix", onWin.secondary, forms.posix);
const onPosix = S.shellHint(STEPS, false);
check("posix leads with &&", onPosix.primary, forms.posix);
check("posix still shows powershell", onPosix.secondary, forms.powershell);
ok("both forms are always labelled", !!onWin.primaryLabel && !!onWin.secondaryLabel);

/* ── the box's shell, not the browser's ────────────────────────────── */

// REGRESSION: the dialect came off `navigator`, so a Mac or a phone paired to a
// Windows box was shown `&&` — a line that fails on the machine it runs on.
check("census says Windows", S.boxPlatform({ os: { system: "Windows" } }), "windows");
check("census says Linux", S.boxPlatform({ os: { system: "Linux" } }), "posix");
check("census says Darwin", S.boxPlatform({ os: { system: "Darwin" } }), "posix");
check("a flat platform field is read too", S.boxPlatform({ platform: "win32" }), "windows");
for (const [label, value] of [
  ["null census", null],
  ["census with no os block", { memory: { total_gb: 8 } }],
  ["os block with no system", { os: { release: "10" } }],
  ["an unrecognised system", { os: { system: "Plan9" } }],
]) {
  check(`${label} -> unknown`, S.boxPlatform(value), "unknown");
}

check("a Windows box beats a Mac browser", S.preferPowerShell("windows", false), true);
check("a Linux box beats a Windows browser", S.preferPowerShell("posix", true), false);
check("unknown box falls back to the browser", S.preferPowerShell("unknown", true), true);
check("unknown box, posix browser", S.preferPowerShell("unknown", false), false);

/* ── /setup/vault 409: a vault already exists ──────────────────────── */

// REGRESSION: the wizard treated 409 `already_initialized` as success and
// advanced, so the owner believed the PIN they had just typed was the vault's.
const V = await import("../../onboarding/vaultConflict.ts");

ok(
  "409 + flag -> already initialized",
  V.isVaultAlreadyInitialized({ ok: false, status: 409, data: { already_initialized: true } }),
);
ok("a bare 409 still counts", V.isVaultAlreadyInitialized({ ok: false, status: 409, data: {} }));
ok(
  "the flag alone still counts",
  V.isVaultAlreadyInitialized({ ok: false, status: 400, data: { already_initialized: true } }),
);
ok(
  "a created vault is not a conflict",
  !V.isVaultAlreadyInitialized({ ok: true, status: 200, data: { recovery_code: "AAAA-BBBB" } }),
);
for (const s of [0, 400, 401, 500, 502]) {
  ok(
    `plain ${s} is not a conflict`,
    !V.isVaultAlreadyInitialized({ ok: false, status: s, data: { message: "x" } }),
  );
}
ok("the notice says the PIN was not used", /was not used/.test(V.VAULT_EXISTS_TITLE));
ok("it offers the existing PIN", /Unlock the existing/.test(V.VAULT_EXISTS_BODY));
ok("it warns a reset destroys the contents", /destroys everything/i.test(V.VAULT_EXISTS_BODY));
ok("the reset path is a real command", V.VAULT_RESET_COMMANDS.includes("puffpai vault status"));
ok("and ends by re-running setup", V.VAULT_RESET_COMMANDS.includes("puffpai setup"));
ok(
  "no reset verb is invented",
  !V.VAULT_RESET_COMMANDS.some((c) => /vault\s+reset|vault\s+destroy/.test(c)),
);
ok("there is something to acknowledge", V.VAULT_EXISTS_ACK.length > 0);

console.log(`\n${pass} passed, ${fail} failed`);
assert.equal(fail, 0, `${fail} setup-gate assertion(s) failed`);
