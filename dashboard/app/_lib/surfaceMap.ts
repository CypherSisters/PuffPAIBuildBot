// Which part of the UI needs which surface — one map, read by everything.
//
// A build that ran the interview carries an allowlist of surfaces (the closed
// set in `build_interview.surface_taxonomy`). The body of the dashboard already
// respects it; so must the navigation around it, or a space bot ends up with a
// meal planner in its sidebar and nothing behind the link.
//
// This file is the single place that says "the FreshTable route needs
// daily.kitchen". The dashboard tiles, the sidebar, the quick search and the
// apps index all read it, so adding a surface-gated page is one edit.
//
// It is also the place that says which APPS exist. That answer is not ours to
// type: it comes from the installed manifests (`apps/<id>/app.json`) by way of
// GET /build/self, so removing an app folder removes its sidebar entry, its
// card on the apps index and its tile - rather than leaving three links to
// something that is not there. `withInstalledApps` applies that, and composes
// with `filterMenuByBuild` rather than replacing it: one says installed, the
// other says loaded, and a build needs both.
//
// THE GATES FAIL OPEN, and that is not an accident. `anySurfaceLoaded` treats
// an unknown build (backend unreachable, fetch not landed) and an untrimmed
// build as "show everything", and `installedApps` returning null - unknown app
// list - leaves the built-in menu whole. Nothing here may add a check in front
// of either: a stranger whose backend hiccups must get the whole nav, never an
// empty one.
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.
import {
  mdiAlarmLight,
  mdiBookOpenPageVariant,
  mdiCalendar,
  mdiCamera,
  mdiCartOutline,
  mdiChartLine,
  mdiCheckboxMarkedOutline,
  mdiCog,
  mdiControllerClassic,
  mdiCurrencyUsd,
  mdiFire,
  mdiFlower,
  mdiFood,
  mdiHelpCircle,
  mdiHome,
  mdiLightbulbOutline,
  mdiMusic,
  mdiNotebookOutline,
  mdiPaw,
  mdiPill,
  mdiTelescope,
  mdiThermometer,
  mdiViewGridOutline,
  mdiWeatherPartlyCloudy,
} from "@mdi/js";
// Deliberately NOT in APP_ICONS below: this is the dashboard's own icon for a
// persona the owner made, not an icon name a manifest is allowed to ask for.
import { mdiStarFourPointsOutline } from "@mdi/js";
import {
  anySurfaceLoaded,
  installedApps,
  type BuildSelf,
  type InstalledApp,
} from "./services/buildService";
import type { OwnerApp } from "./services/personaService";
import type { MenuAsideItem } from "../_interfaces";

/** The product's own name, used only when the build has not been given one. */
export const PRODUCT_NAME = "PuffPAI";

/**
 * Routes that only exist when a surface is loaded, keyed by href.
 *
 * A route absent from this map is unconditional — chat, knowledge, the vault,
 * the profile and the setup wizard belong to every build, whatever it was made
 * for. Only leaves are listed; a submenu is gated by its children.
 */
export const SURFACES_BY_HREF: Record<string, string[]> = {
  "/dashboard/homeassistant": [
    "house.lights",
    "house.climate",
    "house.sensors",
    "house.cameras",
  ],
  // No /dashboard/media entry: BuildBot ships no media hub, so there is no
  // route to gate. A build that loads a media.* surface still gets it in
  // conversation — this map only decides what the dashboard draws.
  "/dashboard/apps/alarmflow": ["comms.timers"],
  "/dashboard/apps/calendar": ["comms.calendar"],
  "/dashboard/apps/freshtable": ["daily.kitchen"],
  "/dashboard/apps/fitfam": ["daily.fitness"],
  "/dashboard/apps/trivia": ["daily.games"],
};

/**
 * Landing-page widgets that only exist when a surface is loaded, keyed by the
 * widget id used in the dashboard's own tile list. Separate from the route map
 * because several of these are panels, not destinations.
 */
