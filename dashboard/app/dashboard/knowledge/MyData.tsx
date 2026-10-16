"use client";

/**
 * Your files: the data folder, folders you link, and adding many files or a
 * whole folder at once (src/puffpai/knowledge/my_data.py).
 *
 * Everything added lands in "My files" inside the data folder. Linked folders
 * are read where they are, never copied or changed. Your bot looks at all of
 * them every little while and learns what is new; keys, passwords and
 * personal details go to your vault and the searchable copy says
 * "[held in vault]". The counts and lines here are the backend's.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useRef, useState, type CSSProperties } from "react";
import DataFolderChooser from "@/app/_components/MyData/DataFolderChooser";
import FolderPicker from "@/app/_components/VaultPlace/FolderPicker";
import { openFile } from "@/app/dashboard/developer/addYourOwnService";
import { Notice, PuffButton } from "@/app/onboarding/ui";
import {
  getMyData,
  linkFolder,
  scanNow,
  unlinkFolder,
  uploadMyFiles,
  uploadSummary,
  type DataFolderRow,
  type MyDataStatus,
} from "@/app/_lib/services/myDataService";

const panel: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  padding: "20px 24px",
  boxShadow: "var(--shadow)",
  marginBottom: 20,
};
const H2: CSSProperties = { color: "var(--text)", fontSize: 18, fontWeight: 800, margin: "0 0 6px" };
const H3: CSSProperties = { color: "var(--text)", fontSize: 16, fontWeight: 800, margin: "18px 0 4px" };
const QUIET: CSSProperties = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "4px 0" };
const ROW: CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: 8,
  alignItems: "center",
  justifyContent: "space-between",
  padding: "8px 12px",
  borderRadius: 14,
  border: "1px solid var(--line)",
  marginTop: 6,
};

function counts(row: DataFolderRow): string {
  const bits = [`${row.indexed} file${row.indexed === 1 ? "" : "s"} learned`];
  if (row.waiting) bits.push(`${row.waiting} waiting for the vault`);
  if (row.too_big) bits.push(`${row.too_big} too big`);
  if (row.unreadable) bits.push(`${row.unreadable} could not be read`);
  if (!row.exists) bits.push("not there right now (a drive that is unplugged?)");
  return bits.join(", ");
}

function when(iso: string): string {
  if (!iso) return "not yet";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function MyData() {
  const [status, setStatus] = useState<MyDataStatus | null>(null);
  const [openSoon, setOpenSoon] = useState(false);
  const [linking, setLinking] = useState(false);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [problem, setProblem] = useState("");
  const files = useRef<HTMLInputElement>(null);
  const folder = useRef<HTMLInputElement | null>(null);

  const load = useCallback(async () => setStatus(await getMyData()), []);
  useEffect(() => {
    void load();
    const t = setInterval(() => void load(), 15000);
    return () => clearInterval(t);
  }, [load]);

  async function link() {
    setBusy(true);
    setProblem("");
    const reply = await linkFolder(path.trim());
    setBusy(false);
    if (!reply.ok) {
      setProblem(reply.message || "That folder could not be linked.");
      return;
    }
    setLinking(false);
    setPath("");
    setNote("Linked. Your bot reads it where it is and never changes it.");
    await load();
  }

  async function unlink(row: DataFolderRow) {
    setBusy(true);
    const reply = await unlinkFolder(row.id);
    setBusy(false);
    if (!reply.ok) {
      setProblem(reply.message || "That folder could not be unlinked.");
      return;
    }
    setNote(`Unlinked ${row.name}. Its files are out of what your bot knows; the folder itself was not touched.`);
    await load();
  }

  async function upload(list: FileList | null) {
    if (!list || list.length === 0) return;
    setBusy(true);
    setProblem("");
    setNote(`Adding ${list.length} file${list.length === 1 ? "" : "s"}...`);
    const reply = await uploadMyFiles(Array.from(list));
    setBusy(false);
    setNote(uploadSummary(reply.results));
    if (!reply.ok) setProblem(reply.message || "Not everything was added.");
    if (files.current) files.current.value = "";
    if (folder.current) folder.current.value = "";
    await load();
  }

  const linked = (status?.folders ?? []).filter((f) => f.kind === "linked");
  const own = (status?.folders ?? []).filter((f) => f.kind !== "linked");

  return (
    <section style={panel}>
      <h2 style={H2}>Your files</h2>
      <p style={QUIET}>
        Add your own files and folders and your bot learns from them by itself. Keys, passwords and personal details
        go to your vault; what it searches says &quot;[held in vault]&quot; in their place.
        Nothing leaves your computer without your consent.
      </p>

      <div style={{ marginTop: 14 }}>
        <DataFolderChooser status={status} onChanged={() => void load()} />
      </div>

      <h3 style={H3}>Add files</h3>
      <p style={QUIET}>They go into &quot;My files&quot; in your data folder. You can also drop files in that folder yourself.</p>
      <div className="flex flex-wrap gap-2" style={{ marginTop: 6 }} data-walk="knowledge-upload">
        <PuffButton label="Add files" onClick={() => files.current?.click()} disabled={busy} />
        <PuffButton label="Add a whole folder" onClick={() => folder.current?.click()} disabled={busy} />
      </div>
      <input ref={files} type="file" multiple hidden onChange={(e) => void upload(e.target.files)} />
      <input
        ref={(el) => {
          folder.current = el;
          if (el) {
            el.setAttribute("webkitdirectory", "");
            el.setAttribute("directory", "");
          }
        }}
        type="file"
        multiple
        hidden
        onChange={(e) => void upload(e.target.files)}
      />

      <h3 style={H3}>Linked folders</h3>
      <p style={QUIET}>A folder you already have, read where it is. It is never copied, moved or changed.</p>
      {linked.map((row) => (
        <div key={row.id} style={ROW}>
          <div>
            <div style={{ color: "var(--text)", fontWeight: 700 }}>{row.name}</div>
            <div style={{ ...QUIET, fontSize: 13, wordBreak: "break-all" }}>{row.path}</div>
            <div style={{ ...QUIET, fontSize: 13 }}>{counts(row)}</div>
          </div>
          <PuffButton label="Unlink" onClick={() => void unlink(row)} disabled={busy} />
        </div>
      ))}
      {linking ? (
        <div style={{ marginTop: 8 }} data-walk="knowledge-link">
          <FolderPicker
            id="link-folder"
            value={path}
            onChange={setPath}
            networkLine="A network folder works while that computer is on."
            placeholder="C:\Users\me\Documents\Recipes"
            disabled={busy}
          />
          <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
            <PuffButton kind="primary" label="Link this folder" onClick={() => void link()} disabled={busy || !path.trim()} />
            <PuffButton label="Not now" onClick={() => setLinking(false)} disabled={busy} />
          </div>
        </div>
      ) : (
        <div style={{ marginTop: 8 }} data-walk="knowledge-link">
          <PuffButton label="Link a folder" onClick={() => setLinking(true)} disabled={busy} />
        </div>
      )}

      {(status?.recent_files?.length ?? 0) > 0 && (
        <>
          <h3 style={H3}>In My files</h3>
          {status?.recent_files?.map((f) => (
            <div key={f.path} style={ROW} data-my-file={f.name}>
              <div style={{ minWidth: 0 }}>
                <div style={{ color: "var(--text)", fontWeight: 700, overflowWrap: "anywhere" }}>{f.name}</div>
                <div style={{ ...QUIET, fontSize: 13 }}>
                  {f.folder ? `${f.folder}, ` : ""}
                  {when(f.modified)}
                </div>
              </div>
              <PuffButton
                label={openSoon ? "Open (coming soon)" : "Open"}
                disabled={busy || openSoon}
                onClick={() =>
                  void openFile(f.path).then((r) => {
                    if (r.missing) setOpenSoon(true);
                    else if (r.ok) setNote(`Opening ${f.name}.`);
                    else setProblem(r.message || `${f.name} did not open.`);
                  })
                }
              />
            </div>
          ))}
        </>
      )}

      <h3 style={H3}>What your bot has looked at</h3>
      {own.map((row) => (
        <div key={row.id} style={ROW}>
          <div>
            <div style={{ color: "var(--text)", fontWeight: 700 }}>
              {row.kind === "previous" ? `${row.name} (your earlier data folder)` : "Your data folder"}
            </div>
            <div style={{ ...QUIET, fontSize: 13 }}>{counts(row)}</div>
          </div>
        </div>
      ))}
      {status && (
        <p style={{ ...QUIET, fontSize: 13 }}>
          {status.auto_index
            ? `Looked last: ${when(status.last_scan)}. It looks again every ${status.scan_seconds} seconds.`
            : "Learning by itself is off in the settings, so new files are not looked at."}
          {status.indexing > 0 ? ` Learning ${status.indexing} file${status.indexing === 1 ? "" : "s"} now.` : ""}
          {` Files over ${status.max_file_mb} MB are left out.`}
        </p>
      )}
      {status?.waiting_line && <Notice tone="warn">{status.waiting_line}</Notice>}
      <div style={{ marginTop: 8 }}>
        <PuffButton label="Look again now" onClick={() => void scanNow().then(() => setTimeout(() => void load(), 3000))} disabled={busy} />
      </div>
      {problem && <Notice tone="warn">{problem}</Notice>}
      {note && !problem && <Notice tone="ok">{note}</Notice>}
    </section>
  );
}
