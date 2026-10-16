"use client";

/**
 * The ONE setup list and the ONE "Yes, build all of this", wherever the
 * build waits for her: the wizard's last screen, the home page and every
 * job's page (a bot migrated from an older build has its list here too, not
 * only in the wizard). After the Yes: what is still open (a key, a site that
 * could not be switched on) and the team's latest lines while it builds.
 *
 * The Yes sends the list's sha, so it covers exactly the rows on screen.
 *
 * Books for the goal are one row, "Add to the knowledge base", one tickable
 * line each (a big one starts unticked and says its size). Her first apps
 * that fit the goal are one row too, "Turn on: FitFam (one of your first
 * apps)", turned on by the same Yes.
 *
 * Backend: POST /build/setup/<row>, POST /build/knowledge {id, ticked},
 * POST /build/yes {sha}.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useState, type CSSProperties } from "react";
import Link from "next/link";
import { chooseLocal, getToolsmithModel } from "../../_lib/services/toolsmithModelService";
import { doSetupRow, sayBuildYes, tickKnowledge } from "../../_lib/services/buildService";
import {
  openRows,
  type BuildStatus,
  type FirstAppLine,
  type KnowledgeItem,
  type SetupRow,
  type SetupValue,
} from "../../_lib/buildJobs";
import { SetupList, TeamLines, small } from "./BuildViews";

const NO_ANSWER = "No answer from this computer.";

const yesButton: CSSProperties = {
  border: 0,
  borderRadius: 999,
  padding: "10px 22px",
  background: "linear-gradient(140deg, var(--pink), var(--magenta))",
  color: "#fff",
  fontWeight: 800,
  fontSize: 15,
  cursor: "pointer",
};

export default function YesPanel({
  status,
  reload,
  rowsFor,
  showLines = true,
}: {
  status: BuildStatus;
  reload: () => Promise<void>;
  /** Only these rows after the Yes (a job's own); the whole list before it. */
  rowsFor?: (row: SetupRow) => boolean;
  showLines?: boolean;
}) {
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState<Record<string, string>>({});
  const [problem, setProblem] = useState("");

  const doRow = useCallback(
    async (row: SetupRow, value?: SetupValue) => {
      setBusy(row.id);
      const r = await doSetupRow(row.id, value);
      setBusy("");
      setSaid((s) => ({ ...s, [row.id]: r.status === 0 ? NO_ANSWER : r.message || (r.ok ? "" : "That did not work.") }));
      await reload();
    },
    [reload],
  );

  async function tick(item: KnowledgeItem) {
    setBusy(`k:${item.id}`);
    const r = await tickKnowledge(item.id, !item.ticked);
    setBusy("");
    if (!r.ok) setProblem(r.status === 0 ? NO_ANSWER : r.message || "That did not change.");
    await reload();
  }

  async function yes(sha: string) {
    setBusy("yes");
    setProblem("");
    const r = await sayBuildYes(sha);
    setBusy("");
    if (!r.ok) setProblem(r.status === 0 ? NO_ANSWER : r.message || "The build did not start.");
    await reload();
  }

  const given = status.yes.given;
  const rows = given ? openRows(status.setup, true).filter((r) => !rowsFor || rowsFor(r)) : status.setup;
  const open = openRows(status.setup, given);
  const books = (status.knowledge ?? []).filter((k) => k.status !== "added");
  const firstApps = status.firstApps ?? [];
  const building = status.running || status.jobs.some((j) => j.status === "building" || j.status === "queued");

  return (
    <div style={{ display: "grid", gap: 10 }}>
      {rows.length || !given ? (
        <SetupList rows={rows} jobs={status.jobs} busy={busy} said={said} yesGiven={given} bot={status.bot} onDo={(row, v) => void doRow(row, v)} />
      ) : null}
      {status.toolsmithHold ? <ToolsmithHold line={status.toolsmithHold} reload={reload} /> : null}
      {!given ? <ToolsmithModelLine /> : null}
      {!given && books.length ? (
        <KnowledgeRow items={books} busy={busy} onTick={(i) => void tick(i)} />
      ) : null}
      {!given && firstApps.length ? <FirstAppsRow items={firstApps} /> : null}
      {!given ? (
        <div>
          <button
            type="button"
            style={{ ...yesButton, opacity: busy === "yes" || !status.yes.sha ? 0.6 : 1 }}
            disabled={busy === "yes" || !status.yes.sha}
            onClick={() => void yes(status.yes.sha)}
          >
            Yes, build all of this
          </button>
          {open.length ? (
            <p style={{ ...small, marginTop: 6 }}>
              {open.length === 1 ? "One row needs" : `${open.length} rows need`} you. You can do{" "}
              {open.length === 1 ? "it" : "them"} later; the jobs that need {open.length === 1 ? "it" : "them"} wait until then.
            </p>
          ) : null}
        </div>
      ) : showLines && building ? (
        <div>
          <p style={small}>Building now. This page updates by itself.</p>
          <div style={{ marginTop: 6 }}>
            <TeamLines lines={status.lines} max={6} />
          </div>
        </div>
      ) : null}
      {problem ? <p style={{ ...small, color: "var(--lemon)" }}>{problem}</p> : null}
    </div>
  );
}