export const SURFACES_BY_WIDGET: Record<string, string[]> = {
  schedule: ["comms.calendar"],
  meals: ["daily.kitchen"],
  workout: ["daily.fitness"],
  "smart-home": ["house.lights", "house.climate", "house.sensors"],
  cameras: ["house.cameras"],
  // The design shell splits what used to be one "reminders-weather" tile into
  // two: a reminders list and a weather chip-tile.
  reminders: ["comms.timers", "comms.calendar"],
  // The weather card belongs to the weather page the build chose (a garden
  // bot, a space bot that likes weather), not to the calendar, or a space bot
  // gets the weather page and no card.
  weather: ["daily.weather"],
  // NOT gated, deliberately: transcript, jetson (this box's own telemetry),
  // library and recent-chats. Those are the build looking at itself and at its
  // own knowledge, which every build does whatever it was made for.
  //
  // NOT gated either, and for a stronger reason: the Digital Human stays on
  // every build.
  // It was briefly treated as media furniture, on the reasoning that an
  // animated presence is decoration. That was wrong. The face is how a build
  // stops feeling like a service and starts feeling like yours - which is the
  // whole point of the workbench - and a space bot deserves that as much as a
  // household one. What has to be easy is REPLACING it with your own.
  //   avatar: <-- do not add. Leaving it unmapped is the decision.
};

/**
 * The route prefix that makes a nav entry an APP rather than a base capability.
 *
 * Dashboard, Chat, Vault, Knowledge, Agents, Setup and the rest are what every
 * build has. Only what lives under this prefix is an installable, removable
 * app, and only that is filtered by what `apps/` actually contains.
 */
const APP_ROUTE_PREFIX = "/dashboard/apps/";

/** The nav group the installable apps hang under. */
export const APPS_MENU_LABEL = "Apps";

/**
 * Icon names an app may ask for, and the paths they mean.
 *
 * A manifest declares an icon NAME. It is looked up here, never used as a path:
 * an SVG `d` attribute is drawing instructions, and taking one from an app
 * author would be handing a stranger a pen. An unknown name gets the generic
 * app icon, which is why this list can stay short without breaking anyone.
 */
const APP_ICONS: Record<string, string> = {
  mdiAlarmLight,
  mdiBookOpenPageVariant,
  mdiCalendar,
  mdiCamera,
  mdiCartOutline,
  mdiChartLine,
  mdiCheckboxMarkedOutline,
  mdiCog,
  mdiControllerClassic,
  mdiCurrencyUsd,
  mdiFire,
  mdiFlower,
  mdiFood,
  mdiHelpCircle,
  mdiHome,
  mdiLightbulbOutline,
  mdiMusic,
  mdiNotebookOutline,
  mdiPaw,
  mdiPill,
  mdiTelescope,
  mdiThermometer,
  mdiViewGridOutline,
  mdiWeatherPartlyCloudy,
};

/** The path for an app's declared icon name, or the generic app icon. */
export function appIconPath(name: string): string {
  return APP_ICONS[name] ?? mdiViewGridOutline;
}

/** How long an app's own name may be on screen. Bounded on the way in, at the
 *  API boundary, and here — the label is the app author's string and the
 *  sidebar is 15rem wide whatever the manifest says. */
const MAX_APP_LABEL = 40;

/** Trim an app-supplied label to something a sidebar can hold. */
function appLabel(app: InstalledApp): string {
  const name = (app.name || app.id).trim();
  return name.length > MAX_APP_LABEL ? name.slice(0, MAX_APP_LABEL) : name;
}

/** Does this build carry the route at `href`? Unmapped routes always do.
 *
 *  App routes answer to two questions, not one: is the app installed, and is
 *  its surface loaded. An app removed from `apps/` is gone whatever the surface
 *  says; an installed app still answers to the surface gate. When the installed
 *  list is not known, only the surface gate applies — the same fail-open rule
 *  as everything else here. */
/** Does this build have the shipped app with this id (its page is on the
 *  menu)? Unknown build: yes, the fail-open answer every gate here gives.
 *  The dashboard reads and polls an app's data only when this is true. */
export function appOnBuild(build: BuildSelf | null, appId: string): boolean {
  return hrefLoaded(build, `${APP_ROUTE_PREFIX}${appId}`);
}

export function hrefLoaded(build: BuildSelf | null, href: string): boolean {
  const path = href.split("?")[0];
  const apps = installedApps(build);
  if (apps && path.startsWith(APP_ROUTE_PREFIX)) {
    const app = apps.find((a) => a.route === path);
    if (!app) return false;
    const declared = app.surfaces.length ? app.surfaces : SURFACES_BY_HREF[path];
    if (!declared) return true;
    return anySurfaceLoaded(build, declared);
  }
  const surfaces = SURFACES_BY_HREF[path];
  if (!surfaces) return true;
  return anySurfaceLoaded(build, surfaces);
}

