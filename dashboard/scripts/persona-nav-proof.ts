// The persona the owner built passes the gate. Proof, not documentation.
//
// An owner made a space bot called Infinity. Her allowlist was comms.calendar,
// daily.notes, daily.weather, instruments.telescope, knowledge.library — and
// her bot appeared nowhere in the rail, because a scaffolded persona's only
// home was the "Apps" submenu and that entry carries no `railGroup`
// (menuAside.ts): it is search-only, absorbed by the home board's Apps tile.
//
// The entry is now MADE by `withPersonaNav` (nothing else gives a persona a
// rail group) and GATED like every other page: scaffolding a persona adds its
// own surface, `persona.<id>`, to the build's allowlist, so it is shown because
// it is loaded — not because it is excepted. The allowlist stays strict.
//
// This script asserts both halves: with the persona surface it shows, without
// it it does not, and a build made before the change shows it once the repair
// (persona_scaffold.repair_surfaces) has put the surface on the list. It exits
// non-zero on failure, so it can sit in `npm run test:gates` next to the
// nav-gate proof.
//
// Run from the dashboard directory:
//   npm run test:gates
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import menuAside from "../app/dashboard/_lib/menuAside";
import {
  buildMenu,
  personaGoal,
  personaPrompts,
  personaSurface,
  withPersonaNav,
} from "../app/_lib/surfaceMap";
import type { BuildSelf } from "../app/_lib/services/buildService";
import type { OwnerApp } from "../app/_lib/services/personaService";
import type { MenuAsideItem } from "../app/_interfaces";

let failures = 0;

function check(name: string, ok: boolean, detail = "") {
  console.log(`  ${ok ? "ok  " : "FAIL"}  ${name}${detail ? ` — ${detail}` : ""}`);
  if (!ok) failures += 1;
}

function shape(
  name: string,
  allowlist: string[],
  trimmed = true,
): BuildSelf {
  return {
    configured: true,
    schema: "1",
    generated_at: 0,
    build: { name, identity: "", based_on: "" },
    capabilities: {
      configured: true, trimmed: true, available: [], unavailable: {},
      can_plan: false, can_reach_wan: false, summary: [],
    },
    surfaces: {
      trimmed, allowlist, reasons: {}, derived_from_goal: true,
      owner_edited: false, taxonomy: {},
    },
    // A KNOWN, EMPTY installed-app list: the harshest honest case. The built-in
    // Apps submenu empties out entirely, so nothing but withPersonaNav can put
    // the owner's bot on screen.
    apps: [],
  } as unknown as BuildSelf;
}

const INFINITY: OwnerApp = {
  id: "infinity",
  name: "Infinity",
  blurb:
    "Scaffolded from your goal: knows everything about space, space facts, " +
    "current space events, astronomy and Earth weather events",
  route: "/dashboard/apps/infinity",
  lists: ["launch_log", "targets"],
  list_labels: ["launch log", "targets"],
  try: "add something to the launch log list",
};

/** The id `apps` is legal and must keep working until the owner renames it. */
const NAMED_APPS: OwnerApp = {
  ...INFINITY,
  id: "apps",
  name: "Apps",
  route: "/dashboard/apps/apps",
};

function flatten(items: MenuAsideItem[]): MenuAsideItem[] {
  const out: MenuAsideItem[] = [];
  const walk = (list: MenuAsideItem[]) => {
    for (const i of list) {
      out.push(i);
      if (i.menu) walk(i.menu);
    }
  };
  walk(items);
  return out;
}

function railEntry(
  build: BuildSelf | null,
  apps: OwnerApp[] | null,
  href: string,
): MenuAsideItem | undefined {
  const menu = withPersonaNav(buildMenu(build, menuAside), apps, build);
  return flatten(menu).find((i) => i.href === href && i.railGroup);
}

/** The allowlist that hid Infinity, as the owner actually has it. */
const OWNER_ALLOWLIST = [
  "comms.calendar",
  "daily.notes",
  "daily.weather",
  "instruments.telescope",
  "knowledge.library",
];

// ── The owner's allowlist, WITH the persona's own surface ────────────────────
console.log("\n=== the owner's allowlist plus the persona's own surface");
{
  const build = shape("Infinity", [
    ...OWNER_ALLOWLIST,
    personaSurface(INFINITY),
  ]);
  const entry = railEntry(build, [INFINITY], "/dashboard/apps/infinity");
  check("Infinity has a rail entry", Boolean(entry), entry?.label ?? "absent");
  check("named after the bot", entry?.label === "Infinity", entry?.label ?? "");
  check("in a rail group", entry?.railGroup === "primary", entry?.railGroup ?? "none");
  const menu = withPersonaNav(buildMenu(build, menuAside), [INFINITY], build);
  check(
    "right after Dashboard",
    menu.findIndex((i) => i.href === "/dashboard/apps/infinity") ===
      menu.findIndex((i) => i.href === "/dashboard") + 1,
  );
  // The gate itself must not have been loosened by any of this.
  check(
    "the meal planner is still gone",
    !flatten(menu).some((i) => i.label === "FreshTable"),
  );
}

