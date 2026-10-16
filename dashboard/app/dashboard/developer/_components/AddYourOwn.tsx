"use client";

/**
 * "Add your own": four cards at the top of the Developer page for things she
 * already has.
 *
 *  1. A folder of my code: POST /build/project {path}. Read like a GitHub
 *     project and run where it is (BuildBot's own files go in its .buildbot
 *     folder); it joins a job and waits for her one Yes on the job page.
 *  2. A program on this computer: POST /programs (a command line program or
 *     one with a window), its commands, "Always allow this program", Remove.
 *  3. A desktop app: picked from GET /programs installed_desktop, then Open.
 *  4. A tool server: the existing "Add my own tool" on the Brain page.
 *
 * Cards 2 and 3 say "Coming soon" while /programs is not in this build (404).
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import MsIcon from "@/app/_components/MsIcon";
import FolderPicker from "@/app/_components/VaultPlace/FolderPicker";
import { addProject, whereProjectGoes, type HerOrder } from "@/app/_lib/services/buildService";
import { FREDOKA, JBM, NUNITO, Pill, inputStyle, microLabel, panelStyle, wellStyle } from "@/app/dashboard/apps/_components/ui";
import {
  addProgram,
  commandWords,
  launchProgram,
  listPrograms,
  removeProgram,
  setAlwaysAllow,
  type DesktopApp,
  type Program,
} from "../addYourOwnService";

const small: React.CSSProperties = { margin: 0, fontFamily: NUNITO, fontSize: 13, lineHeight: 1.45, color: "var(--text2)" };
const NO_ANSWER = "No answer from this computer. Nothing was added.";

function Card({
  icon,
  color,
  title,
  happens,
  children,
}: {
  icon: string;
  color: string;
  title: string;
  happens: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ ...panelStyle, display: "grid", gap: 10, alignContent: "start" }} data-own-card={title}>
      <div className="flex items-center gap-2">
        <MsIcon name={icon} size={22} style={{ color }} />
        <div style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 19, lineHeight: 1.1 }}>{title}</div>
      </div>
      <p style={small}>
        <span style={{ ...microLabel, marginRight: 6 }}>What happens</span>
        {happens}
      </p>
      {children}
    </div>
  );
}

function Soon() {
  return <p style={{ ...small, fontWeight: 700, color: "var(--text3)" }}>Coming soon.</p>;
}

function FolderCard() {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [fits, setFits] = useState<{ id: string; name: string } | null>(null);
  const [line, setLine] = useState("");
  const [page, setPage] = useState("");

  async function add(job?: string, order: HerOrder = "first") {
    setBusy(true);
    setLine("");
    setPage("");
    const ask = { url: "", path: path.trim(), name: "", what: "" };
    let target = job;
    if (!target) {
      const where = await whereProjectGoes(ask);
      if (!where.ok) {
        setBusy(false);
        setLine(where.message || NO_ANSWER);
        return;
      }
      if (where.data.fits) {
        setFits(where.data.fits);
        setBusy(false);
        return;
      }
      target = "new";
    }
    const reply = await addProject(ask, target, order);
    setBusy(false);
    setFits(null);
    setLine(reply.ok ? reply.data.line : reply.message || "That was not accepted. Nothing was added.");
    if (reply.ok) setPage(reply.data.page);
  }

  return (
    <Card
      icon="folder_code"
      color="var(--mint)"
      title="A folder of my code"
      happens="Your bot reads its README and run files, then runs it right there in your folder, in a job that waits for your Yes. Its own notes go in a .buildbot folder inside it."
    >
      <FolderPicker
        id="own-code-folder"
        value={path}
        onChange={setPath}
        networkLine="Pick a folder on this computer."
        placeholder="C:\Users\me\code\my-app"
        disabled={busy}
      />
      {fits ? (
        <div style={{ ...wellStyle, display: "grid", gap: 8 }}>
          <p style={small}>It fits the job {fits.name}.</p>
          <div className="flex flex-wrap gap-2">
            <Pill tone="accent" disabled={busy} onClick={() => void add(fits.id, "first")}>Run it first</Pill>
            <Pill disabled={busy} onClick={() => void add(fits.id, "list")}>Add to the list</Pill>
            <Pill disabled={busy} onClick={() => void add("new")}>Its own job</Pill>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <Pill tone="accent" disabled={busy || !path.trim()} onClick={() => void add()}>
            {busy ? "Reading it" : "Add this folder"}
          </Pill>
        </div>
      )}
      {line && <p style={{ ...small, color: "var(--text)" }}>{line}</p>}
      {page && (
        <Link href={page} style={{ ...small, fontWeight: 700, color: "var(--text)" }}>
          Open the job to give your Yes
        </Link>
      )}
    </Card>
  );
}

function ProgramRow({ p, onChange }: { p: Program; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");

  async function act(fn: () => Promise<{ ok: boolean; message: string }>, done?: string) {
    setBusy(true);
    const r = await fn();
    setBusy(false);
    setSaid(r.ok ? done || r.message : r.message || "That did not work. Nothing was changed.");
    if (r.ok) onChange();
  }

  return (
    <div style={{ ...wellStyle, display: "grid", gap: 6 }}>
      <div className="flex flex-wrap items-center gap-2">
        <div style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 15, flex: 1 }}>{p.name}</div>
        <span style={{ ...microLabel }}>{p.kind === "desktop" ? "Has a window" : "Command line"}</span>
      </div>
      <div style={{ fontFamily: JBM, fontSize: 12, color: "var(--text3)", overflowWrap: "anywhere" }}>{p.path || p.command}</div>
      {p.commands.length > 0 && (
        <ul style={{ margin: 0, paddingLeft: 18, ...small }}>
          {p.commands.slice(0, 12).map((c, i) => (
            <li key={i} style={{ fontFamily: JBM, fontSize: 12 }}>
              {commandWords(c)}
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-2" style={{ ...small, color: "var(--text)", cursor: "pointer" }}>
          <input
            type="checkbox"
            role="switch"
            checked={p.always_allow}
            disabled={busy}
            onChange={(e) => void act(() => setAlwaysAllow(p.id, e.target.checked))}
          />
          Always allow this program
        </label>
        <div className="flex-1" />
        {p.kind === "desktop" && (
          <Pill disabled={busy} onClick={() => void act(() => launchProgram(p.id), `Opening ${p.name}.`)}>Open</Pill>
        )}
        <Pill disabled={busy} onClick={() => void act(() => removeProgram(p.id), `Removed ${p.name}.`)}>Remove</Pill>
      </div>
      {said && <p style={small}>{said}</p>}
    </div>
  );
}

function ProgramCard({ programs, missing, reload }: { programs: Program[]; missing: boolean; reload: () => void }) {
  const [what, setWhat] = useState("");
  const [kind, setKind] = useState<"cli" | "desktop">("cli");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");
  const mine = programs.filter((p) => p.kind === "cli");

  async function add() {
    const v = what.trim();
    const looksLikePath = /[\\/]/.test(v);
    setBusy(true);
    const r = await addProgram({ kind, ...(looksLikePath ? { path: v } : { command: v }) });
    setBusy(false);
    setSaid(r.ok ? `Added ${r.data?.name || v}.${kind === "desktop" ? " It is under A desktop app." : ""}` : r.missing ? "" : r.message || "That was not accepted. Nothing was added.");
    if (r.ok) {
      setWhat("");
      reload();
    }
  }

  return (
    <Card
      icon="terminal"
      color="var(--sky)"
      title="A program on this computer"
      happens="Your bot reads the program's help, lists what it can do, and asks before each run unless you switch on Always allow."
    >
      {missing ? (
        <Soon />
      ) : (
        <>
          <input
            value={what}
            onChange={(e) => setWhat(e.target.value)}
            placeholder="ffmpeg, or C:\Tools\mytool.exe"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            style={{ ...inputStyle, width: "100%" }}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Pill tone={kind === "cli" ? "active" : "quiet"} onClick={() => setKind("cli")}>Command line</Pill>
            <Pill tone={kind === "desktop" ? "active" : "quiet"} onClick={() => setKind("desktop")}>Has a window</Pill>
            <div className="flex-1" />
            <Pill tone="accent" disabled={busy || !what.trim()} onClick={() => void add()}>Add</Pill>
          </div>
          {said && <p style={small}>{said}</p>}
          {mine.map((p) => (
            <ProgramRow key={p.id} p={p} onChange={reload} />
          ))}
        </>
      )}
    </Card>
  );
}

function DesktopCard({
  installed,
  programs,
  missing,
  reload,
}: {
  installed: DesktopApp[];
  programs: Program[];
  missing: boolean;
  reload: () => void;
}) {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState("");
  const [said, setSaid] = useState("");
  const added = programs.filter((p) => p.kind === "desktop");
  const have = new Set(added.map((p) => p.path.toLowerCase()));
  const words = q.trim().toLowerCase();
  const shown = installed
    .filter((a) => !have.has(a.path.toLowerCase()) && (!words || a.name.toLowerCase().includes(words)))
    .slice(0, 8);

  async function add(a: DesktopApp) {
    setBusy(a.path);
    const r = await addProgram({ kind: "desktop", path: a.path, name: a.name });
    setBusy("");
    setSaid(r.ok ? `Added ${a.name}.` : r.message || "That was not accepted. Nothing was added.");
    if (r.ok) reload();
  }

  async function open(p: Program) {
    setBusy(p.id);
    const r = await launchProgram(p.id);
    setBusy("");
    setSaid(r.ok ? `Opening ${p.name}.` : r.message || `${p.name} did not open.`);
  }

  return (
    <Card
      icon="desktop_windows"
      color="var(--magenta)"
      title="A desktop app"
      happens="Pick an app you already have, like Word or Excel. Your bot can open it, and open your files in it."
    >
      {missing ? (
        <Soon />
      ) : (
        <>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search your apps"
            autoComplete="off"
            spellCheck={false}
            style={{ ...inputStyle, width: "100%" }}
          />
          {shown.length > 0 && (
            <div style={{ display: "grid", gap: 4 }}>
              {shown.map((a) => (
                <div key={a.path} className="flex items-center gap-2" style={{ ...small, color: "var(--text)" }}>
                  <span style={{ flex: 1, overflowWrap: "anywhere" }}>{a.name}</span>
                  <Pill disabled={!!busy} onClick={() => void add(a)}>Add</Pill>
                </div>
              ))}
            </div>
          )}
          {installed.length === 0 && <p style={small}>No apps were found on this computer yet.</p>}
          {added.map((p) => (
            <div key={p.id} className="flex items-center gap-2" style={{ ...wellStyle, padding: "8px 12px" }}>
              <span style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 14, flex: 1 }}>{p.name}</span>
              <Pill tone="accent" disabled={!!busy} onClick={() => void open(p)}>Open</Pill>
              <Pill
                disabled={!!busy}
                onClick={() =>
                  void removeProgram(p.id).then((r) => {
                    setSaid(r.ok ? `Removed ${p.name}.` : r.message || "Nothing was changed.");
                    if (r.ok) reload();
                  })
                }
              >
                Remove
              </Pill>
            </div>
          ))}
          {said && <p style={small}>{said}</p>}
        </>
      )}
    </Card>
  );
}

function ToolServerCard() {
  return (
    <Card
      icon="hub"
      color="var(--lemon)"
      title="A tool server"
      happens="Give the address of a tool server you run or use. Your bot shows its tools and asks before adding them."
    >
      <Link
        href="/dashboard/developer/brain#add-own-tool"
        className="flex items-center gap-1"
        style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 14, color: "var(--text)", textDecoration: "none" }}
      >
        Add my own tool <MsIcon name="arrow_forward" size={18} />
      </Link>
    </Card>
  );
}

export default function AddYourOwn() {
  const [programs, setPrograms] = useState<Program[]>([]);
  const [installed, setInstalled] = useState<DesktopApp[]>([]);
  const [missing, setMissing] = useState(false);

  async function reload() {
    const r = await listPrograms();
    setMissing(r.missing);
    setPrograms(r.data.programs);
    setInstalled(r.data.installed_desktop);
  }

  useEffect(() => {
    void reload();
  }, []);

  return (
    <section id="add-your-own" style={{ display: "grid", gap: 12 }} data-walk="developer-add-own">
      <div>
        <div style={{ fontFamily: FREDOKA, fontWeight: 600, fontSize: 24 }}>Add your own</div>
        <p style={small}>Things you already have: your code, your programs, your apps, your tool servers.</p>
      </div>
      <div className="grid gap-4" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))" }}>
        <FolderCard />
        <ProgramCard programs={programs} missing={missing} reload={() => void reload()} />
        <DesktopCard installed={installed} programs={programs} missing={missing} reload={() => void reload()} />
        <ToolServerCard />
      </div>
    </section>
  );
}
