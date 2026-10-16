// GitHub connect, proven without a browser: the shared GitHubConnectView in
// every state (not connected, device code, token form, connected, extra
// scopes, vault locked, error), when the wizard asks the GitHub question, the
// "From your GitHub" badge on parts, and that no GitHub file ever touches
// browser storage.
//
// Run from the dashboard directory (part of `npm run test:jobs`):
//   npm run test:github
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import * as fs from "fs";
import * as path from "path";
import { renderToStaticMarkup } from "react-dom/server";
import {
  GitHubConnectView,
  LOCKED_LINE,
  NO_VAULT_LINE,
  TOKEN_STEPS,
  UNLOCK_TITLE,
  VAULT_PIN_TITLE,
  githubLink,
} from "../app/_components/GitHub/GitHubConnect";
import { fromYourGitHub, readStatus, type GitHubStatus } from "../app/_lib/services/githubService";
import { githubDue } from "../app/onboarding/githubDue";
import type { FlowReply } from "../app/_lib/services/wizardService";
import { readBuildStatus, readJobReply } from "../app/_lib/buildJobs";
import { IngredientRows } from "../app/_components/Jobs/BuildViews";

let failed = 0;
function check(name: string, ok: boolean): void {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed += 1;
}
function plain(html: string): boolean {
  const words = html.replace(/<[^>]+>/g, " ");
  return !words.includes("—") && !/\bdoors?\b/i.test(words) && !/\bbeta\b/i.test(words);
}
const noop = () => undefined;

const off: GitHubStatus = readStatus({ connected: false, device_available: false, vault_locked: false });
const on: GitHubStatus = readStatus({
  connected: true,
  login: "octo",
  name: "Octo Cat",
  avatar_url: "https://avatars.githubusercontent.com/u/1",
  scopes: ["public_repo"],
  method: "token",
  device_available: false,
  can_read_private: false,
  vault_locked: false,
  vault_set_up: true,
  rate: { limit: 30, remaining: 29 },
  rate_window: "minute",
});

// ── the states ──────────────────────────────────────────────────────────────

const offHtml = renderToStaticMarkup(<GitHubConnectView phase="off" status={off} onConnect={noop} />);
check("not connected: says so", offHtml.includes('data-gh-state="off"') && offHtml.includes("Not connected"));
check("not connected: Connect button", />Connect</.test(offHtml));
check("not connected: no locked line when unlocked", !offHtml.includes(LOCKED_LINE));

const device = renderToStaticMarkup(
  <GitHubConnectView
    phase="device"
    status={readStatus({ device_available: true })}
    device={{ user_code: "WDJB-MJHT", verification_uri: "https://github.com/login/device", interval: 5, expires_in: 900 }}
    onCancel={noop}
  />,
);
check("device code: the code is shown", device.includes('data-gh-state="device"') && device.includes("WDJB-MJHT"));
check("device code: Open github.com links to GitHub", device.includes('href="https://github.com/login/device"') && device.includes("Open github.com"));
check("device code: waiting line", device.includes("data-gh-waiting") && device.includes("Waiting for GitHub"));
const badDevice = renderToStaticMarkup(
  <GitHubConnectView
    phase="device"
    status={off}
    device={{ user_code: "X", verification_uri: "https://evil.example/login", interval: 5, expires_in: 900 }}
  />,
);
check("device code: a non-GitHub address is never a link", !badDevice.includes("evil.example/login\"") && !badDevice.includes("href=\"https://evil"));
check("githubLink keeps github.com only", githubLink("https://github.com/x") !== "" && githubLink("http://github.com/x") === "" && githubLink("https://github.com.evil.io/") === "");

const token = renderToStaticMarkup(
  <GitHubConnectView
    phase="token"
    status={off}
    help={{ url: "https://github.com/settings/personal-access-tokens/new", steps: ["One.", "Two.", "Three.", "Four."] }}
    token=""
    onToken={noop}
    onSendToken={noop}
  />,
);
check("token form: state", token.includes('data-gh-state="token"'));
check("token form: the box is a password field", /<input[^>]*type="password"/.test(token));
check("token form: no autofill", /<input[^>]*autoComplete="off"|<input[^>]*autocomplete="off"/i.test(token));
check("token form: Make a token on GitHub link", token.includes("Make a token on GitHub") && token.includes('href="https://github.com/settings/personal-access-tokens/new"'));
check("token form: every step token-help gives", (token.match(/<li>/g) || []).length === 4 && token.includes("Four."));
check("token form: Connect is off while empty", /<button type="submit"[^>]*disabled/.test(token));
const tokenFallback = renderToStaticMarkup(<GitHubConnectView phase="token" status={off} help={null} token="" />);
check("token form: fallback steps when token-help does not answer", TOKEN_STEPS.every((s) => tokenFallback.includes(s)));
check("token form: four steps, ending with paste it here", TOKEN_STEPS.length === 4 && TOKEN_STEPS[3] === "Press Generate token, copy it, and paste it here.");

