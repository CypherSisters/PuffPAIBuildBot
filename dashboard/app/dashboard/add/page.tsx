"use client";

/**
 * Add to <bot>: type what you want, see what was found, tick, say yes.
 *
 * The search starts on the lists saved on this computer. Looking online is a
 * separate button that first says exactly which words it sends. "Add these"
 * shows the backend's own sentence for exactly the ticked lines, and only
 * "Yes, add them" carries it out, with a one-time yes for this device. A line
 * with something to show (a live card, an update, a look) gets "Put it on my
 * home page" at that step, ticked for live cards. While looking online, the
 * page says which libraries have answered; the server stops waiting for a
 * library after a few seconds, so the page never sits there silent. After
 * adding, the page shows what really happened (a card whose website is off
 * says so, with its own switch), how far a book has got, and everything
 * added so far with a way to take it out.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Button from "@/app/_components/Button";
import {
  addThese,
  askToAdd,
  askToSwitchOn,
  clearNotes,
  findToAdd,
  getAdded,
  getAddPage,
  getLooking,
  getNotes,
  readFinds,
  setOnHome,
  switchOn,
  takeOut,
  type AddedRow,
  type AddFinds,
  type AddJob,
  type AddLine,
  type Looking,
} from "./addService";
import ThingsSection from "./ThingsSection";
import AddATool from "@/app/_components/AddATool";
import FromYourGitHub from "@/app/_components/GitHub/FromYourGitHub";
import { fromYourGitHub } from "@/app/_lib/services/githubService";
import {
  AppHeader,
  AppPage,
  inputStyle,
  JBM,
  NUNITO,
  Panel,
  SectionBar,
  wellStyle,
} from "@/app/dashboard/apps/_components/ui";

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

type Pending = { ticket: string; sentence: string; ids: string[]; home: string[] };
type SwitchAsk = { service: string; ticket: string; sentence: string };

/** Lines grouped by the job they help with, in the order of her jobs; lines
 * with no job come first under no caption. */
function byJob(items: AddLine[], jobs: AddJob[] | undefined): [string, AddLine[]][] {
  if (!jobs || jobs.length === 0) return [["", items]];
  const names = new Map(jobs.map((j) => [j.id, j.name]));
  const out: [string, AddLine[]][] = [];
  const loose = items.filter((l) => !l.need || !names.has(l.need));
  if (loose.length) out.push(["", loose]);
  for (const job of jobs) {
    const mine = items.filter((l) => l.need === job.id);
    if (mine.length) out.push([job.name, mine]);
  }
  return out;
}

