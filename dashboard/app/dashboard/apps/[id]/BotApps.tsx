"use client";

/**
 * The bot's own page: its apps, one row per job of its build.
 *
 * Each row says the job, whether its app works now (or why not yet), the
 * app's name with a link to its project page ("Made from <repo>" when
 * Toolsmith made the app from a code project), Open (a new tab in her own
 * browser, never a frame) while it runs, and Try next app when it does not
 * work (POST /build/check/<job>: make runs the job again, the next app in
 * its order follows when one cannot start), and Check again (the same job
 * from the top, her project first, ones tried before too; the old reasons
 * stay as "Tried before").
 *
 * Below them, Knowledge: the books for its goal her Yes covered, each with
 * where it is (downloading, added, or why it did not arrive).
 *
 * Data: GET /build/status (jobs, their screen, the parts by name and the
 * knowledge lines) and
 * GET /build/apps (the apps Toolsmith runs, and whether each answers now).
 */

import { useCallback, useEffect, useState } from "react";
import { useBuildStatus } from "@/app/_lib/hooks/useBuildStatus";
import {
  checkJobAgain,
  getRunningApps,
  type RunningApp,
} from "@/app/_lib/services/buildService";
import Link from "next/link";
import { jobHref, tryingLine, type Job, type KnowledgeItem, type PartName } from "@/app/_lib/buildJobs";
import { JBM, NUNITO, Panel, SectionBar, microLabel } from "../_components/ui";
import OpenDesktopProgram from "@/app/_components/OpenDesktopProgram";
import CloudCheckBanner from "@/app/_components/Jobs/CloudCheckBanner";
import VaultWaitUnlock from "@/app/_components/Jobs/VaultWaitUnlock";

type State = { word: string; color: string; line: string };

function stateOf(job: Job, running: RunningApp[]): State {
  if (job.status === "working") {
    const known = job.screen ? running.find((a) => a.url === job.screen) : undefined;
    if (known && !known.running) {
      return { word: "Stopped", color: "var(--lemon)", line: "It is not answering right now." };
    }
    return { word: "Working", color: "var(--mint, var(--sky))", line: "" };
  }
  if (job.trying && !job.trying.waiting) {
    // A try running now always wins over the word kept for the job.
    return { word: `Building: ${job.trying.name}`, color: "var(--sky)", line: tryingLine(job) };
  }
  if (job.status === "building" || job.status === "queued") {
    return { word: "Starting", color: "var(--sky)", line: tryingLine(job) || "Toolsmith is getting it running." };
  }
  if (job.status === "waiting_yes" || job.status === "needs_setup" || job.status === "planned") {
    return {
      word: job.status === "needs_setup" ? "Needs setup" : "Waiting for your Yes",
      color: "var(--lemon)",
      line: job.reason || "Open the job to say Yes, then it starts.",
    };
  }
  return {
    word: "Not yet",
    color: "var(--text3)",
    line: job.reason || (job.route === "D" ? "Nothing found yet." : "It has not run yet."),
  };
}

const openStyle = {
  fontFamily: NUNITO,
  fontWeight: 800,
  fontSize: 14,
  color: "var(--bg)",
  background: "var(--sky)",
  borderRadius: 999,
  padding: "6px 16px",
  textDecoration: "none",
  whiteSpace: "nowrap" as const,
};

const nextStyle = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13,
  color: "var(--text)",
  background: "transparent",
  border: "1px solid var(--line)",
  borderRadius: 999,
  padding: "5px 14px",
  cursor: "pointer",
  whiteSpace: "nowrap" as const,
};

