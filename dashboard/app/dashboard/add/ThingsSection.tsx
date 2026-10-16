"use client";

/**
 * "Find more for <Bot>": the same search as the wizard's "Finding things
 * for your bot" (skill shelves, GitHub and the other code sites, the tool
 * lists), shown as cards: type, stars or downloads, one line, and an Add
 * button. Add first shows what it is, where it comes from, what it runs and
 * the shelf's own check (the backend's words); only "Yes, add it" installs.
 * After the install the new thing is on her home page as a tile, with its
 * own page (Toolsmith makes its page recipe), and the card says so.
 *
 * A code project from GitHub (an app, a program, a library, data) goes into
 * the same build loop as a job's Find more (POST /build/project): to the job
 * it clearly fits, or ONE new job named after it, and the card says where it
 * went; the job starts after her one Yes on its page. One with no licence is
 * never refused: the card says so in one line and the button reads "Add anyway".
 *
 * Each card and each job also says what BuildBot does with it in the
 * Assemble step (GET /assemble/status plan): "Use as is" (a full app),
 * "Parts BuildBot puts together" (pieces it joins into one app), or, for a
 * job, "Nothing found yet" with the reason.
 */

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import Button from "@/app/_components/Button";
import {
  followJob,
  getInstalled,
  installThings,
  previewThing,
  startFind,
  undoInstalled,
  type Find,
  type FindResult,
  type InstalledRow,
  type InstallResult,
  type JobView,
  type Preview,
} from "@/app/_lib/services/thingsService";
import { FIND_EXAMPLE, getTour } from "@/app/_lib/services/tourService";
import { addProject, whereProjectGoes, type HerOrder, type ProjectAsk } from "@/app/_lib/services/buildService";
import MissingCard from "@/app/dashboard/chat/MissingCard";
import {
  getAssembleStatus,
  howForFind,
  HOW_WORDS,
  type AssembleHow,
  type AssemblePlanRow,
} from "@/app/_lib/services/assembleService";
import { FIND_EVENT } from "@/app/_components/AddATool";
import { FREDOKA, inputStyle, NUNITO, Panel, Pill, SectionBar } from "@/app/dashboard/apps/_components/ui";

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

const CARD: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 6,
  padding: "14px 16px",
  borderRadius: 18,
  border: "1px solid var(--line)",
  background: "var(--card)",
};

type Stage =
  | { at: "idle" }
  | { at: "where"; fits: { id: string; name: string }; newName: string }
  | { at: "placed"; line: string; page: string }
  | { at: "asking" }
  | { at: "ask"; preview: Preview }
  | { at: "adding"; lines: string[] }
  | { at: "done"; lines: string[]; ok: boolean; page: string };

/** Code projects that go into the build loop (Toolsmith runs them as an app,
 * or makes an app from them). Skills and tool servers keep their own ways in. */
const CODE_TYPES = new Set(["browser_app", "server_app", "cli", "library", "data"]);
const NO_LICENCE_LINE = "Its maker has not said others may copy it. Using it on your own computer is your call.";

function isCodeProject(f: Find): boolean {
  return f.site === "github" && CODE_TYPES.has(f.type) && f.can === "integrate" && /^https:\/\/github\.com\//.test(f.url);
}

/** The small label for how BuildBot uses a find. */
function HowChip({ how }: { how: AssembleHow }) {
  return (
    <span
      style={{
        ...small,
        fontSize: 11,
        fontWeight: 800,
        padding: "1px 8px",
        borderRadius: 999,
        background: "var(--chip)",
        color: how === "use_as_is" ? "var(--mint)" : how === "assemble" ? "var(--sky)" : "var(--lemon)",
      }}
    >
      {HOW_WORDS[how]}
    </span>
  );
}