/**
 * The menu with its Apps submenu replaced by the apps this build has installed.
 *
 * Composed WITH `filterMenuByBuild`, not instead of it: this says which apps
 * exist, that says which surfaces are loaded, and a build needs both applied.
 * Call this first, then filter.
 *
 * `menuAside` stays a plain synchronous array on purpose. Making its export
 * build-aware would force it async, and "no data yet" would render as "no
 * menu". So the built-in list stays the fallback and the swap happens here at
 * render time: when the installed list is not known, the menu comes back
 * untouched and the owner sees everything.
 *
 * Non-app entries are never touched. Pure, so it can be run without a browser.
 */
export function withInstalledApps(
  items: MenuAsideItem[],
  build: BuildSelf | null,
): MenuAsideItem[] {
  const apps = installedApps(build);
  if (!apps) return items;

  const derived: MenuAsideItem[] = apps
    .filter((app) => app.route.startsWith(APP_ROUTE_PREFIX))
    .map((app) => ({
      label: appLabel(app),
      icon: appIconPath(app.icon),
      href: app.route,
    }));

  return items.map((item) => {
    if (item.label !== APPS_MENU_LABEL || !item.menu) return item;
    return { ...item, menu: derived };
  });
}


/**
 * Home tiles that show one of the first apps' data, keyed by tile id: the tile
 * shows only while one of these apps is on (in /build/self `apps`, the same
 * list the sidebar reads). A first app that is off has no tile, whatever the
 * page list says; its saved data stays. Unknown app list: fail open.
 */
export const APPS_BY_WIDGET: Record<string, string[]> = {
  schedule: ["calendar"],
  meals: ["freshtable"],
  workout: ["fitfam"],
  reminders: ["alarmflow", "calendar"],
};

/** The same for registry widgets, keyed by their `type`. */
export const APPS_BY_WIDGET_TYPE: Record<string, string[]> = {
  calendar: ["calendar"],
  alarmflow: ["alarmflow"],
};

/** Is one of these apps on? Unknown app list (null build, older backend): yes. */
function anyAppOn(build: BuildSelf | null, ids: string[] | undefined): boolean {
  if (!ids) return true;
  const apps = installedApps(build);
  if (!apps) return true;
  return ids.some((id) => apps.some((a) => a.id === id));
}

/** Does this build carry the widget with this id? Unmapped widgets always do. */
export function widgetLoaded(build: BuildSelf | null, id: string): boolean {
  // The weather card is also where the town is typed, so ANY bot with the
  // weather forecast switched on carries it, whatever its page list says.
  if (id === "weather" && build?.weather_on === true) return true;
  if (!anyAppOn(build, APPS_BY_WIDGET[id])) return false;
  const surfaces = SURFACES_BY_WIDGET[id];
  if (!surfaces) return true;
  return anySurfaceLoaded(build, surfaces);
}

/**
 * Surfaces needed by a REGISTRY widget, keyed by its `type`.
 *
 * Separate map from `SURFACES_BY_WIDGET` because the two are keyed by different
 * things and conflating them silently gates nothing. The built-in tiles have
 * stable ids the dashboard types out itself; registry widgets come back from
 * GET /dashboard/widgets carrying a backend-assigned instance `id` and a
 * `type`, so the id is unknowable here and the type is the only handle.
 *
 * The media tile is the reason this exists. BuildBot draws no media hub, and a
 * "Now playing" card on a box with no Jellyfin behind it is a promise about
 * furniture that is not in the house. Gated, not deleted: a build that DID load
 * media.audio has a real media server and real sessions to show, and the
 * renderer plus /api/media/* stay intact for it.
 */
export const SURFACES_BY_WIDGET_TYPE: Record<string, string[]> = {
  "now-playing": ["media.audio", "media.video", "media.books"],
};

