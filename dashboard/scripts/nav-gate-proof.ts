// Three-state proof for the navigation gates, plus the app round trip.
//
// Two gates run over the nav and neither replaces the other: `withInstalledApps`
// says which apps `apps/<id>/app.json` declares, `filterMenuByBuild` says which
// surfaces this build loaded. Both must FAIL OPEN: only a build that actually
// ran the interview hides a surface, and only a KNOWN installed-app list hides
// an app. An unreachable backend gets the whole menu.
//
// This runs the real functions - the same ones the layout and the apps index
// call - against the shapes that matter, and prints what each one sees.
//
// Run from the dashboard directory:
//   npx tsc scripts/nav-gate-proof.ts --outDir .proofout --module commonjs \
//     --target es2020 --moduleResolution node --esModuleInterop --skipLibCheck
//   node .proofout/scripts/nav-gate-proof.js [apps.json] && rm -rf .proofout
//
// The optional argument is a JSON file holding the `apps` array exactly as
// GET /build/self serves it (`python -c "import json,puffpai.app_registry as r;
// print(json.dumps(r.ui_records()))"`). Without it, the installed-app list is
// unknown and the built-in menu must show in full.
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import * as fs from "fs";
import menuAside from "../app/dashboard/_lib/menuAside";
import { listApps } from "../app/dashboard/apps/_lib/appsIndex";
// The gate the rail and the quick search actually call, through the same
// `buildMenu` — proving anything else would prove the wrong thing.
import { assistantName, buildMenu } from "../app/_lib/surfaceMap";
import type {
  BuildSelf,
  InstalledApp,
} from "../app/_lib/services/buildService";

const appsFile = process.argv[2];
const INSTALLED: InstalledApp[] | null = appsFile
  ? (JSON.parse(fs.readFileSync(appsFile, "utf8")) as InstalledApp[])
  : null;

function shape(
  name: string,
  trimmed: boolean,
  allowlist: string[],
  apps: InstalledApp[] | null,
): BuildSelf {
  return {
    configured: true,
    schema: "1",
    generated_at: 0,
    build: { name, identity: "", based_on: "" },
    capabilities: {
      configured: true, trimmed, available: [], unavailable: {},
      can_plan: false, can_reach_wan: false, summary: [],
    },
    surfaces: {
      trimmed, allowlist, reasons: {}, derived_from_goal: true,
      owner_edited: false, taxonomy: {},
    },
    apps,
  } as unknown as BuildSelf;
}

const SPACE = shape("Orrery", true, [
  "instruments.telescope",
  "knowledge.library",
], INSTALLED);
const STOCK = shape("", false, [], INSTALLED);
// The installed-app fetch failed, or an older backend never sent the field.
// This build is untrimmed and its app list is unknown: it must see everything.
const APPS_UNKNOWN = shape("", false, [], null);
const NULL_BUILD: BuildSelf | null = null;

function labels(build: BuildSelf | null): string[] {
  const out: string[] = [];
  const walk = (items: ReturnType<typeof buildMenu>) => {
    for (const i of items) {
      out.push(i.label);
      if (i.menu) walk(i.menu);
    }
  };
  walk(buildMenu(build, menuAside));
  return out;
}

console.log(
  INSTALLED
    ? `installed apps read from ${appsFile}: ${INSTALLED.map((a) => a.id).join(", ") || "(none)"}`
    : "installed-app list: NOT KNOWN (no file given)",
);

for (const [name, build] of [
  ["apps unknown, untrimmed (installed-app fetch failed)", APPS_UNKNOWN],
  ["stock build (untrimmed)", STOCK],
  ["space bot (trimmed: telescope + library)", SPACE],
  ["null build (backend unreachable)", NULL_BUILD],
] as [string, BuildSelf | null][]) {
  const nav = labels(build);
  const apps = listApps(build);
  console.log(`\n=== ${name}`);
  console.log(`  sidebar : ${nav.join(", ")}`);
  console.log(`  apps    : ${apps.length ? apps.map((a) => a.label).join(", ") : "(none)"}`);
  console.log(`  blurbs  : ${apps.map((a) => `${a.id}=${JSON.stringify(a.description)}`).join(" | ") || "(none)"}`);
  console.log(`  name    : ${assistantName(build)}`);
  const banned = ["FreshTable", "FitFam", "Calendar", "AlarmFlow", "Trivia",
                  "Smart Home"];
  const present = banned.filter(
    (b) => nav.includes(b) || apps.some((a) => a.label === b),
  );
  console.log(`  persona items present: ${present.length ? present.join(", ") : "NONE"}`);
}
