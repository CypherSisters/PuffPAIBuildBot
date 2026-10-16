// What the Apps page lists, and the Developer Vault card line, proven without
// a browser.
//
// Run from the dashboard directory (part of `npm run test:gates`).
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import { appsToList, FOLDER_HINT, HOME_HREF } from "../app/dashboard/apps/_lib/appsIndex";
import { vaultStatus } from "../app/dashboard/developer/overviewWords";
import type { BuildSelf, InstalledApp, VaultZone } from "../app/_lib/services/buildService";
import { FOLDER_NO_ANSWER, folderReply } from "../app/_lib/moodBoard";
import { appOnBuild } from "../app/_lib/surfaceMap";
import { noAppsLine, readFirstApps, turnWords } from "../app/_lib/services/firstAppsService";

let failed = 0;
function check(name: string, ok: boolean): void {
  console.log(`${ok ? "PASS" : "FAIL"} ${name}`);
  if (!ok) failed += 1;
}

// The wedding run's build: a trimmed build carrying the web app and the
// persona, nothing shipped.
const installed: InstalledApp[] = [
  {
    id: "bunny-s-big-day-rzwx3knd", name: "Bunny's Big Day",
    blurb: "Bunny's Big Day, a wedding planning dashboard.",
    route: "/dashboard/apps/bunny-s-big-day-rzwx3knd", icon: "", surfaces: [], order: 500,
  } as InstalledApp,
  {
    id: "weddingbot", name: "WeddingBot",
    blurb: "Scaffolded from your goal: I want to build a wedding planner bot. My brother just got engaged",
    route: "/dashboard/apps/weddingbot", icon: "mdiNotebookOutline",
    surfaces: ["persona.weddingbot"], order: 600,
  } as InstalledApp,
];
const build = {
  configured: true,
  surfaces: { trimmed: false, allowlist: [] },
  apps: installed,
} as unknown as BuildSelf;

const added = [
  { id: "bunny-s-big-day-rzwx3knd", name: "Bunny's Big Day", line: "Bunny's Big Day. Plan it.",
    href: "/dashboard/apps/bunny-s-big-day-rzwx3knd" },
  { id: "mood-board-u5p66lvn", name: "Mood board", line: "Mood board. Themes from your pictures.",
    href: "", ask_folder: true },
  { id: "mood-board-zcgbwc3t", name: "Mood board", line: "Mood board. Themes from your pictures.",
    href: "", ask_folder: true },
];

const labels = (xs: { label: string }[]) => xs.map((a) => a.label);

{
  const got = appsToList(build, [{ id: "weddingbot", empty: true }], added);
  check("the empty persona is left out", !labels(got).includes("WeddingBot"));
  check("the web app is listed once", labels(got).filter((l) => l === "Bunny's Big Day").length === 1);
  const mood = got.filter((a) => a.label === "Mood board");
  check("the Mood board is listed, once", mood.length === 1);
  check("the Mood board opens the home page", mood[0]?.href === HOME_HREF);
  check("the Mood board says where its folder is chosen", (mood[0]?.description ?? "").endsWith(FOLDER_HINT));
}
{
  const got = appsToList(build, [{ id: "weddingbot", empty: false }], added);
  const persona = got.find((a) => a.id === "weddingbot");
  check("a persona with something in it is listed", Boolean(persona));
  check("its line is plain, never the scaffold prefix or her goal",
    persona?.description === "WeddingBot's own page, with the lists it keeps.");
}
{
  const got = appsToList(build, null, null);
  check("unknown persona and front answers change nothing but the blurb",
    labels(got).join(",") === "Bunny's Big Day,WeddingBot");
  check("no line anywhere starts with the scaffold prefix",
    got.every((a) => !a.description.startsWith("Scaffolded from your goal")));
}

// -- the Mood board's own page --
{
  const page = "/dashboard/mood-board/mood-board-u5p66lvn";
  const got = appsToList(build, [{ id: "weddingbot", empty: true }],
    [{ id: "mood-board-u5p66lvn", name: "Mood board", line: "Mood board. Themes from your pictures.",
       href: page, ask_folder: true }]);
  const mood = got.filter((a) => a.label === "Mood board");
  check("on the Apps page the Mood board opens its own page, not home", mood[0]?.href === page);
}
check("a refused folder shows the bot's own reason",
  folderReply(false, { ok: false, message: "That folder is not there." }) === "That folder is not there.");
check("a refused folder outside her folders says which ones",
  folderReply(false, { message: "Only folders inside Pictures, Documents, Music, Videos, Desktop or Downloads can be shared." })
    .startsWith("Only folders inside Pictures"));
