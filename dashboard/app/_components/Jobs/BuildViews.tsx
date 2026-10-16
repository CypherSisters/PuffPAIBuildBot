"use client";

/**
 * The pieces every jobs screen draws: the job rows on the home page, "Not
 * used", a job's ingredients, the wizard's How lines, the ONE setup list and
 * the Yes. Words and state come in as props (no fetch here), so the proof
 * script renders these with fixture answers in plain node.
 *
 * Relative imports only, for the same reason.
 *
 * puff puff pass
 */

import { useState, type CSSProperties, type ReactNode } from "react";
import Link from "next/link";
import FromYourGitHub from "../GitHub/FromYourGitHub";
import {
  STATUS_TONE,
  howLine,
  jobHref,
  joinNames,
  jobLabel,
  type BuildLine,
  type BuildWords,
  type DroppedFind,
  type Job,
  type SetupRow,
  type SetupValue,
} from "../../_lib/buildJobs";
import { FREDOKA, JBM, NUNITO, microLabel } from "../../dashboard/apps/_components/ui";

export const small: CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 14,
  lineHeight: 1.45,
  color: "var(--text2)",
};

const rule: CSSProperties = { borderTop: "1px solid var(--line)" };

const linkStyle: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 14,
  color: "var(--sky)",
  textDecoration: "none",
  whiteSpace: "nowrap",
};

const buttonStyle: CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13,
  padding: "6px 14px",
  borderRadius: 999,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

const fieldStyle: CSSProperties = {
  flex: "1 1 180px",
  minWidth: 0,
  background: "var(--card2)",
  border: "1px solid var(--line)",
  borderRadius: 10,
  padding: "6px 10px",
  color: "var(--text)",
  fontFamily: NUNITO,
  fontSize: 14,
};

/** The status word in its colour: WORKING, NOT YET, NEEDS SETUP. */
export function StatusTag({ job, words }: { job: Job; words: BuildWords }) {
  return (
    <span
      style={{
        ...microLabel,
        letterSpacing: ".08em",
        color: `var(${STATUS_TONE[job.status]})`,
        whiteSpace: "nowrap",
      }}
    >
      {jobLabel(words, job)}
    </span>
  );
}

function findMoreHref(job: Job): string {
  return `/dashboard/add?find=${encodeURIComponent(job.name)}#find-skills`;
}