/** Does this build carry a registry widget of this type? Unmapped types do. */
export function widgetTypeLoaded(build: BuildSelf | null, type: string): boolean {
  if (!anyAppOn(build, APPS_BY_WIDGET_TYPE[type])) return false;
  const surfaces = SURFACES_BY_WIDGET_TYPE[type];
  if (!surfaces) return true;
  return anySurfaceLoaded(build, surfaces);
}

/**
 * The nav this build should show: the full menu with the routes it did not
 * load removed, recursively. A submenu whose children all drop out drops out
 * with them rather than leaving an empty disclosure triangle; a submenu that
 * also has its own href keeps it.
 *
 * Pure, so it can be run against a build shape without a browser.
 */
export function filterMenuByBuild(
  items: MenuAsideItem[],
  build: BuildSelf | null,
): MenuAsideItem[] {
  const out: MenuAsideItem[] = [];
  for (const item of items) {
    if (item.href && !hrefLoaded(build, item.href)) continue;
    if (item.menu) {
      const menu = filterMenuByBuild(item.menu, build);
      if (menu.length === 0 && !item.href) continue;
      out.push({ ...item, menu });
      continue;
    }
    out.push(item);
  }
  return out;
}

/**
 * Both nav gates, in the order they have to run.
 *
 * `withInstalledApps` says which apps `apps/<id>/app.json` declares;
 * `filterMenuByBuild` says which surfaces this build loaded. Neither replaces
 * the other and a build needs both. One function so the rail, the quick search
 * and the gate-proof script cannot apply them differently — and pure, with no
 * React import, so the proof can run under plain node.
 */
export function buildMenu(
  build: BuildSelf | null,
  items: MenuAsideItem[],
): MenuAsideItem[] {
  return filterMenuByBuild(withInstalledApps(items, build), build);
}

/**
 * What to call the assistant on screen: the name the owner gave this build, or
 * the product name when they have not named it. A build called Orrery says
 * Orrery; the base says PuffPAI. Never a persona the base is not wearing.
 */
export function assistantName(build: BuildSelf | null): string {
  const name = build?.build?.name?.trim();
  return name || PRODUCT_NAME;
}

// ── The persona the owner made gets a rail entry of its own ──────────────────
//
// An owner built a space bot called Infinity, and could not find it. Both gates
// above were working exactly as written: a scaffolded persona's route lives
// under `/dashboard/apps/`, its only rail home was the "Apps" submenu, and that
// entry carries no `railGroup` (menuAside.ts) — it is search-only, absorbed by
// the home board's Apps tile. So the one thing on the box that is HERS had no
// entry anywhere in the rail, whatever her allowlist said.
//
// THE MISSING ENTRY IS THE BUG; THE GATE IS NOT. This used to be appended after
// `buildMenu` and subject to neither gate, which made the allowlist a rule with
// an exception — and a rule with an exception is not the rule this product
// sells. So the entry is added here (nothing else gives a persona a rail group)
// but it is GATED like every other page: scaffolding a persona adds its own
// surface, `persona.<id>`, to the build's allowlist (persona_scaffold.py), so
// it passes the gate by being loaded, not by being excepted.
//
// Fail-open exactly like the rest: an unknown build and an untrimmed build show
// it, because `anySurfaceLoaded` says so.
//
// The list comes from GET /persona/list (the owner's own apps, decided on disk
// by `is_user_persona`), never from a hardcoded id — `apps` is a legal persona
// id and must work until the owner renames it.

/** The surface a persona carries: its own, named after its id. The one place
 *  the dashboard spells this out; the backend spells it out in
 *  `persona_scaffold.persona_surface`, and the two must agree. */
export function personaSurface(app: OwnerApp): string {
  return `persona.${app.id}`;
}

/** Does this build load this persona? The same question, and the same
 *  fail-open answer, as every other gate here. */
export function personaLoaded(build: BuildSelf | null, app: OwnerApp): boolean {
  return anySurfaceLoaded(build, [personaSurface(app)]);
}

/** Where a persona's route lives. Read from the app record, never assembled
 *  here, so an app that declares its own route keeps it. */
export function personaHref(app: OwnerApp): string {
  return app.route || `${APP_ROUTE_PREFIX}${app.id}`;
}

/** The bot's name as the rail should show it, bounded like any app label. */
export function personaLabel(app: OwnerApp): string {
  const name = (app.name || app.id).trim();
  return name.length > MAX_APP_LABEL ? name.slice(0, MAX_APP_LABEL) : name;
}

