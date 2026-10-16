"use client";

/**
 * The wizard's last screen, for a bot with jobs: its title (not "built"
 * before her Yes: "<Bot> is ready to build", then "Building...", then
 * "Built"), each job and how BuildBot will do it (the answer's route words
 * and its pick), the ONE setup list, one button "Yes, build all of this",
 * then the build's progress.
 *
 * The Yes covers the whole list she sees: programs, packages and the
 * outside sites it shows are done by it (each site with its own ticket on
 * the backend). Only a key or a setting needs her own input; those rows stay
 * on the list (and on each job's page) and their jobs wait at "Needs setup"
 * until she does them. The Yes sends the list's sha, so it covers exactly
 * what was on screen. Nothing on the list is put in before the Yes.
 *
 * Renders `heading(title)`, `intro`, then `fallback` when the build has no
 * jobs (an older backend).
 *
 * Backend: GET /build/status (part names come with it), POST
 * /build/setup/<row>, POST /build/yes {sha}.
 *
 * puff puff pass
 */

import { type ReactNode } from "react";
import { useBuildStatus } from "../_lib/hooks/useBuildStatus";
import { handoffTitle, refWords } from "../_lib/buildJobs";
import { HowRows, TeamLines } from "../_components/Jobs/BuildViews";
import YesPanel from "../_components/Jobs/YesPanel";
import VaultWaitUnlock from "../_components/Jobs/VaultWaitUnlock";

const H2 = { color: "var(--text)", fontSize: 16, fontWeight: 800, margin: "18px 0 4px" };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "6px 0 0" };

export default function BuildHandoff({
  title,
  heading,
  intro,
  note,
  fallback,
}: {
  /** The card's own title, for a bot with no jobs. */
  title: string;
  heading: (text: string) => ReactNode;
  /** The card's own lines, shown only for a bot with no jobs. */
  intro?: ReactNode;
  /** One line for a bot with jobs (how to open it next time): the job list
   *  and the setup list are the summary. */
  note?: string;
  fallback: ReactNode;
}) {
  const { status, reload } = useBuildStatus();

  if (status && !status.ready) {
    return (
      <>
        {heading(title)}
        {intro}
        <p style={HELP}>{status.line || "Your bot is getting ready."}</p>
      </>
    );
  }
  if (!status || status.jobs.length === 0) {
    return (
      <>
        {heading(title)}
        {intro}
        {fallback}
      </>
    );
  }

  const names = new Map(status.parts.map((p) => [p.ref, p.name]));
  const nameOf = (ref: string) => names.get(ref) || refWords(ref);
  const given = status.yes.given;
  const building = status.running || status.jobs.some((j) => j.status === "building" || j.status === "queued");

  return (
    <>
      {heading(handoffTitle(status))}
      {note ? <p style={HELP}>{note}</p> : null}
      <section>
        <h2 style={H2}>What it will do</h2>
        <HowRows jobs={status.jobs} words={status.words} nameOf={nameOf} showStatus={given} />

        <h2 style={H2}>Set up</h2>
        <YesPanel status={status} reload={reload} showLines={false} />
        {given ? (
          <div style={{ marginTop: 14 }}>
            <div style={{ marginBottom: 10 }}>
              <VaultWaitUnlock line={status.vaultWait} onOpened={reload} />
            </div>
            <p style={HELP}>
              {building
                ? "Building now. This page updates by itself."
                : "Built. Each job's page shows how it is doing."}
            </p>
            <div style={{ marginTop: 8 }}>
              <TeamLines lines={status.lines} max={6} />
            </div>
          </div>
        ) : null}
      </section>
    </>
  );
}