/** The home page: one line per job. */
export function JobRows({ jobs, words }: { jobs: Job[]; words: BuildWords }) {
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }} aria-label="What this bot does">
      {jobs.map((job, i) => {
        const line = job.status === "working" ? job.does : job.reason || job.does;
        return (
          <li
            key={job.id}
            className="grid items-baseline gap-x-4 gap-y-1"
            style={{
              ...(i ? rule : {}),
              padding: "11px 2px",
              gridTemplateColumns: "minmax(108px, 196px) minmax(0, 1fr) auto",
            }}
          >
            <StatusTag job={job} words={words} />
            <div className="min-w-0">
              <Link
                href={jobHref(job.id)}
                style={{
                  fontFamily: FREDOKA,
                  fontWeight: 600,
                  fontSize: 17,
                  color: "var(--text)",
                  textDecoration: "none",
                }}
              >
                {job.name}
              </Link>
              {line ? <p style={{ ...small, fontSize: 13 }}>{line}</p> : null}
            </div>
            <span className="flex gap-3">
              {job.status === "nothing_found" ? (
                <Link href={findMoreHref(job)} style={linkStyle}>
                  Find more
                </Link>
              ) : null}
              <Link href={jobHref(job.id)} style={linkStyle}>
                Open
              </Link>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Finds that did not fit any job, folded at the bottom. Remove only for
 *  the ones that are on this computer. */
export function NotUsed({
  dropped,
  removable,
  onRemove,
  busy,
  confirming,
}: {
  dropped: DroppedFind[];
  removable: (ref: string) => boolean;
  onRemove: (ref: string) => void;
  busy?: string;
  /** The one whose Remove was tapped once: it now asks "Yes, remove it". */
  confirming?: string;
}) {
  if (!dropped.length) return null;
  return (
    <details style={{ ...rule, paddingTop: 10 }}>
      <summary style={{ ...small, cursor: "pointer", fontWeight: 700 }}>
        Not used ({dropped.length})
      </summary>
      <ul style={{ margin: "6px 0 0", padding: 0, listStyle: "none" }}>
        {dropped.map((d) => (
          <li key={d.ref} className="flex flex-wrap items-baseline gap-x-3" style={{ padding: "6px 0" }}>
            <span style={{ ...small, color: "var(--text)", fontWeight: 700 }}>{d.name}</span>
            <span style={{ ...small, fontSize: 13, flex: "1 1 200px" }}>{d.why}</span>
            {removable(d.ref) ? (
              <button type="button" style={buttonStyle} disabled={busy === d.ref} onClick={() => onRemove(d.ref)}>
                {confirming === d.ref ? "Yes, remove it" : "Remove"}
              </button>
            ) : null}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** One part of a job, ready to draw. */
export interface IngredientView {
  ref: string;
  /** The thing on this computer it was matched to, if any. */
  thingId?: string;
  name: string;
  /** Its kind in plain words. */
  kind: string;
  /** "Drawbridge: OK, ..." or "" when there is no check to show. */
  check: string;
  checkTone?: "pass" | "fail" | "unknown";
  /** A switch it has (its outside service, or starting it), or none. */
  toggle?: { on: boolean; onLabel: string; offLabel: string };
  /** Its own page, when it has one. */
  href?: string;
  removable: boolean;
  /** It came from the owner's own GitHub. */
  mine?: boolean;
}

export const CHECK_WORDS: Record<"pass" | "fail" | "unknown", string> = {
  pass: "OK",
  fail: "Problem",
  unknown: "Not sure",
};

export function IngredientRows({
  items,
  onToggle,
  onRemove,
  busy,
  confirming,
}: {
  items: IngredientView[];
  onToggle: (ref: string, on: boolean) => void;
  onRemove: (ref: string) => void;
  busy?: string;
  confirming?: string;
}) {
  if (!items.length) return <p style={small}>Nothing yet.</p>;
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {items.map((it, i) => (
        <li key={it.ref} style={{ ...(i ? rule : {}), padding: "9px 0", display: "grid", gap: 3 }}>
          <div className="flex items-baseline gap-2">
            {it.href ? (
              <Link href={it.href} style={{ ...small, fontWeight: 700, color: "var(--text)", textDecoration: "none" }}>
                {it.name}
              </Link>
            ) : (
              <span style={{ ...small, fontWeight: 700, color: "var(--text)" }}>{it.name}</span>
            )}
            <span style={{ ...small, fontSize: 12, color: "var(--text3)" }}>{it.kind}</span>
            {it.mine ? <FromYourGitHub /> : null}
          </div>
          {it.check ? (
            <p
              style={{
                ...small,
                fontSize: 12,
                color: it.checkTone === "fail" ? "var(--lemon)" : "var(--text2)",
              }}
            >
              {it.check}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2" style={{ marginTop: 2 }}>
            {it.toggle ? (
              <button
                type="button"
                role="switch"
                aria-checked={it.toggle.on}
                disabled={busy === it.ref}
                onClick={() => onToggle(it.ref, !it.toggle!.on)}
                style={{
                  ...buttonStyle,
                  borderColor: it.toggle.on ? "var(--mint)" : "var(--line)",
                  color: it.toggle.on ? "var(--mint)" : "var(--text2)",
                }}
              >
                {it.toggle.on ? it.toggle.onLabel : it.toggle.offLabel}
              </button>
            ) : null}
            {it.removable ? (
              <button type="button" style={buttonStyle} disabled={busy === it.ref} onClick={() => onRemove(it.ref)}>
                {confirming === it.ref ? "Yes, remove it" : "Remove"}
              </button>
            ) : null}
          </div>
        </li>
      ))}
    </ul>
  );
}

/** The wizard handoff: each job and how BuildBot will do it. */
export function HowRows({
  jobs,
  words,
  nameOf,
  showStatus,
}: {
  jobs: Job[];
  words: BuildWords;
  nameOf: (ref: string) => string;
  showStatus: boolean;
}) {
  return (
    <ol style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {jobs.map((job, i) => (
        <li
          key={job.id}
          className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5"
          style={{ ...(i ? rule : {}), padding: "9px 0" }}
        >
          <span style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 16, color: "var(--text)" }}>{job.name}</span>
          <span style={{ ...small, flex: "1 1 260px" }}>{howLine(job, words, nameOf)}</span>
          {showStatus ? <StatusTag job={job} words={words} /> : null}
          {showStatus && job.reason && job.status !== "working" ? (
            <span style={{ ...small, fontSize: 12, flexBasis: "100%" }}>{job.reason}</span>
          ) : null}
        </li>
      ))}
    </ol>
  );
}

const KIND_LABEL: Record<SetupRow["kind"], string> = {
  program: "Program",
  package: "Package",
  key: "Key",
  setting: "Setting",
  address: "Outside address",
  switch: "Switch",
};

function TownForm({
  row,
  busy,
  onDo,
}: {
  row: SetupRow;
  busy: boolean;
  onDo: (row: SetupRow, value?: SetupValue) => void;
}) {
  const [town, setTown] = useState(row.pending || row.town || "");
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const withPosition = Boolean(row.position);
  const ready = town.trim().length >= 2 && (!withPosition || (lat.trim() && lon.trim()) || !(lat.trim() || lon.trim()));
  return (
    <form
      className="flex flex-wrap gap-2"
      style={{ flex: "1 1 100%", maxWidth: 560 }}
      onSubmit={(e) => {
        e.preventDefault();
        if (!ready) return;
        if (withPosition && lat.trim() && lon.trim()) {
          onDo(row, { town: town.trim(), latitude: lat.trim(), longitude: lon.trim() });
        } else {
          onDo(row, town.trim());
        }
      }}
    >
      <input
        aria-label="Your town"
        type="text"
        autoComplete="off"
        placeholder="Your town"
        value={town}
        maxLength={100}
        onChange={(e) => setTown(e.target.value)}
        style={fieldStyle}
      />
      {withPosition ? (
        <>
          <input
            aria-label="Latitude"
            inputMode="decimal"
            placeholder="Latitude, like 39.7"
            value={lat}
            maxLength={12}
            onChange={(e) => setLat(e.target.value)}
            style={{ ...fieldStyle, flex: "0 1 150px" }}
          />
          <input
            aria-label="Longitude"
            inputMode="decimal"
            placeholder="Longitude, like -105.0"
            value={lon}
            maxLength={12}
            onChange={(e) => setLon(e.target.value)}
            style={{ ...fieldStyle, flex: "0 1 150px" }}
          />
        </>
      ) : null}
      <button type="submit" style={buttonStyle} disabled={busy || !ready}>
        Save
      </button>
    </form>
  );
}

function SetupRowView({
  row,
  jobNames,
  busy,
  said,
  yesGiven,
  onDo,
}: {
  row: SetupRow;
  jobNames: string;
  busy: boolean;
  said: string;
  yesGiven: boolean;
  onDo: (row: SetupRow, value?: SetupValue) => void;
}) {
  const [value, setValue] = useState("");
  let control: ReactNode = null;
  const comes = <span style={{ ...small, fontSize: 12, color: "var(--text3)" }}>Comes with your Yes</span>;
  if (row.done) {
    control = <span style={{ ...microLabel, color: "var(--mint)" }}>Done</span>;
  } else if (row.by_yes) {
    // Her one Yes does it (the bot's own go-online setting).
    control = yesGiven ? <span style={{ ...small, fontSize: 12, color: "var(--lemon)" }}>Not done yet</span> : comes;
  } else if (!yesGiven && (row.kind === "address" || row.kind === "switch")) {
    // Her one Yes switches on the outside sites the list shows.
    control = comes;
  } else if (row.kind === "setting") {
    control = (
      <>
        {row.pending && !yesGiven && !said ? (
          <span style={{ ...small, fontSize: 12, color: "var(--text3)", flexBasis: "100%" }}>
            Kept: {row.pending}. It is looked up right after your Yes.
          </span>
        ) : null}
        <TownForm row={row} busy={busy} onDo={onDo} />
      </>
    );
  } else if (row.kind === "key") {
    control = (
      <form
        className="flex flex-wrap gap-2"
        style={{ flex: "1 1 100%", maxWidth: 560 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (value.trim()) onDo(row, value.trim());
          setValue("");
        }}
      >
        <input
          aria-label={row.label}
          type="password"
          autoComplete="off"
          placeholder="Paste the key"
          value={value}
          maxLength={4000}
          onChange={(e) => setValue(e.target.value)}
          style={fieldStyle}
        />
        <button type="submit" style={buttonStyle} disabled={busy || !value.trim()}>
          Keep in my vault
        </button>
      </form>
    );
  } else if (row.kind === "address") {
    control = (
      <button type="button" style={buttonStyle} disabled={busy} onClick={() => onDo(row)}>
        Allow
      </button>
    );
  } else if (row.kind === "switch") {
    control = (
      <button type="button" role="switch" aria-checked={false} style={buttonStyle} disabled={busy} onClick={() => onDo(row)}>
        Turn on
      </button>
    );
  } else {
    control = comes;
  }
  const note = said || row.said || "";
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-1.5" style={{ padding: "10px 0" }}>
      <span style={{ ...microLabel, letterSpacing: ".08em", minWidth: 104 }}>{KIND_LABEL[row.kind]}</span>
      <div className="min-w-0" style={{ flex: "1 1 240px" }}>
        <div style={{ ...small, color: "var(--text)", fontWeight: 700 }}>{row.label}</div>
        {row.detail ? <p style={{ ...small, fontSize: 13 }}>{row.detail}</p> : null}
        {jobNames ? <p style={{ ...small, fontSize: 12, color: "var(--text3)" }}>For: {jobNames}</p> : null}
      </div>
      {control}
      {note ? <p style={{ ...small, fontSize: 13, flexBasis: "100%" }}>{note}</p> : null}
    </li>
  );
}

/** The ONE setup list: programs, packages, keys, settings, outside addresses
 *  and switches, each with its own control. */
/** A site row's own name: "NASA EPIC Earth photos" from "Turn on NASA EPIC
 *  Earth photos". */
function siteName(row: SetupRow): string {
  return row.label.replace(/^(Turn on|Connect to|Allow)\s+/i, "");
}

/** Rows her one Yes switches on (outside sites, BuildBot's own look, the
 *  bot going online): shown as ONE row before the Yes. */
export function isSiteRow(row: SetupRow): boolean {
  return row.kind === "switch" || row.kind === "address" || Boolean(row.by_yes);
}

/** "Let StargazerBot go online and reach 8 websites", with "Show which". Each
 *  site still gets its own ticket under the Yes (the backend switches them
 *  one by one). */
function SiteGroup({ rows, jobNames, bot }: { rows: SetupRow[]; jobNames: (r: SetupRow) => string; bot: string }) {
  // The bot's own go-online setting is the one setting row in the group;
  // every other row is a site (its own switch). BuildBot's look is not a site.
  const goOnline = (r: SetupRow) => r.kind === "setting" && Boolean(r.by_yes);
  const online = rows.some(goOnline);
  const ownLook = (r: SetupRow) => /^BuildBot /.test(r.detail) && !r.hosts?.length;
  const n = rows.filter((r) => !goOnline(r) && !ownLook(r)).length;
  const label = `Let ${bot || "your bot"} ${online ? "go online and " : ""}reach ${n} website${n === 1 ? "" : "s"}`;
  return (
    <li className="flex flex-wrap items-start gap-x-3 gap-y-1.5" style={{ padding: "10px 0" }}>
      <span style={{ ...microLabel, letterSpacing: ".08em", minWidth: 104 }}>Websites</span>
      <div className="min-w-0" style={{ flex: "1 1 240px" }}>
        <div style={{ ...small, color: "var(--text)", fontWeight: 700 }}>{label}</div>
        <p style={{ ...small, fontSize: 13 }}>
          Your Yes switches each one on, one by one. You can switch any of them off later on What leaves your computer.
        </p>
        <details>
          <summary style={{ ...small, fontSize: 13, cursor: "pointer", color: "var(--sky)", fontWeight: 700 }}>Show which</summary>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {rows.map((r) => (
              <li key={r.id} style={{ ...small, fontSize: 13 }}>
                <span style={{ color: "var(--text)", fontWeight: 700 }}>{goOnline(r) ? "Go online" : siteName(r)}</span>
                {r.detail ? `: ${r.detail}` : ""}
                {jobNames(r) ? ` For ${jobNames(r)}.` : ""}
                {r.done ? " (on already)" : ""}
              </li>
            ))}
          </ul>
        </details>
      </div>
      <span style={{ ...small, fontSize: 12, color: "var(--text3)" }}>Comes with your Yes</span>
    </li>
  );
}

export function SetupList({
  rows,
  jobs,
  busy,
  said,
  yesGiven = false,
  bot = "",
  onDo,
}: {
  rows: SetupRow[];
  jobs: Job[];
  busy: string;
  said: Record<string, string>;
  /** After her Yes, a site it could not switch on gets its own button. */
  yesGiven?: boolean;
  /** The bot's name, for the one websites row. */
  bot?: string;
  onDo: (row: SetupRow, value?: SetupValue) => void;
}) {
  if (!rows.length) return <p style={small}>Nothing to set up.</p>;
  const names = new Map(jobs.map((j) => [j.id, j.name]));
  const jobNames = (row: SetupRow) => joinNames(row.job_ids.map((id) => names.get(id) || "").filter(Boolean));
  // Before the Yes: what needs her first (town, keys), then programs, then
  // ONE row for every site the Yes switches on. After it: each open row.
  const sites = yesGiven ? [] : rows.filter(isSiteRow);
  const own = yesGiven ? rows : rows.filter((r) => !isSiteRow(r));
  const first = own.filter((r) => r.kind === "setting" || r.kind === "key");
  const rest = own.filter((r) => !(r.kind === "setting" || r.kind === "key"));
  const shown = [...first, ...rest];
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none" }} aria-label="Setup list">
      {shown.map((row, i) => (
        <div key={row.id} style={i ? rule : undefined}>
          <SetupRowView
            row={row}
            jobNames={jobNames(row)}
            busy={busy === row.id}
            said={said[row.id] || ""}
            yesGiven={yesGiven}
            onDo={onDo}
          />
        </div>
      ))}
      {sites.length ? (
        <div style={shown.length ? rule : undefined}>
          <SiteGroup rows={sites} jobNames={jobNames} bot={bot} />
        </div>
      ) : null}
    </ul>
  );
}

/** The team's latest lines (Majordomo, Toolsmith, Drawbridge). */
export function TeamLines({ lines, max = 8 }: { lines: BuildLine[]; max?: number }) {
  if (!lines.length) return null;
  return (
    <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gap: 3 }}>
      {lines.slice(-max).map((l, i) => (
        <li key={`${i}-${l.text}`} className="flex flex-wrap items-baseline gap-2" style={{ ...small, fontSize: 13 }}>
          <span
            style={{
              fontFamily: JBM,
              fontWeight: 700,
              fontSize: 11,
              letterSpacing: ".08em",
              textTransform: "uppercase",
              color: "var(--text3)",
              minWidth: 92,
            }}
          >
            {l.agent}
          </span>
          <span style={{ color: "var(--text)", flex: "1 1 240px" }}>{l.text}</span>
        </li>
      ))}
    </ul>
  );
}
