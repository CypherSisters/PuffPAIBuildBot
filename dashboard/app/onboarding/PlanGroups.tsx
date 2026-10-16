"use client";

/**
 * "What it will do": the plan grouped by the jobs the
 * owner kept. One group per ticked job, heading = the job's name, holding
 * every line whose `need` is that job, whatever its section; under the lines,
 * the notes (things found that BuildBot cannot add yet, "found nothing") and,
 * for a job the search did not reach, the line saying so. The lines are drawn
 * by the caller's row renderer, so a line looks and ticks the same here as in
 * the sections below.
 *
 * The words come from the backend (wizard/proposal.py _job_groups); the
 * terminal prints the same groups (render_cli.summary_lines).
 */

import type { ReactNode } from "react";
import type { Summary, SummaryItem } from "../_lib/services/wizardService";
import { Toggle } from "./ui";

const HEADING = { color: "var(--text)", fontSize: 16, fontWeight: 800, margin: "0 0 4px" };
const JOB = { color: "var(--text)", fontSize: 15, fontWeight: 800, margin: "12px 0 2px" };
const NOTE = { color: "var(--text3)", fontSize: 13, lineHeight: 1.5, margin: "4px 0 0" };

/** Statements said under a group or a section; never a tick box. */
export function PlanNotes({ notes }: { notes: string[] | undefined }) {
  const shown = (notes ?? []).filter(Boolean);
  if (!shown.length) return null;
  return (
    <div data-testid="plan-notes">
      {shown.map((note, i) => (
        <p key={`${i}-${note}`} style={NOTE}>
          {note}
        </p>
      ))}
    </div>
  );
}

/** "Similar projects also have (tick to add):": one
 *  unticked line per feature 2 or more fitting projects have. Ticking one
 *  asks the backend to add it as a job (POST /wizard/needs `added`); the plan
 *  is then drawn again with its finds under it. */
export function SimilarGroup({
  summary,
  onAdd,
  busy,
}: {
  summary: Summary;
  onAdd?: (name: string) => void;
  busy?: boolean;
}) {
  const rows = summary.similar?.rows ?? [];
  if (!rows.length) return null;
  return (
    <div data-testid="plan-similar">
      <h3 style={JOB}>{summary.similar?.heading}</h3>
      {rows.map((similar) => (
        <Toggle
          key={similar.name}
          id={`similar-${similar.name}`}
          checked={false}
          disabled={busy || !onAdd}
          onChange={(on) => (on && onAdd ? onAdd(similar.name) : undefined)}
          title={similar.line}
        />
      ))}
    </div>
  );
}

export default function PlanGroups({
  summary,
  row,
  onAddSimilar,
  busy,
}: {
  summary: Summary;
  row: (item: SummaryItem) => ReactNode;
  onAddSimilar?: (name: string) => void;
  busy?: boolean;
}) {
  const groups = summary.jobs ?? [];
  if (!groups.length && !summary.similar?.rows?.length) return null;
  return (
    <section style={{ marginTop: 16 }} data-testid="plan-groups">
      <h2 style={HEADING}>{summary.jobs_heading || "What it will do"}</h2>
      {groups.map((group) => (
        <div key={group.id} data-testid={`plan-job-${group.id}`}>
          <h3 style={JOB}>{group.heading}</h3>
          {group.items.map((item) => row(item))}
          <PlanNotes notes={group.notes} />
          {group.not_reached ? <p style={NOTE}>{group.not_reached}</p> : null}
        </div>
      ))}
      <SimilarGroup summary={summary} onAdd={onAddSimilar} busy={busy} />
    </section>
  );
}