export default function BotApps({ name }: { name: string }) {
  const { status, failed, reload } = useBuildStatus();
  const [running, setRunning] = useState<RunningApp[]>([]);
  const [said, setSaid] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);

  const readRunning = useCallback(async () => {
    setRunning(await getRunningApps());
  }, []);

  useEffect(() => {
    void readRunning();
  }, [readRunning, status]);

  const tryNext = useCallback(
    async (job: Job, fromTop = false) => {
      setBusy(job.id);
      const r = await checkJobAgain(job.id, fromTop);
      setSaid((s) => ({ ...s, [job.id]: r.message || (r.ok ? "Trying it now." : "It could not be started.") }));
      setBusy(null);
      await reload();
    },
    [reload],
  );

  const parts = new Map<string, PartName>((status?.parts ?? []).map((p) => [p.ref, p]));
  const jobs = status?.jobs ?? [];
  const books = (status?.knowledge ?? []).filter((k) => k.ticked || k.status);

  return (
    <>
      <SectionBar title="Apps" />
      <CloudCheckBanner jobs={jobs} onDone={reload} />
      <VaultWaitUnlock line={status?.vaultWait} onOpened={reload} />
      <Panel style={{ padding: 0, overflow: "hidden" }}>
        {jobs.length === 0 ? (
          <p className="px-4 py-4 text-sm" style={{ fontFamily: NUNITO, color: "var(--text2)" }}>
            {failed
              ? `${name}'s jobs could not be read right now.`
              : status
                ? `${name} has no jobs yet.`
                : "Reading its jobs..."}
          </p>
        ) : (
          <table className="w-full text-left text-sm">
            <tbody className="divide-y divide-[var(--line)]">
              {jobs.map((job) => {
                const st = stateOf(job, running);
                const ref = job.appUsed?.ref || job.pick?.ref || "";
                const part = ref ? parts.get(ref) : undefined;
                // Her project (code on the job) names the app until one runs.
                const appName = job.appUsed?.name || part?.name || job.code?.[0]?.name || "";
                const open = st.word === "Working" && job.screen ? job.screen : "";
                const busyNow = st.word === "Starting" || st.word.startsWith("Building");
                const waiting = st.word === "Waiting for your Yes" || st.word === "Needs setup";
                const canRetry = st.word !== "Working" && !busyNow && !waiting && job.route !== "D";
                const allTried = /Every app found for this job was tried/.test(job.reason);
                return (
                  <tr key={job.id} data-job={job.id}>
                    <td className="px-4 py-3 align-top" style={{ width: "30%" }}>
                      <div style={{ fontFamily: NUNITO, fontWeight: 700, color: "var(--text)" }}>{job.name}</div>
                      <div style={{ ...microLabel, color: st.color, marginTop: 4 }}>{st.word}</div>
                    </td>
                    <td className="px-4 py-3 align-top">
                      {appName ? (
                        part?.url ? (
                          <a
                            href={part.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{ fontFamily: JBM, fontSize: 13, color: "var(--sky)" }}
                          >
                            {appName}
                          </a>
                        ) : (
                          <span style={{ fontFamily: JBM, fontSize: 13, color: "var(--text)" }}>{appName}</span>
                        )
                      ) : (
                        <span style={{ fontFamily: JBM, fontSize: 13, color: "var(--text3)" }}>No app yet</span>
                      )}
                      {job.appUsed?.program && <OpenDesktopProgram program={job.appUsed.program} name={appName || job.name} />}
                      {job.appUsed?.madeFrom && (
                        <p className="mt-1" style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text3)" }}>
                          Made from {job.appUsed.madeFrom}
                        </p>
                      )}
                      {(job.appUsed?.notes || []).map((note) => (
                        <p key={note} className="mt-1" style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text2)" }}>
                          {note}
                        </p>
                      ))}
                      {(st.line || said[job.id]) && (
                        <p className="mt-1" style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text2)" }}>
                          {said[job.id] || st.line}
                        </p>
                      )}
                      {(job.tried ?? []).map((t) => (
                        <p key={t.ref} className="mt-1" style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text3)" }}>
                          Tried {t.name}: {t.why || "it did not start here"}
                        </p>
                      ))}
                      {job.triedBefore?.length ? (
                        <p className="mt-1" style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text3)" }}>
                          Tried before: {job.triedBefore.map((t) => `${t.name} (${t.why || "it did not start here"})`).join("; ")}
                        </p>
                      ) : null}
                    </td>
                    <td className="px-4 py-3 text-right align-top">
                      {open && (
                        <a href={open} target="_blank" rel="noopener noreferrer" style={openStyle}>
                          Open
                        </a>
                      )}
                      {(allTried || waiting) && (
                        <Link href={jobHref(job.id)} style={nextStyle}>
                          {waiting ? "Open the job" : "Find more"}
                        </Link>
                      )}
                      {canRetry && !allTried && (
                        <button
                          type="button"
                          style={nextStyle}
                          disabled={busy === job.id}
                          onClick={() => void tryNext(job)}
                        >
                          Try next app
                        </button>
                      )}
                      {canRetry && (
                        <button
                          type="button"
                          style={{ ...nextStyle, marginLeft: 6 }}
                          disabled={busy === job.id}
                          onClick={() => void tryNext(job, true)}
                        >
                          Check again
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Panel>
      {books.length > 0 && (
        <>
          <SectionBar title="Knowledge" />
          <Panel style={{ padding: 0, overflow: "hidden" }}>
            <ul className="divide-y divide-[var(--line)]" style={{ listStyle: "none", margin: 0, padding: 0 }}>
              {books.map((k) => (
                <li key={k.id} data-knowledge={k.id} className="px-4 py-3">
                  <div style={{ fontFamily: NUNITO, fontWeight: 700, color: "var(--text)" }}>{k.title}</div>
                  <div style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text2)", marginTop: 2 }}>
                    {k.site}, {k.sizeWords}
                  </div>
                  <div style={{ ...microLabel, color: KNOWLEDGE_STATE[k.status].color, marginTop: 4 }}>
                    {k.status === "" && status?.yes.given ? "Waiting its turn" : KNOWLEDGE_STATE[k.status].word}
                  </div>
                  {k.status === "failed" && k.reason && (
                    <p className="mt-1" style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text2)" }}>
                      {k.reason}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </Panel>
        </>
      )}
    </>
  );
}

const KNOWLEDGE_STATE: Record<KnowledgeItem["status"], { word: string; color: string }> = {
  "": { word: "Waits for your yes", color: "var(--text3)" },
  downloading: { word: "Downloading", color: "var(--sky)" },
  added: { word: "Added, chat can search it", color: "var(--mint, var(--sky))" },
  failed: { word: "Not added", color: "var(--lemon)" },
};