// ── The same allowlist WITHOUT it: the rule is strict ────────────────────────
console.log("\n=== the same allowlist without the persona surface");
{
  // This is the build a pre-change scaffold left behind: the persona is
  // installed, daily.notes is loaded, and its own surface is on no list.
  const build = shape("Infinity", OWNER_ALLOWLIST);
  check(
    "no rail entry — not loaded means not loaded",
    !railEntry(build, [INFINITY], "/dashboard/apps/infinity"),
  );
  check(
    "daily.notes does not smuggle it in",
    build.surfaces.allowlist.includes("daily.notes") &&
      !railEntry(build, [INFINITY], "/dashboard/apps/infinity"),
  );
}

// ── The legacy build, after the repair has run ───────────────────────────────
console.log("\n=== a build scaffolded before this change, repaired");
{
  // persona_scaffold.repair_surfaces() adds exactly one thing to that build:
  // the persona's own surface. Nothing else about the allowlist changes.
  const repaired = shape("Infinity", [
    ...OWNER_ALLOWLIST,
    personaSurface(INFINITY),
  ]);
  check(
    "the repaired build shows it",
    Boolean(railEntry(repaired, [INFINITY], "/dashboard/apps/infinity")),
  );
}

// ── An allowlist with nothing but the persona surface ────────────────────────
console.log("\n=== an allowlist with no apps.* and no daily.* surface");
{
  const build = shape("Infinity", [
    "instruments.telescope",
    "knowledge.library",
    personaSurface(INFINITY),
  ]);
  check(
    "Infinity still has a rail entry",
    Boolean(railEntry(build, [INFINITY], "/dashboard/apps/infinity")),
  );
}

// ── Fail open, exactly like every other gate ─────────────────────────────────
console.log("\n=== the manifest is not loaded yet, or was never trimmed");
{
  check(
    "unknown build shows it",
    Boolean(railEntry(null, [INFINITY], "/dashboard/apps/infinity")),
  );
  const untrimmed = shape("Infinity", [], false);
  check(
    "untrimmed build shows it",
    Boolean(railEntry(untrimmed, [INFINITY], "/dashboard/apps/infinity")),
  );
}

// ── The id `apps` ────────────────────────────────────────────────────────────
console.log("\n=== a persona whose id is literally `apps`");
{
  const build = shape("Apps", ["knowledge.library", personaSurface(NAMED_APPS)]);
  const entry = railEntry(build, [NAMED_APPS], "/dashboard/apps/apps");
  check("it gets its own rail entry", Boolean(entry));
  check(
    "and is not confused with the Apps index",
    entry?.href === "/dashboard/apps/apps",
    entry?.href ?? "",
  );
}

// ── No persona: nothing new renders ──────────────────────────────────────────
console.log("\n=== no persona on this build");
{
  const build = shape("", ["knowledge.library"]);
  const before = flatten(buildMenu(build, menuAside)).length;
  check(
    "empty list adds nothing",
    flatten(withPersonaNav(buildMenu(build, menuAside), [], build)).length ===
      before,
  );
  check(
    "unknown list adds nothing",
    flatten(withPersonaNav(buildMenu(build, menuAside), null, build)).length ===
      before,
  );
}

// ── The card's copy comes from the owner's own words ─────────────────────────
console.log("\n=== the Meet card's copy");
{
  const goal = personaGoal(INFINITY);
  check("the scaffold prefix is stripped", !goal.startsWith("Scaffolded from"), goal);
  check("the goal is the owner's words", goal.startsWith("knows everything about space"));
  check("bounded", goal.length <= 181, String(goal.length));
  // The backend now sends the goal as its own field. The blurb strip above is
  // the fallback for a reply that has none.
  const sent = personaGoal({ ...INFINITY, goal: "keeps the seed list" });
  check("the sent goal wins over the blurb", sent === "keeps the seed list", sent);

  // The Meet card's prompts are the front page's checked questions (GET
  // /front try_asking), never a chore about one of the app's lists.
  const asked = [
    "Show me the latest pictures",
    "Tell me about Mars",
    "Tell me about Saturn",
    "What can you do?",
  ];
  const prompts = personaPrompts(INFINITY, asked);
  check("three prompts from the front page", prompts.length === 3, prompts.join(" | "));
  check(
    "in the front page's order, as sent",
    prompts.every((p, i) => p === asked[i]),
    prompts.join(" | "),
  );
  check(
    "none of them is a list chore, even for an app that keeps lists",
    !prompts.some((p) => /\blist\b/i.test(p)),
    prompts.join(" | "),
  );
  const waiting = personaPrompts(INFINITY);
  check(
    "no front page yet: only the question every build answers",
    waiting.length === 1 && waiting[0] === "What can you do?",
    waiting.join(" | "),
  );
  const generic = [...waiting];
  // A product rule, enforced everywhere a string reaches the screen.
  const words = [...prompts, ...generic, goal, "Meet", "Try saying"];
  check("no banned word", !words.some((w) => w.toLowerCase().includes("hatch")));
}

console.log(failures === 0 ? "\npersona nav: PASS" : `\npersona nav: ${failures} FAILURE(S)`);
if (failures > 0) process.exit(1);
