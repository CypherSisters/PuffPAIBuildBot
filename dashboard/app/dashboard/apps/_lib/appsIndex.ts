// The apps this build carries, derived rather than retyped.
//
// The membership list comes from the installed manifests — `apps/<id>/app.json`
// — by way of GET /build/self, composed onto the same `menuAside` array the
// sidebar renders. So this page and the nav cannot disagree about what exists,
// and an app the owner removed cannot linger here pretending to be installed.
//
// Descriptions come from each manifest's own `blurb`. The local map below is
// the fallback for the one case that matters: the backend has not answered yet,
// or cannot. Then the full built-in list shows, described the way it always was
// — the gate fails open, here as everywhere.
//
// The surface gate applies on top, from the same map, so an installed app whose
// surface this build never loaded still drops out. Passing no build shows
// everything.
import menuAside from "../../_lib/menuAside";
import { filterMenuByBuild, withInstalledApps, APPS_MENU_LABEL } from "../../../_lib/surfaceMap";
import { installedApps, type BuildSelf } from "../../../_lib/services/buildService";

export type AppEntry = {
  /** Route segment, e.g. "calendar". */
  id: string;
  label: string;
  href: string;
  icon?: string;
  /** One line, said plainly. Empty when nothing has been written yet. */
  description: string;
};

/** What each built-in app is for, used only when the installed list is not
 *  known and the built-in menu is being shown. An installed app's own manifest
 *  is the source of truth for its blurb; this is the fallback, keyed by route
 *  segment. */
const DESCRIPTIONS: Record<string, string> = {
  alarmflow: "Alarms and timers, with the sound played on this box.",
  calendar: "Your events and to-dos on one month view.",
  freshtable: "Pantry, shopping list, meal plan and the recipes behind them.",
  fitfam: "Workouts per person, with streaks and a history that stays here.",
  trivia: "Question rounds and scores, for when the room needs something to do.",
};

function segmentOf(href: string): string {
  const parts = href.split("?")[0].split("/").filter(Boolean);
  return parts[parts.length - 1] ?? "";
}

/** The apps this build has, in the order the sidebar lists them.
 *
 *  `build` is what this build IS, from GET /build/self. Null (unknown backend,
 *  or a build that never ran the interview) lists every built-in app. */
export function listApps(build: BuildSelf | null = null): AppEntry[] {
  const menu = filterMenuByBuild(withInstalledApps(menuAside, build), build);
  const group = menu.find((item) => item.label === APPS_MENU_LABEL);
  const items = group?.menu ?? [];

  const blurbs = new Map(
    (installedApps(build) ?? []).map((app) => [app.id, app.blurb]),
  );

  return items
    .filter((item): item is typeof item & { href: string } => Boolean(item.href))
    .map((item) => {
      const id = segmentOf(item.href);
      return {
        id,
        label: item.label,
        href: item.href,
        icon: item.icon,
        description: blurbs.get(id) || DESCRIPTIONS[id] || "",
      };
    });
}

// -- What the Apps page lists beyond the menu ----------------------------------

/** The prefix a scaffolded persona's stored blurb starts with. Kept on disk
 *  (the backend reads the goal back from it); never shown. */
const SCAFFOLD_PREFIX = "Scaffolded from your goal: ";

/** The one thing the Apps page needs to know about an owner-made app. */
export type PersonaFact = { id: string; empty?: boolean };

/** Something Toolsmith added, as GET /front sends it (see frontService). */
export type AddedApp = {
  id: string;
  name: string;
  line: string;
  href: string;
  ask_folder?: boolean;
};

/** Where an added thing with no page of its own is used: the home page. */
export const HOME_HREF = "/dashboard";
/** Said on an added thing that waits for a pictures folder. */
export const FOLDER_HINT = "Choose a pictures folder for it on the home page.";

/** The apps the Apps page lists.
 *
 *  `listApps(build)` first, then:
 *  - a scaffolded persona with nothing in it (`empty` from GET /persona/list)
 *    is left out; the bot's own page and rail entry stay;
 *  - a persona's stored "Scaffolded from your goal: ..." blurb is replaced by
 *    a plain line (the goal is her own words and stays off a subtitle);
 *  - every thing Toolsmith added (GET /front `apps`) that is not listed yet
 *    is added, so the Mood board is on this page too. One with no page of its
 *    own opens the home page, where its folder is chosen.
 *
 *  Pure: the page passes what its hooks returned, the proof passes fixtures.
 *  Null persona or front answers change nothing (fail open, as listApps). */
export function appsToList(
  build: BuildSelf | null,
  personas: PersonaFact[] | null = null,
  added: AddedApp[] | null = null,
): AppEntry[] {
  const empty = new Set((personas ?? []).filter((p) => p.empty === true).map((p) => p.id));
  const out: AppEntry[] = listApps(build)
    .filter((app) => !empty.has(app.id))
    .map((app) => (app.description.startsWith(SCAFFOLD_PREFIX)
      ? { ...app, description: `${app.label}'s own page, with the lists it keeps.` }
      : app));
  const listed = new Set(out.map((app) => app.href.split("?")[0]));
  const seen = new Set<string>();
  for (const a of added ?? []) {
    const name = (a.name || "").trim();
    if (!name) continue;
    const href = a.href || HOME_HREF;
    if (a.href && listed.has(a.href.split("?")[0])) continue;
    // The same thing added twice (same name, same place) is listed once.
    const key = `${name}|${href}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const line = (a.line || "").trim();
    out.push({
      id: a.id,
      label: name,
      href,
      description: !a.href && a.ask_folder ? `${line} ${FOLDER_HINT}`.trim() : line,
    });
  }
  return out;
}