/** The rail entries for the owner's personas THIS BUILD LOADS, in the order
 *  served. A persona whose surface is not on a trimmed build's allowlist is not
 *  here, the same way a meal planner is not. */
export function personaNavItems(
  apps: OwnerApp[] | null,
  build: BuildSelf | null = null,
): MenuAsideItem[] {
  if (!apps) return [];
  return apps
    .filter((app) => personaLoaded(build, app))
    .map((app) => ({
      label: personaLabel(app),
      icon: mdiStarFourPointsOutline,
      msIcon: "auto_awesome",
      href: personaHref(app),
      railGroup: "primary" as const,
    }));
}

/**
 * The build's menu with the personas this build LOADS added to the primary rail
 * group. Placed straight after Dashboard: the bot the owner built is the second
 * thing in the rail, not the last.
 *
 * This runs after `buildMenu` for one reason only — a persona's entry does not
 * exist in `menuAside` to be filtered, so it has to be made here. It is not
 * exempt from anything: `personaNavItems` applies the surface gate itself.
 *
 * Deduplicated against entries that already RENDER in the rail (`railGroup`
 * set). The Apps submenu's children have none, so a persona that appears there
 * is still added here — that is why this exists.
 *
 * Pure, so the gate proof can run it under plain node.
 */
export function withPersonaNav(
  items: MenuAsideItem[],
  apps: OwnerApp[] | null,
  build: BuildSelf | null = null,
): MenuAsideItem[] {
  const shown = new Set<string>();
  const walk = (list: MenuAsideItem[]) => {
    for (const item of list) {
      if (item.href && item.railGroup) shown.add(item.href.split("?")[0]);
      if (item.menu) walk(item.menu);
    }
  };
  walk(items);

  const added = personaNavItems(apps, build).filter(
    (item) => item.href && !shown.has(item.href.split("?")[0]),
  );
  if (added.length === 0) return items;

  const at = items.findIndex((item) => item.href === "/dashboard") + 1;
  return [...items.slice(0, at), ...added, ...items.slice(at)];
}

/** The prefix `persona_scaffold.build_app_manifest` puts in front of the goal
 *  it records in a scaffolded app's blurb. Stripped for display only; the
 *  blurb is still shown whole when it does not carry one. */
const GOAL_BLURB_PREFIX = "Scaffolded from your goal: ";

/** How much of a goal a card shows before it is cut at a word boundary. */
const MAX_GOAL_CHARS = 180;

/** What this bot is FOR, in the owner's own words, short enough for a card.
 *
 * The backend sends the goal as its own field; the blurb-prefix strip below is
 * only for a reply that does not carry one (an older backend, or a blurb the
 * owner rewrote by hand). */
export function personaGoal(app: OwnerApp): string {
  const sent = (app.goal || "").trim();
  const blurb = (app.blurb || "").trim();
  const goal = sent
    ? sent
    : blurb.startsWith(GOAL_BLURB_PREFIX)
      ? blurb.slice(GOAL_BLURB_PREFIX.length).trim()
      : blurb;
  if (goal.length <= MAX_GOAL_CHARS) return goal;
  return `${goal.slice(0, MAX_GOAL_CHARS).replace(/\s+\S*$/, "")}…`;
}

/** The one question every build can answer: its own list of what it can do. */
export const ALWAYS_ASK = "What can you do?";

/**
 * Up to three things worth saying to this bot, for its "Meet" card.
 *
 * They come from the bot's front page (GET /front, `try_asking`): questions
 * the backend built from the owner's goal words, this build's live cards and
 * the pages its books really hold, and checked against this build before they
 * were sent ("Show me the latest pictures", "Tell me about Tomato"). Never a
 * chore about a list: the Meet card introduces the bot, it does not hand out
 * housework. With no front page yet (still loading, or the backend is not
 * answering) the card offers the one question every build answers.
 */
export function personaPrompts(_app: OwnerApp, asked: readonly string[] = []): string[] {
  const picked: string[] = [];
  for (const raw of asked) {
    const text = (raw || "").trim();
    if (text && text.length <= 120 && !picked.includes(text)) picked.push(text);
    if (picked.length === 3) break;
  }
  return picked.length > 0 ? picked : [ALWAYS_ASK];
}
