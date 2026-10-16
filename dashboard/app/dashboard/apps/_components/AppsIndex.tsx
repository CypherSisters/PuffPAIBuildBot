"use client";

/**
 * The apps this build carries.
 *
 * A client component so it can ask GET /build/self which apps were loaded — the
 * page around it stays a server component and keeps the page title. The list is
 * still derived from the sidebar's own menu, so this page and the nav cannot
 * disagree about what exists.
 *
 * Drawn in the shell's own design language (./ui), not the admin template it
 * arrived in. The DATA contract is unchanged — `listApps` and the surface gate
 * behind it are BuildBot's.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import React, { useEffect, useState } from "react";
import Link from "next/link";
import { mdiChevronRight, mdiPlus } from "@mdi/js";
import Icon from "../../../_components/Icon";
import { useBuildSelf } from "../../../_lib/hooks/useBuildSelf";
import { usePersonaApps } from "../../../_lib/hooks/usePersonaApps";
import { getFront, type FrontApp } from "../../../_lib/services/frontService";
import { appsToList } from "../_lib/appsIndex";
import AppLink from "../_lib/AppLink";
import { AppHeader, AppPage, FREDOKA, NUNITO, Panel, microLabel } from "./ui";

export default function AppsIndex() {
  // No build data, or an untrimmed build, lists every app — the gate in
  // `listApps` fails open, including while this fetch is still in flight. So
  // there is no spinner here on purpose: the first paint is already correct.
  const { build } = useBuildSelf();
  // Which owner-made app has nothing in it (left out), and what Toolsmith
  // added (the Mood board has no page of its own and is listed here too).
  const { apps: personas } = usePersonaApps();
  const [added, setAdded] = useState<FrontApp[] | null>(null);
  useEffect(() => {
    let alive = true;
    getFront()
      .then((front) => {
        if (alive) setAdded(front.apps ?? []);
      })
      .catch(() => {
        if (alive) setAdded(null);
      });
    return () => {
      alive = false;
    };
  }, []);
  const apps = appsToList(build, personas, added);

  return (
    <AppPage>
      <AppHeader
        title="Apps"
        lede="Each one keeps its own data on this computer. Some come with example entries of their own; the app's page says so and can clear them."
      >
        <span style={microLabel}>
          {apps.length} {apps.length === 1 ? "app" : "apps"} on this build
        </span>
      </AppHeader>

      {apps.length === 0 && (
        <Panel>
          <p
            style={{
              fontFamily: NUNITO,
              fontSize: 14,
              lineHeight: 1.5,
              color: "var(--text2)",
              margin: 0,
            }}
          >
            This build carries none of the built-in apps. They load with the
            surfaces they need, which this build was not set up for.{" "}
            <Link
              href="/onboarding?rerun=1"
              className="hover:underline"
              style={{ color: "var(--sky)" }}
            >
              Change what it carries
            </Link>
            .
          </p>
        </Panel>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {/* First tile, before the build's own apps: the answer to "there is
            nothing here for what I want". An owner's app is a folder with a
            manifest in it, and this is the form that writes one. */}
        <Link href="/dashboard/apps/new" className="block">
          <Panel
            className="h-full"
            hoverable
            style={{ borderStyle: "dashed", borderWidth: 1 }}
          >
            <div className="flex items-start gap-3">
              <Icon
                path={mdiPlus}
                size="22"
                w="w-6"
                h="h-6"
                className="mt-0.5 shrink-0"
                style={{ color: "var(--pink)" }}
              />
              <div className="min-w-0 flex-1">
                <p
                  style={{
                    margin: 0,
                    fontFamily: FREDOKA,
                    fontWeight: 600,
                    fontSize: 18,
                    lineHeight: 1.15,
                    color: "var(--text)",
                  }}
                >
                  New
                </p>
                <p
                  style={{
                    margin: "5px 0 0",
                    fontFamily: NUNITO,
                    fontWeight: 500,
                    fontSize: 14,
                    lineHeight: 1.4,
                    color: "var(--text2)",
                  }}
                >
                  Build a persona: a name, one sentence, and the lists you want
                  kept. It gets its own page and its own words.
                </p>
              </div>
            </div>
          </Panel>
        </Link>

        {apps.map((app) => (
          // an app's own page is always a full page load (see AppLink)
          <AppLink key={app.id} href={app.href} className="block">
            <Panel className="h-full" hoverable>
              <div className="flex items-start gap-3">
                {app.icon && (
                  <Icon
                    path={app.icon}
                    size="22"
                    w="w-6"
                    h="h-6"
                    className="mt-0.5 shrink-0"
                    style={{ color: "var(--sky)" }}
                  />
                )}
                <div className="min-w-0 flex-1">
                  <p
                    style={{
                      margin: 0,
                      fontFamily: FREDOKA,
                      fontWeight: 600,
                      fontSize: 18,
                      lineHeight: 1.15,
                      color: "var(--text)",
                    }}
                  >
                    {app.label}
                  </p>
                  {app.description && (
                    <p
                      style={{
                        margin: "5px 0 0",
                        fontFamily: NUNITO,
                        fontWeight: 500,
                        fontSize: 14,
                        lineHeight: 1.4,
                        color: "var(--text2)",
                      }}
                    >
                      {app.description}
                    </p>
                  )}
                </div>
                <Icon
                  path={mdiChevronRight}
                  size="18"
                  w="w-5"
                  h="h-5"
                  className="mt-1 shrink-0"
                  style={{ color: "var(--text3)" }}
                />
              </div>
            </Panel>
          </AppLink>
        ))}
      </div>
    </AppPage>
  );
}
