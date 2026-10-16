// Three-state proof for the LANDING PAGE surface gate.
//
// Companion to nav-gate-proof.ts, which covers the sidebar and the apps index.
// This one covers app/dashboard/page.tsx: the workbench is the page, and every
// tile below it is persona furniture decided by the same rule.
//
// The gate must FAIL OPEN: only a build that actually ran the interview and
// carries an allowlist hides anything. A stranger whose backend hiccups gets
// the whole page, never a blank one - so an untrimmed build and a null build
// must produce byte-identical output.
//
// Run from the dashboard directory:
//   npx tsc scripts/landing-gate-proof.ts --outDir .proofout --module commonjs //     --target es2020 --moduleResolution node --esModuleInterop --skipLibCheck
//   node .proofout/scripts/landing-gate-proof.js && rm -rf .proofout
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import { readFileSync } from "fs";
import { join } from "path";
import { hrefLoaded, widgetLoaded } from "../app/_lib/surfaceMap";
import type { BuildSelf } from "../app/_lib/services/buildService";

/** The draggable tile ids in app/dashboard/page.tsx, in page order. Keep in
 *  step with that file: a tile added there and not here proves nothing. */
const TILE_IDS = [
  "weather",
  "jetson",
  "smart-home",
  "cameras",
  "schedule",
  "reminders",
  "recent-chats",
  "apps",
  "meals",
  "workout",
];

/** The app launcher strip, same page. */
const APP_HREFS: [string, string][] = [
  ["AlarmFlow", "/dashboard/apps/alarmflow"],
  ["Calendar", "/dashboard/apps/calendar"],
  ["FreshTable", "/dashboard/apps/freshtable"],
  ["FitFam", "/dashboard/apps/fitfam"],
  ["Trivia", "/dashboard/apps/trivia"],
];

function shape(name: string, trimmed: boolean, allowlist: string[]): BuildSelf {
  return {
    configured: true,
    schema: "1",
    generated_at: 0,
    build: { name, identity: "", based_on: "" },
    surfaces: {
      trimmed,
      allowlist,
      reasons: {},
      derived_from_goal: true,
      owner_edited: false,
      taxonomy: {},
    },
  } as unknown as BuildSelf;
}

const SPACE = shape("Orrery", true, [
  "instruments.telescope",
  "knowledge.library",
  "knowledge.maps",
]);
const STOCK = shape("", false, []);
const NULL_BUILD: BuildSelf | null = null;

/** Exactly what page.tsx renders: widget gate, then the empty-launcher rule. */
function rendered(build: BuildSelf | null) {
  const apps = APP_HREFS.filter(([, href]) => hrefLoaded(build, href)).map(
    ([label]) => label,
  );
  const tiles = TILE_IDS.filter((id) => widgetLoaded(build, id)).filter(
    (id) => id !== "apps" || apps.length > 0,
  );
  return { tiles, apps };
}

const results = new Map<string, string>();
for (const [name, build] of [
  ["space bot (trimmed: telescope + library + maps)", SPACE],
  ["stock build (untrimmed)", STOCK],
  ["null build (backend unreachable)", NULL_BUILD],
] as [string, BuildSelf | null][]) {
  const { tiles, apps } = rendered(build);
  const dropped = TILE_IDS.filter((id) => !tiles.includes(id));
  console.log(`\n=== ${name}`);
  console.log(`  hero+rail : always (the shell, chat, the avatar, this box)`);
  console.log(`  tiles     : ${tiles.join(", ") || "(none)"}`);
  console.log(`  dropped   : ${dropped.join(", ") || "(none)"}`);
  console.log(`  apps      : ${apps.length ? apps.join(", ") : "(none)"}`);
  results.set(name, JSON.stringify({ tiles, apps }));
}

const stock = results.get("stock build (untrimmed)");
const nul = results.get("null build (backend unreachable)");
const space = results.get("space bot (trimmed: telescope + library + maps)");
const full = JSON.stringify({
  tiles: TILE_IDS,
  apps: APP_HREFS.map(([label]) => label),
});

// The front page above the board: the bot's own words from GET /front. No
// hard-coded wake-word promise (the backend says "Hey Puff" only when a wake
// word really works on this build) and no hard-coded colours (the theme's
// variables only, so a look the owner picks reaches the home page).
function code(rel: string): string {
  return readFileSync(join(process.cwd(), rel), "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/(^|[^:])\/\/.*$/gm, "$1")
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, "");
}
const homePage = code("app/dashboard/page.tsx");
const frontPage = code("app/_components/Front/FrontPage.tsx");
console.log("\n--- home page source");

const checks: [string, boolean][] = [
  ["home page renders the front page", homePage.includes("<FrontPage")],
  ["no 'Hey Puff' promise written into the home page", !/Hey Puff/i.test(homePage + frontPage)],
  ["the transcript tile (which promises it) is not on the home page", !homePage.includes("TranscriptTile")],
  ["no list-chore 'Try:' line on the home page", !homePage.includes("tryLine")],
  ["front page uses theme colours only (no #hex, rgb or named white)",
    !/#[0-9a-fA-F]{3,8}\b|rgba?\(|["']#fff|["']white["']/.test(frontPage)],
  ["untrimmed and null are identical", stock === nul],
  ["null carries the whole page", nul === full],
  ["untrimmed carries the whole page", stock === full],
  ["trimmed loses furniture", space !== full],
  // Any bot with the weather forecast switched on carries the weather card
  // (where its town is typed), whatever its page list says.
  ["trimmed bot without weather has no weather card",
    !rendered(SPACE).tiles.includes("weather")],
  ["trimmed bot with the weather switched on has the weather card",
    rendered({ ...SPACE, weather_on: true } as BuildSelf).tiles.includes("weather")],
  ["weather switched on adds only the weather card",
    rendered({ ...SPACE, weather_on: true } as BuildSelf).tiles.length ===
      rendered(SPACE).tiles.length + 1],
];
console.log("\n--- fail-open assertions ---");
for (const [label, ok] of checks) {
  console.log(`  ${ok ? "OK  " : "FAIL"} ${label}`);
}
const passed = checks.every(([, ok]) => ok);
console.log(passed ? "\nPASS" : "\nFAIL");
process.exit(passed ? 0 : 1);
