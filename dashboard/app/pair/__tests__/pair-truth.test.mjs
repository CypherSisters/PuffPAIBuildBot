/**
 * The pairing screen tells the truth about where it is and how to get a code.
 *
 *   node app/pair/__tests__/pair-truth.test.mjs
 *
 * Plain Node, no test runner, no network, no build. Runs the real helper
 * (app/pair/pairWords.ts, Node type stripping) and reads the page sources with
 * comments stripped. Exits non-zero on any failure.
 *
 * REGRESSIONS UNDER TEST:
 *  1. "Connected to: http://localhost:8000" on every bot. The page was built
 *     ahead of time, so it printed the address the BUILD saw, and it called a
 *     fallback a connection.
 *  2. "npm run pair:code", which works only from the dashboard folder of the
 *     code copy. The real ways: the window that started the bot, puffpai pair.
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const pairDir = join(here, "..");
const read = (p) => readFileSync(join(pairDir, p), "utf8");
const code = (src) =>
  src.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "").replace(/\{\/\*[\s\S]*?\*\/\}/g, "");

let pass = 0;
let fail = 0;
function ok(name, cond) {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
}

const W = await import(pathToFileURL(join(pairDir, "pairWords.ts")).href);
const page = code(read("page.tsx"));
const form = code(read("PairForm.tsx"));

// The page reads the address per request, not at build time
ok("page is rendered per request", /export const dynamic = "force-dynamic";/.test(page));
ok("page passes whether the address was set", /backendConfigured=\{configured\}/.test(page)
  && /Boolean\(process\.env\.PUFFPAI_BACKEND_URL\)/.test(page));

// No false words
ok("no 'Connected to' anywhere", !/Connected to/.test(form) && !/Connected to/.test(page));
ok("no npm run pair:code on the screen", !/pair:code/.test(form));
ok("the browser address comes from window.location.origin", /window\.location\.origin/.test(form));

// The helper
const set = W.whereLines("http://127.0.0.1:3340", "http://127.0.0.1:8340", true);
ok("shows the page address", set.page === "This page: http://127.0.0.1:3340");
ok("shows the bot address", set.bot.endsWith("http://127.0.0.1:8340"));
ok("no fallback note when the address was set", set.botNote === null);

const unset = W.whereLines(null, undefined, false);
ok("no page line before the browser knows it", unset.page === null);
ok("the fallback is named as the usual address, not a connection",
  unset.bot.endsWith(W.DEFAULT_BACKEND_URL) && /usual address/.test(unset.botNote || ""));

const ways = W.FRESH_CODE_WAYS.join(" ");
ok("names the window that started the bot", /window that started your bot/.test(ways));
ok("names puffpai start", /puffpai start/.test(ways));
ok("names puffpai pair", /puffpai pair/.test(ways));
ok("the short hint names puffpai pair", /puffpai pair/.test(W.FRESH_CODE_SHORT));
ok("no em dash in the words", !/—/.test(ways + W.FRESH_CODE_SHORT + (unset.botNote || "")));

// The proxy's fallback and the page's are the same value
const proxy = readFileSync(join(pairDir, "..", "api", "pai", "[...path]", "route.ts"), "utf8");
ok("fallback matches the proxy's", proxy.includes(`?? "${W.DEFAULT_BACKEND_URL}"`));

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
