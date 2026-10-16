"use client";

/**
 * /dashboard/jobs/<id>: one job the bot does, on one page.
 *
 * Main area: the app's own screen in a frame (an app on this computer, or the
 * app BuildBot put together for this job, on 127.0.0.1 only; the same frame
 * rules as a thing's page, see middleware.ts assembledFrameSrc), or a way to
 * its own page when it has no screen. Side: what it is made of (each part's
 * name, kind, Drawbridge's check, its switch and Remove), "Ask about this"
 * filled in with the job's own sample question (sent to the chat), and "Check
 * again". Setup rows that hold this job back are listed above the screen.
 *
 * Backend: GET /build/status and /build/job/<id> (src/puffpai/build/). Every
 * status and route word comes from the answer's `words`.
 *
 * puff puff pass
 */

import OpenDesktopProgram from "@/app/_components/OpenDesktopProgram";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { AppPage, FREDOKA, NUNITO, Panel, Pill, microLabel } from "@/app/dashboard/apps/_components/ui";
import { useBuildStatus } from "@/app/_lib/hooks/useBuildStatus";
import { addProjectLink, checkJobAgain, getBuildJob, type HerOrder } from "@/app/_lib/services/buildService";
import { getThing, hostAction, listThings, removeThing, type Thing } from "@/app/_lib/services/myThingsService";
import { setFeedAllowed } from "@/app/_lib/services/feedService";
import { stashChatQuery } from "@/app/_lib/chatHandoff";
import { applyFix } from "@/app/_lib/services/chatService";
import type { FixCardData } from "@/app/_interfaces/api";
import { currentMode } from "@/app/_lib/looks";
import { howLine, openRows, refWords, thingIdOf, tryingLine, type Job } from "@/app/_lib/buildJobs";
import { IngredientRows, StatusTag, small } from "@/app/_components/Jobs/BuildViews";
import YesPanel from "@/app/_components/Jobs/YesPanel";
import VaultWaitUnlock from "@/app/_components/Jobs/VaultWaitUnlock";
import {
  feedAppId,
  loadParts,
  partViews,
  screenCandidates,
  screenFor,
  type PartInfo,
  type Screen,
} from "@/app/_components/Jobs/jobParts";

const NO_ANSWER = "No answer from this computer.";

function JobScreen({ job, screen, onStart }: { job: Job; screen: Screen | null | undefined; onStart: () => void }) {
  const frameRef = useRef<HTMLIFrameElement>(null);
  if (screen === undefined) {
    return <p style={small}>Opening...</p>;
  }
  if (screen && screen.kind === "frame") {
    const src = `${screen.url}${screen.url.includes("?") ? "&" : "?"}theme=${currentMode()}`;
    return (
      <iframe
        ref={frameRef}
        src={src}
        title={screen.name}
        sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-popups-to-escape-sandbox"
        allow="autoplay; fullscreen; picture-in-picture"
        referrerPolicy="no-referrer"
        style={{
          width: "100%",
          height: "min(76vh, 900px)",
          border: "1px solid var(--line)",
          borderRadius: 16,
          background: "var(--card2)",
        }}
      />
    );
  }
  const box: React.CSSProperties = {
    minHeight: 200,
    display: "grid",
    placeContent: "center",
    gap: 10,
    textAlign: "center",
    border: "1px dashed var(--line)",
    borderRadius: 16,
    padding: 24,
  };
  if (screen && screen.kind === "window") {
    return (
      <div style={box}>
        <p style={small}>
          {screen.name} is running on this computer. It opens in your browser, the way it is built. Ask about
          it on the right: your bot can look at its page.
        </p>
        <div>
          <a
            href={screen.url}
            target="_blank"
            rel="noopener noreferrer"
            style={{
              ...small,
              display: "inline-block",
              fontWeight: 800,
              fontSize: 16,
              color: "var(--bg)",
              background: "var(--sky)",
              borderRadius: 999,
              padding: "10px 22px",
              textDecoration: "none",
            }}
          >
            Open {screen.name}
          </a>
        </div>
        <p style={{ ...small, color: "var(--text3)" }}>
          {screen.url}
          {screen.thing ? (
            <>
              {" "}
              <Link href={`/dashboard/things/${screen.thing}`} style={{ color: "var(--sky)" }}>
                Start, stop and logs
              </Link>
            </>
          ) : null}
        </p>
      </div>
    );
  }
  if (screen && screen.kind === "stopped") {
    return (
      <div style={box}>
        <p style={small}>{screen.name} is stopped.</p>
        <div>
          <Pill tone="accent" onClick={onStart}>
            Start it
          </Pill>
        </div>
      </div>
    );
  }
  if (screen && screen.kind === "link") {
    return (
      <div style={box}>
        <p style={small}>{screen.name} has its own page.</p>
        <Link href={screen.href} style={{ ...small, fontWeight: 700, color: "var(--sky)" }}>
          Open {screen.name}
        </Link>
      </div>
    );
  }
  return (
    <div style={box}>
      <p style={small}>
        {job.status === "working"
          ? "This job has no screen of its own. Ask about it on the right."
          : job.reason || "Nothing to show here yet."}
      </p>
      {job.status === "nothing_found" ? (
        <Link
          href={`/dashboard/add?find=${encodeURIComponent(job.name)}#find-skills`}
          style={{ ...small, fontWeight: 700, color: "var(--sky)" }}
        >
          Find more
        </Link>
      ) : null}
    </div>
  );
}

