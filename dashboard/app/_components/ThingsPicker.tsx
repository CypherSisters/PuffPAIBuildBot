"use client";

/**
 * The list of things found for her bot, grouped by her jobs and then by type,
 * with nothing ticked except at most two small free books that clearly fit
 * (the backend marks them ``ticked``). She taps what she wants; "Add what I picked" first shows,
 * for each one, what it is, where it comes from, what it runs and the shelf's
 * own check (the backend's words), and only "Yes, add these" installs them.
 * The install lines appear as they happen.
 *
 * Used by the wizard's "Finding things for your bot" screen and the Add page.
 */

import { useState } from "react";
import {
  followJob,
  previewThing,
  type Find,
  type FindResult,
  type InstallResult,
  type JobView,
  type Preview,
} from "@/app/_lib/services/thingsService";
import { Notice, PuffButton, Toggle } from "@/app/onboarding/ui";

const JOB_HEAD = { color: "var(--text)", fontSize: 17, fontWeight: 800, margin: "18px 0 2px" };
const TYPE_HEAD = {
  color: "var(--text3)",
  fontSize: 12,
  fontWeight: 800,
  letterSpacing: 0.6,
  textTransform: "uppercase" as const,
  margin: "12px 0 0",
};
const QUIET = { color: "var(--text3)", fontSize: 13, lineHeight: 1.5, margin: "4px 0 0" };
const LINE = { color: "var(--text2)", fontSize: 14, lineHeight: 1.6, margin: 0 };

function facts(f: Find): string {
  return [f.by ? `by ${f.by}` : "", `on ${f.site_name}`, f.popularity_words, f.changed_words,
    f.licence_words, f.size_words, f.verdict_words]
    .filter(Boolean)
    .join(", ");
}

function FindRow({ f, picked, onToggle }: { f: Find; picked: boolean; onToggle: () => void }) {
  const canPick = f.can !== "look";
  return (
    <Toggle
      id={`find-${f.id}`}
      checked={picked}
      disabled={!canPick}
      onChange={onToggle}
      title={`${f.name} (${f.type_words})`}
      description={
        <>
          {facts(f)}.{f.what ? ` ${f.what}` : ""}
          <br />
          <span style={{ color: "var(--text3)" }}>
            {f.can_words}
            {f.can_trade ? " This one can place trades with real money." : ""}
          </span>
          {f.url ? (
            <>
              {" "}
              <a href={f.url} target="_blank" rel="noreferrer noopener" style={{ color: "var(--sky)" }}>
                Where it comes from
              </a>
            </>
          ) : null}
        </>
      }
    />
  );
}

export function FoundList({
  result,
  picked,
  onToggle,
}: {
  result: FindResult;
  picked: Set<string>;
  onToggle: (id: string) => void;
}) {
  return (
    <div>
      {result.jobs_line ? <p style={QUIET}>{result.jobs_line}</p> : null}
      {result.jobs.map((job) => (
        <section key={job.id}>
          <h2 style={JOB_HEAD}>{job.name}</h2>
          {job.types.map((t) => (
            <div key={t.type}>
              <p style={TYPE_HEAD}>{t.type_words}</p>
              {t.finds.map((f) => (
                <FindRow key={f.id} f={f} picked={picked.has(f.id)} onToggle={() => onToggle(f.id)} />
              ))}
            </div>
          ))}
          {job.notes.map((n) => (
            <p key={n} style={QUIET}>
              {n}
            </p>
          ))}
        </section>
      ))}
      {result.notes.map((n) => (
        <p key={n} style={QUIET}>
          {n}
        </p>
      ))}
    </div>
  );
}

type Stage = "pick" | "ask" | "installing" | "done";

export default function ThingsPicker({
  result,
  startInstall,
  onFinished,
  doneLabel = "Next",
}: {
  result: FindResult;
  /** Starts installing these ids; answers the install job's id, or "" when refused. */
  startInstall: (ids: string[]) => Promise<string>;
  onFinished: () => void;
  doneLabel?: string;
}) {
  const [picked, setPicked] = useState<Set<string>>(
    () =>
      new Set(
        result.jobs.flatMap((j) => j.types.flatMap((t) => t.finds.filter((f) => f.ticked).map((f) => f.id))),
      ),
  );
  const [stage, setStage] = useState<Stage>("pick");
  const [previews, setPreviews] = useState<Preview[]>([]);
  const [lines, setLines] = useState<string[]>([]);
  const [problem, setProblem] = useState("");
  const [busy, setBusy] = useState(false);

  const toggle = (id: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  async function ask() {
    setBusy(true);
    setProblem("");
    const replies = await Promise.all(Array.from(picked).map((id) => previewThing(id)));
    setBusy(false);
    if (replies.some((r) => r.status === 0)) {
      setProblem("No answer from this computer. Nothing was added.");
      return;
    }
    setPreviews(replies.map((r) => r.data));
    setStage("ask");
  }

  async function install() {
    const ids = previews.filter((p) => p.ok && !p.refuse).map((p) => p.id);
    setBusy(true);
    setProblem("");
    setStage("installing");
    const job = await startInstall(ids);
    if (!job) {
      setBusy(false);
      setProblem("That was not accepted. Nothing was added.");
      setStage("ask");
      return;
    }
    const last = await followJob<InstallResult>(job, (view: JobView<InstallResult>) =>
      setLines(view.lines),
    );
    setBusy(false);
    if (last?.error) setProblem(last.error);
    setStage("done");
  }

  if (stage === "ask") {
    return (
      <div>
        <p style={LINE}>Here is what you picked. Read it, then say yes.</p>
        {previews.map((p) => (
          <Notice key={p.id || p.line} tone={p.refuse ? "warn" : "info"}>
            {(p.lines || [p.line || ""]).map((l) => (
              <span key={l} style={{ display: "block" }}>
                {l}
              </span>
            ))}
            {p.refuse ? <strong>BuildBot will not install this one.</strong> : null}
          </Notice>
        ))}
        {problem ? <p role="alert" style={{ ...LINE, color: "var(--puff-red)" }}>{problem}</p> : null}
        <div className="flex gap-2" style={{ marginTop: 14 }}>
          <PuffButton kind="primary" label="Yes, add these" disabled={busy} onClick={() => void install()} />
          <PuffButton label="Back" disabled={busy} onClick={() => setStage("pick")} />
        </div>
      </div>
    );
  }

  if (stage === "installing" || stage === "done") {
    return (
      <div>
        <div role="status" aria-live="polite">
          {lines.map((l, i) => (
            <p key={`${i}-${l}`} style={LINE}>
              {l}
            </p>
          ))}
          {stage === "installing" && lines.length === 0 ? <p style={LINE}>Starting...</p> : null}
        </div>
        {problem ? <p role="alert" style={{ ...LINE, color: "var(--puff-red)" }}>{problem}</p> : null}
        {stage === "done" ? (
          <div style={{ marginTop: 14 }}>
            <PuffButton kind="primary" label={doneLabel} onClick={onFinished} />
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div>
      <FoundList result={result} picked={picked} onToggle={toggle} />
      {problem ? <p role="alert" style={{ ...LINE, color: "var(--puff-red)" }}>{problem}</p> : null}
      <div className="flex gap-2" style={{ marginTop: 16 }}>
        <PuffButton
          kind="primary"
          label={picked.size ? `Add what I picked (${picked.size})` : "Add what I picked"}
          disabled={busy || picked.size === 0}
          onClick={() => void ask()}
        />
        <PuffButton label={picked.size ? "Skip these" : doneLabel} disabled={busy} onClick={onFinished} />
      </div>
    </div>
  );
}
