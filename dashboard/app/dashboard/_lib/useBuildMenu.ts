"use client";

/**
 * The nav THIS build should show.
 *
 * `menuAside` is the full catalogue of destinations the dashboard knows how to
 * draw. A build that ran the interview carries less than that: an allowlist of
 * surfaces, and an `apps/` directory holding only the apps it was given. Two
 * gates apply, and neither replaces the other —
 *
 *   withInstalledApps  : which apps `apps/<id>/app.json` declares
 *   filterMenuByBuild  : which surfaces this build loaded
 *
 * — so a space bot does not get a meal planner in its sidebar, and an app the
 * owner deleted does not linger as three links to nothing.
 *
 * BOTH GATES FAIL OPEN. While the fetch is in flight, and forever if the
 * backend is unreachable, `build` is null and the full menu comes back
 * untouched. A stranger whose backend hiccups gets the whole nav, never an
 * empty one — that rule lives in surfaceMap.ts and nothing here may add a
 * check in front of it.
 *
 * One hook so the rail (dashboard/layout.tsx) and the quick search agree; both
 * mount under the same layout, so this is one /build/self fetch per surface,
 * not one per consumer of the list.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useEffect, useMemo } from "react";
import menuAside from "./menuAside";
import { buildMenu, withPersonaNav } from "../../_lib/surfaceMap";
import { useBuildSelf } from "../../_lib/hooks/useBuildSelf";
import { usePersonaApps } from "../../_lib/hooks/usePersonaApps";
import type { BuildSelf } from "../../_lib/services/buildService";
import type { MenuAsideItem } from "../../_interfaces";
import { useBuildStatus } from "../../_lib/hooks/useBuildStatus";
import { withoutPlacedFeeds } from "../../_lib/buildJobs";
import { reportSeen, useDashLayout } from "../../_lib/hooks/useDashLayout";
import type { LayoutSection } from "../../_lib/services/layoutService";

/**
 * Her own sidebar (the layout file's `sidebar` section, keyed by href): her
 * names, her order within each rail group, pinned items at the top of the
 * primary group, hidden items out of the rail but still in quick search.
 * `rail` is every rail item with her names and order but nothing hidden,
 * for the Customize panel.
 */
export function withOwnerLayout(
  menu: MenuAsideItem[],
  sidebar: LayoutSection,
): { menu: MenuAsideItem[]; rail: MenuAsideItem[] } {
  const named = menu.map((m) =>
    m.href && sidebar.names[m.href] ? { ...m, label: sidebar.names[m.href] } : m,
  );
  const rank = (m: MenuAsideItem) => {
    const at = m.href ? sidebar.order.indexOf(m.href) : -1;
    return at >= 0 ? at : 1000;
  };
  const rail = named
    .map((m, i) => ({ m, i }))
    .filter(({ m }) => m.railGroup)
    .sort((a, b) => rank(a.m) - rank(b.m) || a.i - b.i)
    .map(({ m }) => m);
  const pinned = (m: MenuAsideItem) => (m.href ? sidebar.pinned.indexOf(m.href) : -1);
  const shown = rail
    .filter((m) => !(m.href && sidebar.hidden.includes(m.href)))
    .map((m) => (pinned(m) >= 0 ? { ...m, railGroup: "primary" as const } : m))
    .sort((a, b) => {
      const pa = pinned(a);
      const pb = pinned(b);
      return (pa >= 0 ? pa - 100 : 0) - (pb >= 0 ? pb - 100 : 0);
    });
  const off = named
    .filter((m) => !m.railGroup || (m.href && sidebar.hidden.includes(m.href)))
    .map((m) => ({ ...m, railGroup: undefined }));
  return { menu: [...shown, ...off], rail };
}

export function useBuildMenu(): {
  build: BuildSelf | null;
  menu: MenuAsideItem[];
  rail: MenuAsideItem[];
} {
  const { build } = useBuildSelf();
  // ...and the bot the owner built, which has no entry in `menuAside` to
  // filter: its route lives under the search-only Apps submenu, which is why a
  // space bot's own name was in no rail group at all. So the entry is MADE
  // here — and gated on the persona's own surface like every other page. See
  // surfaceMap.ts, "the persona the owner made gets a rail entry of its own".
  const { apps } = usePersonaApps();
  // A live card the build placed (in a job, or under "Not used") gets no rail
  // entry of its own: it is part of a job, on the job's page.
  const { status } = useBuildStatus(false);
  const { layout } = useDashLayout();
  const { menu, rail } = useMemo(
    () =>
      withOwnerLayout(
        withPersonaNav(buildMenu(build, menuAside), withoutPlacedFeeds(apps, status), build),
        layout.sidebar,
      ),
    [build, apps, status, layout.sidebar],
  );
  // What the rail shows, by name, so the brain can find a menu item she names.
  const seenKey = rail.map((m) => `${m.href}=${m.label}`).join("\n");
  useEffect(() => {
    const seen: Record<string, string> = {};
    for (const pair of seenKey.split("\n")) {
      const at = pair.indexOf("=");
      if (at > 0) seen[pair.slice(0, at)] = pair.slice(at + 1);
    }
    reportSeen("sidebar", seen);
  }, [seenKey]);
  return { build, menu, rail };
}