export default function JobPage() {
  const params = useParams();
  const router = useRouter();
  const id = String((params as { id?: string })?.id || "");
  const { status, failed, reload } = useBuildStatus();
  const [detail, setDetail] = useState<Job | null>(null);
  const [things, setThings] = useState<Thing[] | null>(null);
  const [parts, setParts] = useState<Record<string, PartInfo>>({});
  const [screen, setScreen] = useState<Screen | null | undefined>(undefined);
  const [ask, setAsk] = useState<string | null>(null);
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState("");
  const [confirming, setConfirming] = useState("");
  const [link, setLink] = useState("");
  const lastOrder = useRef<HerOrder>("first");
  // The switch reading a pasted address needs (GitHub access), offered when it is off.
  const [linkFix, setLinkFix] = useState<FixCardData | null>(null);

  const job = detail || status?.jobs.find((j) => j.id === id) || null;

  const loadDetail = useCallback(async () => {
    const r = await getBuildJob(id);
    if (r.data) setDetail(r.data);
  }, [id]);

  const loadThings = useCallback(async () => {
    const r = await listThings();
    setThings(r.ok && Array.isArray(r.data) ? r.data : []);
  }, []);

  useEffect(() => {
    void loadDetail();
    void loadThings();
  }, [loadDetail, loadThings]);

  // The job from the status answer is newer once the status polls again;
  // the part words only the job's own answer carries are kept.
  useEffect(() => {
    const fresh = status?.jobs.find((j) => j.id === id);
    if (!fresh) return;
    setDetail((d) => {
      if (!d) return fresh;
      const known = new Map(d.ingredients.map((i) => [i.ref, i]));
      return {
        ...fresh,
        screen: fresh.screen || d.screen,
        screenFramable: fresh.screen ? fresh.screenFramable : d.screenFramable,
        appUsed: fresh.appUsed || d.appUsed,
        beyond: fresh.beyond || d.beyond,
        ingredients: fresh.ingredients.map((i) => ({ ...known.get(i.ref), ...i, name: i.name || known.get(i.ref)?.name })),
      };
    });
  }, [status, id]);

  const refsKey = job ? [job.pick?.ref || "", ...job.ingredients.map((i) => i.ref), job.view, job.screen || ""].join("|") : "";

  const loadScreen = useCallback(async (j: Job, known: Record<string, PartInfo>) => {
    if (j.screen) {
      const name = j.appUsed?.name || j.name;
      setScreen(
        j.route === "A" || j.screenFramable === false
          ? { kind: "window", thing: "", name, url: j.screen }
          : { kind: "frame", thing: "", name, url: j.screen },
      );
      return;
    }
    for (const cand of screenCandidates(j, (ref) => known[ref]?.thing?.id || thingIdOf(ref))) {
      const r = await getThing(cand);
      const s = r.ok ? screenFor(r.data) : null;
      if (s) {
        setScreen(s);
        return;
      }
    }
    setScreen(null);
  }, []);

  useEffect(() => {
    if (!job || things === null) return;
    void loadParts(job, things).then((p) => {
      setParts(p);
      void loadScreen(job, p);
    });
    // refsKey stands for the job's parts; the job object changes on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refsKey, things, loadScreen]);

  useEffect(() => {
    if (job && ask === null) setAsk(job.ask);
  }, [job, ask]);

  const nameOf = useCallback((ref: string) => parts[ref]?.thing?.name || refWords(ref), [parts]);

  const views = useMemo(() => (job ? partViews(job, parts) : []), [job, parts]);
  const rows = useMemo(
    // Only rows she does herself; programs, packages and the outside sites
    // on the list come with the Yes (after it, a site it could not switch on).
    () =>
      status && job
        ? openRows(
            status.setup.filter((r) => r.job_ids.includes(job.id)),
            status.yes.given,
          )
        : [],
    [status, job],
  );

  async function again(fromTop = false) {
    setBusy("check");
    const r = await checkJobAgain(id, fromTop);
    setBusy("");
    setSaid(r.status === 0 ? NO_ANSWER : r.message || (r.ok ? "Checking it again." : "That did not start."));
    await reload();
    await loadDetail();
    if (r.ok) {
      // The status polls fast while it is being built; the line goes once it is done.
      setTimeout(() => setSaid(""), 15_000);
    }
  }

  async function addLink(order: HerOrder = lastOrder.current) {
    const url = link.trim();
    if (!url) return;
    lastOrder.current = order;
    setBusy("link");
    const r = await addProjectLink(id, url, order);
    setBusy("");
    setSaid(r.status === 0 ? NO_ANSWER : r.message || (r.ok ? "Added to the list: say yes to it to start it." : "That did not work."));
    const fix = (r.data as { fix?: FixCardData | null } | null)?.fix;
    setLinkFix(!r.ok && fix && fix.kind === "switch_on" ? fix : null);
    if (r.ok) setLink("");
    await reload();
    await loadDetail();
  }

  async function switchOnForLink(fix: FixCardData) {
    setBusy("link");
    const got = await applyFix(fix);
    setBusy("");
    setSaid(got.line);
    if (got.ok) {
      setLinkFix(null);
      await addLink();
    }
  }

  const thingOf = (ref: string) => views.find((v) => v.ref === ref)?.thingId || "";

  async function toggle(ref: string, on: boolean) {
    const tid = thingOf(ref);
    if (!tid) return;
    setBusy(ref);
    try {
      if (tid.startsWith("feed-")) {
        const r = await setFeedAllowed(feedAppId(tid), on);
        setSaid(r?.message || "");
      } else {
        const r = await hostAction(tid, on ? "start" : "stop");
        setSaid(r.status === 0 ? NO_ANSWER : r.data.message || "");
      }
    } catch (err) {
      setSaid(err instanceof Error ? err.message : "That did not work.");
    }
    setBusy("");
    if (job && things) {
      const p = await loadParts(job, things);
      setParts(p);
      void loadScreen(job, p);
    }
  }

  async function remove(ref: string) {
    if (confirming !== ref) {
      setConfirming(ref);
      return;
    }
    setConfirming("");
    const tid = thingOf(ref);
    if (!tid) return;
    setBusy(ref);
    const r = await removeThing(tid);
    setBusy("");
    setSaid(r.status === 0 ? NO_ANSWER : r.data.message || (r.ok ? "Taken out." : "It could not be taken out."));
    await loadThings();
    await reload();
  }

  async function start() {
    if (!screen || screen.kind !== "stopped") return;
    const r = await hostAction(screen.thing, "start");
    setSaid(r.status === 0 ? NO_ANSWER : r.data.message || "");
    if (job) setTimeout(() => void loadScreen(job, parts), 1500);
  }


  if (!job) {
    return (
      <AppPage>
        <p style={small}>
          {failed
            ? NO_ANSWER
            : status && !status.ready
              ? status.line || "Your bot is getting ready."
              : status
                ? "Your bot has no job by that name."
                : "Opening..."}
        </p>
        {status || failed ? (
          <Link href="/dashboard" style={{ ...small, color: "var(--sky)" }}>
            Back to your home page
          </Link>
        ) : null}
      </AppPage>
    );
  }

  const words = status?.words;
  return (
    <AppPage>
      <header className="flex flex-wrap items-end gap-x-6 gap-y-2">
        <div className="min-w-0" style={{ flex: "1 1 420px" }}>
          <div className="flex items-center gap-3">
            <Link href="/dashboard" style={{ ...microLabel, textDecoration: "none" }}>
              Home
            </Link>
            {words ? <StatusTag job={job} words={words} /> : null}
          </div>
          <h1
            style={{
              margin: "4px 0 0",
              fontFamily: FREDOKA,
              fontWeight: 600,
              fontSize: "clamp(26px,2.4vw,38px)",
              letterSpacing: "-.6px",
              lineHeight: 1.05,
              color: "var(--text)",
            }}
          >
            {job.name}
          </h1>
          {job.does ? <p style={{ ...small, fontSize: 15, marginTop: 4 }}>{job.does}</p> : null}
          {job.wanted ? (
            <p style={{ ...small, fontSize: 13, marginTop: 2 }}>
              You asked for: {job.wanted}. What these sources do not give is not covered yet.{" "}
              <Link href={`/dashboard/add?find=${encodeURIComponent(job.name)}#find-skills`} style={{ color: "var(--sky)", fontWeight: 700 }}>
                Find more
              </Link>
            </p>
          ) : null}
          {words && job.route !== "D" ? (
            <p style={{ ...small, fontSize: 13, marginTop: 2, color: "var(--text3)" }}>{howLine(job, words, nameOf)}</p>
          ) : null}
          {job.trying ? (
            <p role="status" style={{ ...small, fontSize: 13, marginTop: 2, fontWeight: 700 }}>
              {tryingLine(job)}
            </p>
          ) : null}
          {job.appUsed?.program ? <OpenDesktopProgram program={job.appUsed.program} name={job.appUsed.name} /> : null}
          {(job.appUsed?.notes || []).map((note) => (
            <p key={note} style={{ ...small, fontSize: 13, marginTop: 2 }}>
              {note}
            </p>
          ))}
        </div>
      </header>

      <VaultWaitUnlock line={status?.vaultWait} onOpened={reload} />

      {status && job.route !== "D" && (!status.yes.given || rows.length > 0) ? (
        <Panel accent="--lemon" style={{ padding: "12px 18px" }}>
          <div style={{ ...microLabel, marginBottom: 4 }}>
            {status.yes.given ? "To set up for this job" : "Set up, then say yes (one yes builds every job)"}
          </div>
          <YesPanel status={status} reload={reload} rowsFor={(r) => r.job_ids.includes(job.id)} showLines={false} />
        </Panel>
      ) : null}
      {job.status === "building" ? (
        <p style={{ ...small, fontSize: 13 }} role="status">
          Being built now. This page updates by itself.
        </p>
      ) : null}

      <div className="grid items-start gap-5 lg:grid-cols-[minmax(0,1fr)_320px]">
        <main className="min-w-0">
          <JobScreen job={job} screen={screen} onStart={() => void start()} />
        </main>

        <aside style={{ display: "grid", gap: 18 }}>
          <section>
            <div style={{ ...microLabel, marginBottom: 2 }}>Ingredients</div>
            <IngredientRows
              items={views}
              onToggle={(ref, on) => void toggle(ref, on)}
              onRemove={(ref) => void remove(ref)}
              busy={busy}
              confirming={confirming}
            />
          </section>

          {job.beyond?.length ? (
            <section>
              <div style={{ ...microLabel, marginBottom: 2 }}>
                Added beyond {job.appUsed?.name || "its"} own folder
              </div>
              <ul style={{ display: "grid", gap: 8, listStyle: "none", margin: 0, padding: 0 }}>
                {job.beyond.map((b) => (
                  <li key={b.what} style={{ ...small, fontSize: 13 }}>
                    <div style={{ fontWeight: 700, overflowWrap: "anywhere" }}>
                      {b.what}
                      {b.admin ? " (with your admin yes)" : ""}
                    </div>
                    {b.undo.startsWith("no automatic undo") ? (
                      <div style={{ overflowWrap: "anywhere" }}>{b.undo}</div>
                    ) : (
                      <div style={{ overflowWrap: "anywhere" }}>
                        To undo, run: <code style={{ userSelect: "all" }}>{b.undo}</code>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {job.tried?.length || job.triedBefore?.length ? (
            <section>
              <div style={{ ...microLabel, marginBottom: 2 }}>Tried</div>
              {(job.tried ?? []).map((t) => (
                <p key={t.ref} style={{ ...small, fontSize: 13, overflowWrap: "anywhere" }}>
                  {t.name}: {t.why || "it did not start here"}
                </p>
              ))}
              {job.triedBefore?.length ? (
                <p style={{ ...small, fontSize: 13, color: "var(--text3)", overflowWrap: "anywhere" }}>
                  Tried before: {job.triedBefore.map((t) => `${t.name} (${t.why || "it did not start here"})`).join("; ")}
                </p>
              ) : null}
            </section>
          ) : null}

          <form
            style={{ display: "grid", gap: 8 }}
            onSubmit={(e) => {
              e.preventDefault();
              const text = (ask || "").trim();
              if (text && stashChatQuery(text)) router.push("/dashboard/chat");
            }}
          >
            <label htmlFor="job-ask" style={microLabel}>
              Ask about this
            </label>
            <textarea
              id="job-ask"
              rows={3}
              maxLength={1000}
              value={ask || ""}
              onChange={(e) => setAsk(e.target.value)}
              style={{
                resize: "vertical",
                background: "var(--card2)",
                border: "1px solid var(--line)",
                borderRadius: 12,
                padding: "8px 10px",
                color: "var(--text)",
                fontFamily: NUNITO,
                fontSize: 14,
              }}
            />
            <div className="flex flex-wrap gap-2">
              <Pill tone="accent" type="submit" disabled={!(ask || "").trim()}>
                Ask in chat
              </Pill>
              <Pill disabled={busy === "check"} onClick={() => void again()}>
                Try next app
              </Pill>
              <Pill disabled={busy === "check"} onClick={() => void again(true)}>
                Check again
              </Pill>
            </div>
          </form>
          <form
            style={{ display: "grid", gap: 8 }}
            onSubmit={(e) => {
              e.preventDefault();
              void addLink("first");
            }}
          >
            <label htmlFor="job-link" style={microLabel}>
              Use a project you know
            </label>
            <input
              id="job-link"
              type="url"
              placeholder="https://github.com/owner/project"
              maxLength={300}
              value={link}
              onChange={(e) => setLink(e.target.value)}
              style={{
                background: "var(--card2)",
                border: "1px solid var(--line)",
                borderRadius: 12,
                padding: "8px 10px",
                color: "var(--text)",
                fontFamily: NUNITO,
                fontSize: 14,
              }}
            />
            <div className="flex flex-wrap gap-2">
              <Pill type="submit" tone="accent" disabled={busy === "link" || !link.trim()}>
                Run it first
              </Pill>
              <Pill disabled={busy === "link" || !link.trim()} onClick={() => void addLink("list")}>
                Add to the list
              </Pill>
              {linkFix ? (
                <Pill tone="accent" disabled={busy === "link"} onClick={() => void switchOnForLink(linkFix)}>
                  {linkFix.label || "Switch it on"}
                </Pill>
              ) : null}
            </div>
            {linkFix?.detail ? <p style={{ ...small, fontSize: 13 }}>{linkFix.detail}</p> : null}
          </form>
          {said ? <p style={{ ...small, fontSize: 13 }}>{said}</p> : null}
        </aside>
      </div>
    </AppPage>
  );
}