const conn = renderToStaticMarkup(<GitHubConnectView phase="connected" status={on} canDisconnect onDisconnect={noop} />);
check("connected: Connected as <login>", conn.includes('data-gh-state="connected"') && conn.includes("Connected as octo"));
check("connected: avatar", conn.includes("data-gh-avatar"));
check("connected: never loads a picture from outside", !conn.includes("<img"));
check("connected: the SEARCH limit, per minute", conn.includes("GitHub searches left this minute: 29 of 30"));
check("connected: Disconnect", />Disconnect</.test(conn));
check("connected: no extra-scopes warning when none", !conn.includes("data-gh-extra"));
const confirm = renderToStaticMarkup(<GitHubConnectView phase="connected" status={on} canDisconnect confirmingOff />);
check("disconnect asks first", confirm.includes("Yes, disconnect") && confirm.includes("Keep it"));
const wizardConn = renderToStaticMarkup(<GitHubConnectView phase="connected" status={on} />);
check("wizard: no Disconnect", !wizardConn.includes("Disconnect"));

const extra = renderToStaticMarkup(
  <GitHubConnectView phase="connected" status={{ ...on, extra_scopes: ["repo", "delete_repo"] }} />,
);
check("extra scopes: plain warning naming them", extra.includes("data-gh-extra") && extra.includes("repo, delete_repo") && extra.includes("read-only token is safer"));

const locked = renderToStaticMarkup(<GitHubConnectView phase="connected" status={{ ...on, vault_locked: true }} />);
check("vault locked: says searches are public", locked.includes("data-gh-locked") && locked.includes(LOCKED_LINE));
const lockedOff = renderToStaticMarkup(<GitHubConnectView phase="off" status={{ ...off, vault_locked: true }} onConnect={noop} />);
check("vault locked, not connected: same line", lockedOff.includes(LOCKED_LINE));

// ── no vault yet, a locked vault, after a disconnect ───────────────────────

const none = readStatus({ connected: false, vault_set_up: false, vault_locked: false });
const noneOff = renderToStaticMarkup(<GitHubConnectView phase="off" status={none} onConnect={noop} />);
check("no vault: says No vault yet, never the locked line", noneOff.includes("No vault yet.") && !noneOff.includes(LOCKED_LINE) && !noneOff.includes("Unlock your vault"));
const noneToken = renderToStaticMarkup(<GitHubConnectView phase="token" status={none} help={null} token="" />);
check("no vault: Connect offers to set a PIN right there", noneToken.includes('data-gh-state="vault"') && noneToken.includes(VAULT_PIN_TITLE) && noneToken.includes(NO_VAULT_LINE));
check("no vault: the PIN fields are hidden and not the token box", (noneToken.match(/type="password"/g) || []).length === 2 && !noneToken.includes("GitHub token"));
const lockedToken = renderToStaticMarkup(<GitHubConnectView phase="token" status={readStatus({ vault_locked: true })} help={null} token="" />);
check("locked: Connect offers to open the vault right there", lockedToken.includes('data-gh-state="unlock"') && lockedToken.includes(UNLOCK_TITLE));
const lockedPrivate = renderToStaticMarkup(<GitHubConnectView phase="connected" status={{ ...on, can_read_private: true, vault_locked: true }} />);
check("locked: never says private ones too", !lockedPrivate.includes("private ones too"));
const openPrivate = renderToStaticMarkup(<GitHubConnectView phase="connected" status={{ ...on, can_read_private: true }} />);
check("open: private ones too", openPrivate.includes("private ones too"));
const revoked = renderToStaticMarkup(
  <GitHubConnectView phase="off" status={off} revoke={{ line: "Disconnected.", url: "https://github.com/settings/personal-access-tokens" }} />,
);
check("after disconnect: where to revoke it", revoked.includes("data-gh-revoke") && revoked.includes('href="https://github.com/settings/personal-access-tokens"'));
const badRevoke = renderToStaticMarkup(<GitHubConnectView phase="off" status={off} revoke={{ line: "Disconnected.", url: "https://evil.example/" }} />);
check("after disconnect: a non-GitHub address is never a link", !badRevoke.includes("evil.example"));

const err = renderToStaticMarkup(
  <GitHubConnectView phase="token" status={off} token="" error="GitHub did not accept that token." />,
);
check("error: shown as an alert", err.includes('role="alert"') && err.includes("GitHub did not accept that token."));

check(
  "every state: plain words (no em dash, door or beta)",
  [offHtml, device, token, tokenFallback, conn, confirm, extra, locked, lockedOff, err, noneOff, noneToken, lockedToken, lockedPrivate, revoked].every(plain),
);

check("readStatus: rate missing is null, unknown method is empty", readStatus({ method: "oauth" }).method === "" && readStatus({}).rate === null);

// ── when the wizard asks ────────────────────────────────────────────────────