function LineRow({
  line,
  ticked,
  onTick,
  progress,
  onSwitch,
  busy,
}: {
  line: AddLine;
  ticked: boolean;
  onTick: () => void;
  progress: string[];
  onSwitch: (service: string) => void;
  busy: boolean;
}) {
  const result = line.result;
  return (
    <div style={{ ...wellStyle, display: "grid", gap: 4 }}>
      <label className="flex items-start gap-3" style={{ cursor: line.can_add ? "pointer" : "default" }}>
        {line.can_add && !result ? (
          <input type="checkbox" checked={ticked} onChange={onTick} disabled={busy} />
        ) : (
          <span style={{ width: 13 }} />
        )}
        <span style={{ display: "grid", gap: 2 }}>
          <span className="flex flex-wrap items-center" style={{ gap: 8 }}>
            <strong style={{ fontFamily: NUNITO, fontSize: 15, color: "var(--text)" }}>
              {line.label}
            </strong>
            {fromYourGitHub(line) ? <FromYourGitHub /> : null}
          </span>
          <span style={small}>{line.detail}</span>
          {line.from && <span style={{ ...small, color: "var(--text3)" }}>From: {line.from}</span>}
          {!line.can_add && (
            <span style={{ ...small, color: "var(--text3)" }}>Not something your bot can add yet.</span>
          )}
        </span>
      </label>
      {result && (
        <div style={{ borderLeft: "3px solid var(--mint)", paddingLeft: 10, display: "grid", gap: 4 }}>
          <p style={{ ...small, color: "var(--text)" }}>{result.line}</p>
          {progress.map((p) => (
            <p key={p} style={small}>{p}</p>
          ))}
          {result.status === "added_off" && result.switch_on && (
            <div>
              <Button label="Switch it on" color="info" small roundedFull disabled={busy}
                      onClick={() => onSwitch(result.switch_on!.service)} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function AddPageView() {
  const [title, setTitle] = useState("Find more for your bot");
  const [lede, setLede] = useState("");
  const [paired, setPaired] = useState(true);
  const [words, setWords] = useState("");
  const [finds, setFinds] = useState<AddFinds | null>(null);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [pending, setPending] = useState<Pending | null>(null);
  const [switchAsk, setSwitchAsk] = useState<SwitchAsk | null>(null);
  const [added, setAdded] = useState<AddedRow[]>([]);
  const [notes, setNotes] = useState<{ at: string; terms: string[] }[]>([]);
  const [said, setSaid] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [looking, setLooking] = useState<Looking | null>(null);
  const [noOnline, setNoOnline] = useState(false);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadLists = useCallback(async () => {
    try {
      const [a, n] = await Promise.all([getAdded(), getNotes()]);
      setAdded(a.rows || []);
      setNotes(n.notes || []);
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "This page did not load.");
    }
  }, []);

  useEffect(() => {
    getAddPage()
      .then((p) => {
        setTitle(p.title);
        setLede(p.line);
        setPaired(p.paired);
      })
      .catch((err) => setProblem(err instanceof Error ? err.message : "This page did not load."));
    loadLists();
  }, [loadLists]);

  // While a book is on its way, read how far it has got every few seconds.
  const working =
    !!finds && finds.groups.some((g) => g.items.some((i) => i.result?.status === "working"));
  useEffect(() => {
    if (timer.current) clearInterval(timer.current);
    if (!finds || !working) return;
    const id = finds.session;
    timer.current = setInterval(() => {
      readFinds(id).then(setFinds).catch(() => undefined);
    }, 3000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [finds, working]);

  const act = useCallback(async (work: () => Promise<unknown>) => {
    setBusy(true);
    setProblem(null);
    try {
      await work();
    } catch (err) {
      setProblem(err instanceof Error ? err.message : "That did not work.");
    } finally {
      setBusy(false);
    }
  }, []);

  const search = (online: boolean) =>
    act(async () => {
      setPending(null);
      setSaid([]);
      setTicked(new Set());
      setNoOnline(false);
      if (!online) {
        setLooking(null);
        setFinds(await findToAdd(words, false));
        await loadLists();
        return;
      }
      // Say which libraries have answered while the server asks them.
      setLooking({ active: true, line: "Looking online...", libraries: [] });
      const poll = setInterval(() => {
        getLooking()
          .then((now) => {
            if (now.active) setLooking(now);
          })
          .catch(() => undefined);
      }, 700);
      try {
        const found = await findToAdd(words, true);
        setFinds(found);
        setLooking(await getLooking().catch(() => null));
      } catch (err) {
        setLooking(null);
        throw err;
      } finally {
        clearInterval(poll);
      }
      await loadLists();
    });

  const lineById = (id: string): AddLine | undefined =>
    finds?.groups.flatMap((g) => g.items).find((i) => i.id === id);

  const toggleHome = (id: string) =>
    setPending((prev) =>
      prev
        ? { ...prev, home: prev.home.includes(id) ? prev.home.filter((h) => h !== id) : [...prev.home, id] }
        : prev,
    );

  const toggle = (id: string) =>
    setTicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const progressFor = (id: string) => (finds?.progress && finds.progress[id]) || [];

  return (
    <AppPage>
      <AppHeader title={title} lede={lede} />
      {!paired && (
        <Panel accent="--lemon">
          <p style={small}>Adding things has to be done from a browser that is paired with your bot.</p>
        </Panel>
      )}
      {problem && (
        <Panel accent="--lemon">
          <p style={small}>{problem}</p>
        </Panel>
      )}

      <AddATool where="find" />

      <ThingsSection />

      <SectionBar title="Books, live cards and pictures" />
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (words.trim()) search(false);
        }}
      >
        <input
          aria-label="What do you want to add?"
          placeholder="What do you want your bot to know or show?"
          value={words}
          maxLength={200}
          onChange={(e) => setWords(e.target.value)}
          style={{ ...inputStyle, flex: "1 1 280px" }}
        />
        <Button label="Find" color="info" roundedFull disabled={busy || !words.trim()} type="submit" />
      </form>

      {looking && (looking.active || looking.libraries.length > 0) && (
        <div role="status" aria-live="polite" style={{ ...wellStyle, display: "grid", gap: 2 }}>
          <p style={{ ...small, color: "var(--text)" }}>{looking.line}</p>
          {looking.libraries.map((l) => (
            <p key={l.library} style={{ ...small, fontFamily: JBM, fontSize: 12 }}>
              {l.line}
            </p>
          ))}
        </div>
      )}

      {finds && (
        <div style={{ display: "grid", gap: 10 }}>
          {finds.searched_line && <p style={small}>{finds.searched_line}</p>}
          {finds.notes.map((n) => (
            <p key={n} style={small}>{n}</p>
          ))}
          {finds.nothing_found.length > 0 && (
            <Panel>
              {finds.nothing_found.map((l) => (
                <p key={l} style={small}>{l}</p>
              ))}
            </Panel>
          )}
          {finds.groups.map((group) => (
            <div key={group.section} style={{ display: "grid", gap: 8 }}>
              <SectionBar title={group.heading} />
              {byJob(group.items, finds.jobs).map(([job, lines]) => (
                <div key={`${group.section}:${job}`} style={{ display: "grid", gap: 8 }}>
                {job && <p style={{ ...small, fontWeight: 600 }}>{job}</p>}
                {lines.map((line) => (
                <LineRow
                  key={line.id}
                  line={line}
                  ticked={ticked.has(line.id)}
                  onTick={() => toggle(line.id)}
                  progress={progressFor(line.id)}
                  busy={busy}
                  onSwitch={(service) =>
                    act(async () => {
                      const asked = await askToSwitchOn(service);
                      setSwitchAsk({ service, ticket: asked.ticket, sentence: asked.sentence });
                    })
                  }
                />
                ))}
                </div>
              ))}
            </div>
          ))}
          {finds.online.possible && !finds.searched_online && !noOnline && (
            <div className="flex flex-wrap items-center gap-3">
              <p style={small}>{finds.online.line}</p>
              <Button label="Search online" color="lightDark" small roundedFull
                      disabled={busy} onClick={() => search(true)} />
              <Button label="No thanks" color="lightDark" small roundedFull
                      disabled={busy} onClick={() => setNoOnline(true)} />
            </div>
          )}
          {finds.choose_line && !pending && (
            <div className="flex flex-wrap items-center gap-3">
              <p style={small}>{finds.choose_line}</p>
              <Button
                label="Add these"
                color="info"
                roundedFull
                disabled={busy || ticked.size === 0 || !paired}
                onClick={() =>
                  act(async () => {
                    const picked = Array.from(ticked);
                    const asked = await askToAdd(finds.session, picked);
                    const home = picked.filter((id) => lineById(id)?.home?.default);
                    setPending({ ticket: asked.ticket, sentence: asked.sentence, ids: picked, home });
                  })
                }
              />
            </div>
          )}
          {pending && (
            <Panel accent="--lemon">
              <p style={{ ...small, color: "var(--text)" }}>{pending.sentence}</p>
              {pending.ids
                .map((id) => lineById(id))
                .filter((line): line is AddLine => !!line && !!line.home?.offer)
                .map((line) => (
                  <label key={line.id} className="flex items-center gap-2" style={{ ...small, marginTop: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={pending.home.includes(line.id)}
                      onChange={() => toggleHome(line.id)}
                      disabled={busy}
                    />
                    <span>
                      Put it on my home page: <strong style={{ color: "var(--text)" }}>{line.label}</strong>
                    </span>
                  </label>
                ))}
              <div className="flex gap-2" style={{ marginTop: 8 }}>
                <Button
                  label="Yes, add them"
                  color="info"
                  small
                  roundedFull
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      const yes = pending;
                      setPending(null);
                      const done = await addThese(finds.session, yes.ids, yes.ticket, yes.home);
                      setSaid(done.results.map((r) => r.line));
                      setTicked(new Set());
                      setFinds(await readFinds(finds.session));
                      await loadLists();
                    })
                  }
                />
                <Button label="Not now" color="lightDark" small roundedFull disabled={busy}
                        onClick={() => setPending(null)} />
              </div>
            </Panel>
          )}
          {switchAsk && (
            <Panel accent="--lemon">
              <p style={{ ...small, color: "var(--text)" }}>{switchAsk.sentence}</p>
              <div className="flex gap-2" style={{ marginTop: 8 }}>
                <Button
                  label="Yes, switch it on"
                  color="info"
                  small
                  roundedFull
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      const yes = switchAsk;
                      setSwitchAsk(null);
                      const done = await switchOn(yes.service, yes.ticket);
                      setSaid([done.line]);
                    })
                  }
                />
                <Button label="Not now" color="lightDark" small roundedFull disabled={busy}
                        onClick={() => setSwitchAsk(null)} />
              </div>
            </Panel>
          )}
          {said.length > 0 && (
            <Panel>
              {said.map((l) => (
                <p key={l} style={{ ...small, color: "var(--text)" }}>{l}</p>
              ))}
            </Panel>
          )}
        </div>
      )}

      <SectionBar title="Added so far" />
      {added.length === 0 ? (
        <p style={small}>Nothing has been added since your bot was built.</p>
      ) : (
        <div style={{ display: "grid", gap: 6 }}>
          {added.map((row) => (
            <div key={row.entry} style={{ ...wellStyle }} className="flex flex-wrap items-center gap-3">
              <span style={{ ...small, color: "var(--text)" }}>{row.label}</span>
              <span style={{ ...small, fontFamily: JBM, fontSize: 12, color: "var(--text3)" }}>
                added {row.added_on} from {row.from}
                {row.websites.length > 0 ? ` - uses ${row.websites.join(", ")}` : ""}
              </span>
              <div className="flex-1" />
              <div className="flex gap-2">
              {row.home?.can && (
                <Button
                  label={row.home.on ? "Take it off my home page" : "Put it on my home page"}
                  color="lightDark"
                  small
                  roundedFull
                  disabled={busy}
                  onClick={() =>
                    act(async () => {
                      const done = await setOnHome(row.entry, !row.home!.on);
                      setSaid([done.line]);
                      await loadLists();
                    })
                  }
                />
              )}
              <Button label="Take it out" color="lightDark" small roundedFull disabled={busy}
                      onClick={() =>
                        act(async () => {
                          const done = await takeOut(row.entry);
                          setSaid([done.line]);
                          await loadLists();
                        })
                      } />
              </div>
            </div>
          ))}
        </div>
      )}

      {notes.length > 0 && (
        <>
          <SectionBar title="Asked for, not found yet" />
          <p style={small}>
            Words you asked for that nothing was found for. They stay on this computer, so a later
            version can build them.
          </p>
          {notes.map((n) => (
            <p key={`${n.at}-${n.terms.join("-")}`} style={small}>
              {n.at.slice(0, 10)}: {n.terms.join(", ")}
            </p>
          ))}
          <div>
            <Button label="Clear these" color="lightDark" small roundedFull disabled={busy}
                    onClick={() =>
                      act(async () => {
                        await clearNotes();
                        await loadLists();
                      })
                    } />
          </div>
        </>
      )}
    </AppPage>
  );
}