function FindCard({
  f,
  have,
  how,
  cap,
  onAdded,
}: {
  f: Find;
  have: boolean;
  how?: AssembleHow;
  cap?: string;
  onAdded: () => void;
}) {
  const [stage, setStage] = useState<Stage>({ at: "idle" });
  const canAdd = f.can !== "look";
  const code = isCodeProject(f);
  const project: ProjectAsk = { url: f.url, name: f.name, what: f.what, ...(cap ? { cap } : {}) };

  /** A code project: to the job it clearly fits (asked first), else one new job. */
  async function addCode(job?: string, order: HerOrder = "first") {
    setStage({ at: "asking" });
    let target = job;
    if (!target) {
      const where = await whereProjectGoes(project);
      if (!where.ok) {
        setStage({ at: "done", lines: [where.message || "No answer from this computer. Nothing was added."], ok: false, page: "" });
        return;
      }
      if (where.data.fits) {
        setStage({ at: "where", fits: where.data.fits, newName: where.data.new_name });
        return;
      }
      target = "new";
    }
    setStage({ at: "adding", lines: [] });
    const reply = await addProject(project, target, order);
    if (!reply.ok) {
      setStage({ at: "done", lines: [reply.message || "That was not accepted. Nothing was added."], ok: false, page: "" });
      return;
    }
    setStage({ at: "placed", line: reply.data.line, page: reply.data.page });
  }

  async function ask() {
    setStage({ at: "asking" });
    const reply = await previewThing(f.id);
    if (!reply.ok) {
      setStage({ at: "done", lines: ["No answer from this computer. Nothing was added."], ok: false, page: "" });
      return;
    }
    setStage({ at: "ask", preview: reply.data });
  }

  async function add() {
    setStage({ at: "adding", lines: [] });
    const reply = await installThings([f.id]);
    if (!reply.ok || !reply.data.job) {
      setStage({ at: "done", lines: ["That was not accepted. Nothing was added."], ok: false, page: "" });
      return;
    }
    const last = await followJob<InstallResult>(reply.data.job, (view: JobView<InstallResult>) =>
      setStage({ at: "adding", lines: view.lines }),
    );
    const row = last?.result?.results?.[0];
    const ok = row?.status === "installed" || row?.status === "added";
    setStage({ at: "done", lines: last?.lines || [], ok, page: row?.page || "" });
    if (ok) onAdded();
  }

  const facts = [
    f.popularity_words,
    f.by ? `by ${f.by}` : "",
    `on ${f.site_name}`,
    f.type === "book" || code ? f.licence_words : "",
    f.size_words,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div style={CARD}>
      <div className="flex flex-wrap items-center gap-2">
        <span
          style={{
            ...small,
            fontSize: 11,
            fontWeight: 800,
            letterSpacing: 0.5,
            textTransform: "uppercase",
            color: "var(--text3)",
          }}
        >
          {f.type_words}
        </span>
        {f.verdict_words && <span style={{ ...small, fontSize: 11 }}>{f.verdict_words}</span>}
        {how && how !== "not_found" && <HowChip how={how} />}
      </div>
      <h3 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 18, color: "var(--text)" }}>{f.name}</h3>
      <p style={{ ...small, fontSize: 12, color: "var(--text3)" }}>{facts}</p>
      {f.what && <p style={small}>{f.what}</p>}
      {f.can_trade && <p style={{ ...small, color: "var(--puff-red)" }}>This one can place trades with real money.</p>}

      {stage.at === "idle" && code && !have && f.no_licence && <p style={small}>{NO_LICENCE_LINE}</p>}
      {stage.at === "idle" && (
        <div className="flex flex-wrap items-center gap-2" style={{ marginTop: 4 }}>
          {have ? (
            <span style={{ ...small, fontWeight: 700, color: "var(--text)" }}>Already in your bot</span>
          ) : code ? (
            <Pill tone="accent" onClick={() => void addCode()}>
              {f.no_licence ? "Add anyway" : "Add"}
            </Pill>
          ) : canAdd ? (
            <Pill tone="accent" onClick={() => void ask()}>
              Add
            </Pill>
          ) : (
            <span style={{ ...small, fontSize: 12 }}>{f.can_words}</span>
          )}
          {f.url && (
            <a href={f.url} target="_blank" rel="noreferrer noopener" style={{ ...small, color: "var(--sky)" }}>
              Where it comes from
            </a>
          )}
        </div>
      )}
      {stage.at === "asking" && <p style={small}>Reading it...</p>}
      {stage.at === "where" && (
        <div style={{ display: "grid", gap: 6 }}>
          <p style={small}>It fits your job {stage.fits.name}.</p>
          <div className="flex flex-wrap gap-2">
            <Pill tone="accent" onClick={() => void addCode(stage.fits.id, "first")}>
              Run it first
            </Pill>
            <Pill onClick={() => void addCode(stage.fits.id, "list")}>Add to the list</Pill>
            <Pill onClick={() => void addCode("new")}>Make it its own job</Pill>
            <Pill onClick={() => setStage({ at: "idle" })}>No</Pill>
          </div>
        </div>
      )}
      {stage.at === "placed" && (
        <p role="status" style={{ ...small, fontWeight: 700, color: "var(--text)" }}>
          {stage.line}{" "}
          {stage.page && (
            <Link href={stage.page} style={{ color: "var(--sky)" }}>
              Open the job
            </Link>
          )}
        </p>
      )}
      {stage.at === "ask" && (
        <div style={{ display: "grid", gap: 6 }}>
          {(stage.preview.lines || [stage.preview.line || ""]).map((l) => (
            <p key={l} style={small}>
              {l}
            </p>
          ))}
          {stage.preview.missing && <MissingCard card={stage.preview.missing} />}
          {stage.preview.refuse ? (
            <p style={{ ...small, fontWeight: 700 }}>BuildBot will not install this one.</p>
          ) : (
            <div className="flex gap-2">
              <Pill tone="accent" onClick={() => void add()}>
                Yes, add it
              </Pill>
              <Pill onClick={() => setStage({ at: "idle" })}>No</Pill>
            </div>
          )}
        </div>
      )}
      {(stage.at === "adding" || stage.at === "done") && (
        <div role="status" aria-live="polite" style={{ display: "grid", gap: 3 }}>
          {stage.lines.map((l, i) => (
            <p key={`${i}-${l}`} style={small}>
              {l}
            </p>
          ))}
          {stage.at === "adding" && stage.lines.length === 0 && <p style={small}>Starting...</p>}
          {stage.at === "done" && stage.ok && (
            <p style={{ ...small, fontWeight: 700, color: "var(--text)" }}>
              It is on your home page now.{" "}
              {stage.page && (
                <Link href={stage.page} style={{ color: "var(--sky)" }}>
                  Open its page
                </Link>
              )}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

function FoundCards({
  result,
  have,
  plan,
  cap,
  onAdded,
}: {
  result: FindResult;
  have: Set<string>;
  plan: AssemblePlanRow[];
  cap?: string;
  onAdded: () => void;
}) {
  /** How a find is used: its own word from the backend first, then the plan. */
  const howOf = (f: Find): AssembleHow | undefined => {
    const own = (f as Find & { how?: AssembleHow }).how;
    return own || howForFind(plan, f.id)?.how;
  };
  return (
    <div style={{ display: "grid", gap: 14 }}>
      {result.jobs_line ? <p style={small}>{result.jobs_line}</p> : null}
      {result.jobs.map((job) => (
        <section key={job.id} style={{ display: "grid", gap: 8 }}>
          <h2 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 19, color: "var(--text)" }}>
            {job.name}
          </h2>
          <div className="grid gap-3" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))" }}>
            {job.types.flatMap((t) => t.finds).map((f) => (
              <FindCard key={f.id} f={f} have={have.has(f.id)} how={howOf(f)} cap={cap} onAdded={onAdded} />
            ))}
          </div>
          {plan
            .filter((p) => p.cap === job.id && p.how === "not_found")
            .map((p) => (
              <p key={p.cap} className="flex flex-wrap items-center gap-2" style={small}>
                <HowChip how="not_found" />
                {p.reason || ""}
              </p>
            ))}
          {job.notes.map((n) => (
            <p key={n} style={small}>
              {n}
            </p>
          ))}
        </section>
      ))}
      {result.notes.map((n) => (
        <p key={n} style={small}>
          {n}
        </p>
      ))}
    </div>
  );
}