check("a folder that worked says what was found",
  folderReply(true, { ok: true, line: "Looked at 6 pictures and found 6 colour themes." }).startsWith("Looked at 6"));
check("no answer at all says so, plainly", folderReply(false, null) === FOLDER_NO_ANSWER);

// -- the dashboard reads only apps this build has --
{
  check("unknown build: the app is read (fail open)", appOnBuild(null, "alarmflow"));
  check("a build without AlarmFlow is not asked about it", !appOnBuild(build, "alarmflow"));
  check("a build without Calendar is not asked about it", !appOnBuild(build, "calendar"));
  const withAlarms = {
    ...build,
    apps: [...installed, { id: "alarmflow", name: "AlarmFlow", blurb: "", route: "/dashboard/apps/alarmflow",
      icon: "", surfaces: [], order: 10 } as InstalledApp],
  } as unknown as BuildSelf;
  check("a build with AlarmFlow reads it", appOnBuild(withAlarms, "alarmflow"));
}

// -- your first apps: off on a new bot, listed with a Turn on button --
{
  // A new bot: /build/self carries none of the five, so the Apps list and the
  // sidebar have none of them; "Your first apps" lists all five, off.
  const fresh = { configured: true, surfaces: { trimmed: false, allowlist: [] }, apps: [] } as unknown as BuildSelf;
  const five = ["alarmflow", "calendar", "freshtable", "fitfam", "trivia"];
  check("a new bot lists none of the five as apps", appsToList(fresh, null, null).length === 0);
  check("a new bot does not read the five apps' data", five.every((id) => !appOnBuild(fresh, id)));
  const listed = readFirstApps({
    ok: true,
    apps: five.map((id) => ({ id, name: id, blurb: `${id} line`, route: `/dashboard/apps/${id}`, icon: "", on: false })),
  });
  check("Your first apps lists all five", listed.map((a) => a.id).join(",") === five.join(","));
  check("each starts off, with a Turn on button", listed.every((a) => !a.on && turnWords(a) === "Turn on"));
  check("an app that is on says Turn off", turnWords({ ...listed[0], on: true }) === "Turn off");
  check("a bad answer lists nothing", readFirstApps(null).length === 0 && readFirstApps({ apps: "x" }).length === 0);
  check("an id that is not an app id is dropped", readFirstApps({ apps: [{ id: "../x", name: "x" }] }).length === 0);
  // FitFam turned on: /build/self carries it, so it is listed and read.
  const withFit = {
    ...fresh,
    apps: [{ id: "fitfam", name: "FitFam", blurb: "Workouts.", route: "/dashboard/apps/fitfam", icon: "", surfaces: [], order: 40 } as InstalledApp],
  } as unknown as BuildSelf;
  check("a first app turned on is listed", labels(appsToList(withFit, null, null)).join(",") === "FitFam");
  check("a first app turned on is read", appOnBuild(withFit, "fitfam") && !appOnBuild(withFit, "trivia"));
  check("an unpaired browser is told to pair, not to turn apps on below",
    noAppsLine({ apps: [], unpaired: true }) === "No apps are on yet. Pair this device to turn on your first apps.");
  check("a paired browser with first apps is pointed below",
    noAppsLine({ apps: listed, unpaired: false }).includes("Turn on one of your first apps below"));
  check("no answer never promises a section that is not drawn",
    !noAppsLine(null).includes("below") && !noAppsLine({ apps: [], unpaired: false }).includes("below"));
}

const vault = (v: Partial<VaultZone>) => ({ initialized: true, ...v }) as VaultZone;
check("vault: unreadable", vaultStatus(undefined) === "Could not be read");
check("vault: not made", vaultStatus(vault({ initialized: false })) === "Not made yet");
check("vault: locked", vaultStatus(vault({ unlocked: false })) === "Made, locked");
check("vault: open", vaultStatus(vault({ unlocked: true })) === "Made, open now");
check("vault: drive away", vaultStatus(vault({ unlocked: false, missing: "drive" })) === "Made, its drive is not plugged in");
check("vault: unplugged, as the backend really sends it", vaultStatus(vault({ initialized: false, missing: "Your vault drive is not plugged in." })) === "Made, its drive is not plugged in");
check("vault: an older backend never claims open", vaultStatus(vault({})) === "Made");

if (failed) {
  console.log(`\n${failed} check(s) failed`);
  process.exit(1);
}
console.log("\nall apps-listing checks passed");