/** She picked Anthropic for Toolsmith in the wizard but its key is not saved:
 *  the build waits for one of two answers (never a silent build on this
 *  computer). The server's own line. */
function ToolsmithHold({ line, reload }: { line: string; reload: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  async function useThisComputer() {
    setBusy(true);
    const r = await chooseLocal();
    setBusy(false);
    setSaid(r.ok ? "" : r.message || "That did not work. Try again.");
    await reload();
  }
  return (
    <div role="status" style={{ display: "grid", gap: 8 }}>
      <p style={{ ...small, margin: 0, color: "var(--text)" }}>{line}</p>
      <div className="flex flex-wrap gap-2">
        <Link href="/dashboard/settings" style={{ ...yesButton, textDecoration: "none" }}>
          Open Settings
        </Link>
        <button
          type="button"
          style={{ ...yesButton, background: "transparent", color: "var(--text)", border: "1px solid var(--line)" }}
          disabled={busy}
          onClick={() => void useThisComputer()}
        >
          Use this computer
        </button>
      </div>
      {said ? <p style={{ ...small, margin: 0, color: "var(--lemon)" }}>{said}</p> : null}
    </div>
  );
}

/** One line on the setup list: where Toolsmith thinks (Settings changes it). */
function ToolsmithModelLine() {
  const [line, setLine] = useState("");
  useEffect(() => {
    let live = true;
    void getToolsmithModel().then((r) => {
      if (live && r.data) setLine(r.data.line);
    });
    return () => {
      live = false;
    };
  }, []);
  if (!line) return null;
  return (
    <p style={{ ...small, margin: 0 }}>
      {line}. <Link href="/dashboard/settings">Change it in Settings</Link>
    </p>
  );
}

/** Her first apps the Yes turns on: one plain line each. They are already on
 *  this computer, so nothing is downloaded. */
function FirstAppsRow({ items }: { items: FirstAppLine[] }) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px" }}>
      <ul style={{ listStyle: "none", margin: 0, padding: 0, display: "grid", gap: 4 }}>
        {items.map((item) => (
          <li key={item.id} style={{ fontSize: 14, fontWeight: 700, color: "var(--text)" }}>
            {item.line}
          </li>
        ))}
      </ul>
      <p style={{ ...small, marginTop: 4 }}>Already on this computer. Your yes turns {items.length === 1 ? "it" : "them"} on.</p>
    </div>
  );
}

/** The one knowledge row: each book a tickable line, its size said plainly,
 *  and what saying yes to it means (find_more's own consent lines). */
function KnowledgeRow({
  items,
  busy,
  onTick,
}: {
  items: KnowledgeItem[];
  busy: string;
  onTick: (item: KnowledgeItem) => void;
}) {
  return (
    <div style={{ border: "1px solid var(--line)", borderRadius: 10, padding: "10px 12px" }}>
      <p style={{ margin: 0, fontWeight: 800, fontSize: 14, color: "var(--text)" }}>Add to the knowledge base</p>
      <p style={{ ...small, marginTop: 2 }}>
        Books and libraries about your goal. The ticked ones download after your yes, one at a time, and chat can search
        them. Nothing leaves your computer without your consent.
      </p>
      <ul style={{ listStyle: "none", margin: "8px 0 0", padding: 0, display: "grid", gap: 6 }}>
        {items.map((item) => (
          <li key={item.id}>
            <label style={{ display: "flex", gap: 8, alignItems: "flex-start", cursor: "pointer" }}>
              <input
                type="checkbox"
                checked={item.ticked}
                disabled={busy === `k:${item.id}`}
                onChange={() => onTick(item)}
                style={{ marginTop: 3 }}
              />
              <span style={{ fontSize: 13, color: "var(--text)" }}>
                {item.title} ({item.site}, {item.sizeWords})
                {item.consent.length ? (
                  <span title={item.consent.join("\n")} style={{ ...small, display: "block" }}>
                    {item.consent
                      .slice(1)
                      .filter((l) => !/^(What it is|Download|Why):/.test(l))
                      .join(" ")}
                  </span>
                ) : null}
              </span>
            </label>
          </li>
        ))}
      </ul>
    </div>
  );
}
