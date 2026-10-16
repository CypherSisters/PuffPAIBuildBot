"use client";

/**
 * "Your first apps" on the Apps page: the apps BuildBot ships with, each with
 * its one line and a Turn on / Turn off button. They start off on a new bot.
 * Turning one on is local only (POST /apps/first/turn, a paired device).
 * The page loads the list (GET /apps/first) and hands it in, so its own
 * "no apps yet" line can say what this browser can do.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import React, { useState } from "react";
import {
  FIRST_APPS_LINE,
  FIRST_APPS_TITLE,
  turnFirstApp,
  turnWords,
  type FirstApp,
  type FirstAppsAnswer,
} from "../../../_lib/services/firstAppsService";
import { FREDOKA, NUNITO, Panel, Pill, SectionBar } from "./ui";

export default function FirstApps({
  answer,
  reload,
}: {
  answer: FirstAppsAnswer;
  reload: () => Promise<void>;
}) {
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState("");

  async function turn(app: FirstApp) {
    setBusy(app.id);
    const r = await turnFirstApp(app.id, !app.on);
    setBusy("");
    setSaid(r.ok ? r.line : r.line || "That did not change.");
    await reload();
  }

  // No answer, not paired (the page's own line says so), or none on disk.
  const apps = answer && !answer.unpaired ? answer.apps : [];
  if (apps.length === 0) return null;

  return (
    <section style={{ display: "grid", gap: 12, marginTop: 8 }}>
      <SectionBar title={FIRST_APPS_TITLE} />
      <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 14, lineHeight: 1.5, color: "var(--text2)" }}>
        {FIRST_APPS_LINE}
      </p>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {apps.map((app) => (
          <Panel key={app.id} className="h-full">
            <div className="flex items-start gap-3">
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
                  {app.name}
                </p>
                {app.blurb ? (
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
                    {app.blurb}
                  </p>
                ) : null}
              </div>
              <Pill
                tone={app.on ? "quiet" : "accent"}
                disabled={busy === app.id}
                onClick={() => void turn(app)}
              >
                {turnWords(app)}
              </Pill>
            </div>
          </Panel>
        ))}
      </div>
      {said ? (
        <p role="status" style={{ margin: 0, fontFamily: NUNITO, fontSize: 13, color: "var(--text2)" }}>
          {said}
        </p>
      ) : null}
    </section>
  );
}