const q = (goal: boolean, identity = false) => [
  { id: "purpose:goal", applied: goal, card: {} as never },
  { id: "purpose:identity", applied: identity, card: {} as never },
];
const flow = (f: Partial<FlowReply>): FlowReply => ({ github: { asked: false }, ...f });
check("order: not on the first screen", !githubDue(flow({ next: "first_look", questions: q(false) })));
check("order: not before the goal", !githubDue(flow({ next: "goal", questions: q(false) })));
check("order: right after the goal", githubDue(flow({ next: "identity", questions: q(true) })));
check("order: still before the plan", githubDue(flow({ next: "summary", questions: q(true, true) })) && githubDue(flow({ next: "needs", questions: q(true, true) })));
check("order: never after the plan", ["hub", "vault", "build", "building", "done"].every((n) => !githubDue(flow({ next: n as FlowReply["next"], questions: q(true, true), approved: true }))));
check("order: once asked, never again", !githubDue(flow({ next: "identity", questions: q(true), github: { asked: true } })));
check("order: an older backend never asks", !githubDue({ next: "identity", questions: q(true) }));

const conversation = fs.readFileSync(path.resolve(__dirname, "..", "..", "app", "onboarding", "Conversation.tsx"), "utf-8");
const firstLookAt = conversation.indexOf('flow.next === "first_look"');
const githubAt = conversation.indexOf("githubDue(flow)");
const questionAt = conversation.indexOf("else if (question)");
check("order: the page checks first_look, then GitHub, then the questions", firstLookAt > 0 && githubAt > firstLookAt && questionAt > githubAt);

// ── "From your GitHub" ──────────────────────────────────────────────────────

check("fromYourGitHub: origin.yours", fromYourGitHub({ origin: { yours: true } }));
check("fromYourGitHub: from_your_github", fromYourGitHub({ from_your_github: true }));
check("fromYourGitHub: absent or other is false", !fromYourGitHub({ origin: { owner: "octo" } }) && !fromYourGitHub(null) && !fromYourGitHub({ origin: "yours" }));
const st = readBuildStatus({
  jobs: [{ id: "j1", name: "Notes", does: "Keeps notes.", route: "A", status: "planned", ingredients: ["github:octo/notes", "github:else/thing"] }],
  parts: [
    { ref: "github:octo/notes", name: "notes", class: "APP", kind: "app", origin: { yours: true } },
    { ref: "github:else/thing", name: "thing", class: "APP", kind: "app", origin: { owner: "else" } },
  ],
});
const ings = st.jobs[0]?.ingredients || [];
check("build status: a part from her GitHub is marked", ings.find((i) => i.ref === "github:octo/notes")?.mine === true);
check("build status: others are not", !ings.find((i) => i.ref === "github:else/thing")?.mine);
const jr = readJobReply({ job: { id: "j1", name: "Notes", ingredients: ["github:octo/notes"] }, parts: [{ ref: "github:octo/notes", name: "notes", from_your_github: true }] });
check("job reply: marked too", jr?.ingredients[0]?.mine === true);
const rows = renderToStaticMarkup(
  <IngredientRows
    items={[
      { ref: "a", name: "notes", kind: "app", check: "", removable: false, mine: true },
      { ref: "b", name: "thing", kind: "app", check: "", removable: false },
    ]}
    onToggle={noop}
    onRemove={noop}
  />,
);
check("ingredient rows: badge on hers only", (rows.match(/data-from-your-github/g) || []).length === 1 && rows.includes("From your GitHub"));

// ── no browser storage ──────────────────────────────────────────────────────

const DASH = path.resolve(__dirname, "..", "..");
const files = [
  "app/_lib/services/githubService.ts",
  "app/_components/GitHub/GitHubConnect.tsx",
  "app/_components/GitHub/FromYourGitHub.tsx",
  "app/onboarding/GitHubStep.tsx",
  "app/onboarding/githubDue.ts",
  "app/dashboard/developer/_components/GitHubTile.tsx",
];
const code = (f: string) =>
  fs
    .readFileSync(path.join(DASH, f), "utf-8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1");
for (const f of files) {
  const c = code(f);
  check(`no storage: ${f}`, !/localStorage|sessionStorage|document\.cookie|indexedDB|console\.|history\.(push|replace)State/.test(c));
}
const connectCode = code("app/_components/GitHub/GitHubConnect.tsx");
const send = connectCode.slice(connectCode.indexOf("async function sendToken"));
check(
  "token field is cleared before the token is sent",
  send.indexOf('setToken("")') > 0 && send.indexOf('setToken("")') < send.indexOf("sendGitHubToken("),
);
check("token is sent in a POST body only", /req\("\/github\/token", "POST", \{ token \}\)/.test(code("app/_lib/services/githubService.ts")));

if (failed) {
  console.error(`${failed} check(s) failed`);
  process.exit(1);
}
console.log("all GitHub connect checks passed");