export default function ThingsSection() {
  const [words, setWords] = useState("");
  const [view, setView] = useState<JobView<FindResult> | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState("");
  const [rows, setRows] = useState<InstalledRow[]>([]);
  const [said, setSaid] = useState("");
  // An example that fits this bot (its goal or kind), from the tour's words.
  const [example, setExample] = useState(FIND_EXAMPLE);
  const [plan, setPlan] = useState<AssemblePlanRow[]>([]);
  // The job she was finding something for ("Find something for this").
  const [cap, setCap] = useState<string | undefined>(undefined);

  useEffect(() => {
    void getAssembleStatus().then((r) => {
      if (r.ok) setPlan(r.data.plan);
    });
  }, []);

  useEffect(() => {
    void getTour().then((t) => {
      if (t?.find_example) setExample(t.find_example);
    });
  }, []);

  const loadInstalled = useCallback(async () => {
    const reply = await getInstalled();
    if (reply.ok) setRows(reply.data.installed || []);
  }, []);

  useEffect(() => {
    void loadInstalled();
  }, [loadInstalled]);

  // The welcome tour's "Find more" box and "Add a tool" send her words, or a
  // pasted link, here (?find=... from another page, FIND_EVENT on this one).
  // "Find something for this" on the home page adds &cap=<capability>, so
  // what she adds from this search covers that job.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const asked = params.get("find");
    const cap = params.get("cap") || undefined;
    if (asked && asked.trim()) {
      setWords(asked.slice(0, 300));
      void look(asked.slice(0, 300), cap);
    }
    const onFind = (e: Event) => {
      const said = String((e as CustomEvent).detail || "").slice(0, 300);
      if (!said.trim()) return;
      setWords(said);
      void look(said);
    };
    window.addEventListener(FIND_EVENT, onFind);
    return () => window.removeEventListener(FIND_EVENT, onFind);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function look(said?: string, cap?: string) {
    setBusy(true);
    setProblem("");
    setSaid("");
    setView(null);
    setCap(cap);
    const reply = await startFind((said ?? words).trim(), undefined, cap);
    if (!reply.ok || !reply.data.job) {
      setBusy(false);
      setProblem(reply.status === 0 ? "No answer from this computer." : reply.data.message || "That did not work.");
      return;
    }
    await followJob<FindResult>(reply.data.job, setView);
    setBusy(false);
  }

  async function takeOut(entry: string) {
    const reply = await undoInstalled(entry);
    setSaid(reply.data?.line || "");
    await loadInstalled();
  }

  return (
    <div id="find-skills" style={{ display: "grid", gap: 10, scrollMarginTop: 16 }}>
      <SectionBar title="Skills, tools and apps" />
      <p style={small}>
        Search GitHub, the skill shelves and the tool lists for skills, tools and apps other people built.
        Nothing leaves your computer without your consent.
      </p>
      <form
        className="flex flex-wrap gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (words.trim()) void look();
        }}
      >
        <input
          aria-label="What should your bot be able to do?"
          placeholder={`What should your bot be able to do? ${example}`}
          value={words}
          maxLength={300}
          onChange={(e) => setWords(e.target.value)}
          style={{ ...inputStyle, flex: "1 1 280px" }}
        />
        <Button label="Look" color="info" roundedFull disabled={busy || !words.trim()} type="submit" />
      </form>
      {problem && (
        <Panel accent="--lemon">
          <p style={small}>{problem}</p>
        </Panel>
      )}
      {view && !view.done && (
        <div role="status" aria-live="polite">
          {view.lines.map((l, i) => (
            <p key={`${i}-${l}`} style={small}>
              {l}
            </p>
          ))}
        </div>
      )}
      {view?.done && view.error && <p style={small}>{view.error}</p>}
      {view?.done && view.result && (
        <FoundCards
          result={view.result}
          have={new Set(rows.map((r) => r.id).filter(Boolean))}
          plan={plan}
          cap={cap}
          onAdded={() => void loadInstalled()}
        />
      )}
      {plan.length > 0 && (
        <div style={{ display: "grid", gap: 4 }}>
          <p style={{ ...small, fontWeight: 700, color: "var(--text)" }}>What BuildBot does for each job</p>
          {plan.map((p) => (
            <p key={p.cap} className="flex flex-wrap items-center gap-2" style={small}>
              <span style={{ fontWeight: 700, color: "var(--text)" }}>{p.name}</span>
              <HowChip how={p.how} />
              {p.how === "not_found" && p.reason ? <span>{p.reason}</span> : null}
            </p>
          ))}
        </div>
      )}
      {rows.length > 0 && (
        <div style={{ display: "grid", gap: 6 }}>
          <p style={{ ...small, fontWeight: 700, color: "var(--text)" }}>Added from these finds</p>
          {rows.map((r) => (
            <div key={r.entry} className="flex flex-wrap items-center gap-2">
              <p style={{ ...small, flex: "1 1 240px" }}>{r.line}</p>
              <Button label="Take it out" color="white" roundedFull onClick={() => void takeOut(r.entry)} />
            </div>
          ))}
        </div>
      )}
      {said && <p style={small}>{said}</p>}
    </div>
  );
}
