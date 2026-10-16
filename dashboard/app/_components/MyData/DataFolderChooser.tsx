"use client";

/**
 * "Where should BuildBot keep your knowledge and your files?"
 *
 * One folder for downloads and anything she adds (src/puffpai/knowledge/my_data.py).
 * Shows the folder in use, a Change button (type a path or pick one with the
 * vault step's own folder list) and the free space there. Every check is the
 * backend's (POST /knowledge/data-folder). Used by the setup plan and the
 * Knowledge page.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useCallback, useEffect, useState } from "react";
import FolderPicker from "@/app/_components/VaultPlace/FolderPicker";
import { Notice, PuffButton } from "@/app/onboarding/ui";
import { getMyData, setDataFolder, type MyDataStatus } from "@/app/_lib/services/myDataService";

export const DATA_FOLDER_QUESTION = "Where should BuildBot keep your knowledge and your files?";
const HELP = "Downloads and anything you add go here.";

const QUESTION = { color: "var(--text)", fontSize: 16, fontWeight: 800, margin: "0 0 4px" };
const QUIET = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "4px 0" };
const PATH = { fontFamily: "var(--font-jbmono), monospace", fontSize: 13, color: "var(--text)", wordBreak: "break-all" as const };

export default function DataFolderChooser({
  onChanged,
  status: given,
}: {
  onChanged?: () => void;
  status?: MyDataStatus | null;
}) {
  const [status, setStatus] = useState<MyDataStatus | null>(given ?? null);
  const [changing, setChanging] = useState(false);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [lines, setLines] = useState<string[]>([]);
  const [problem, setProblem] = useState("");

  const load = useCallback(async () => setStatus(await getMyData()), []);
  useEffect(() => {
    if (given !== undefined) setStatus(given);
    else void load();
  }, [given, load]);

  async function save() {
    setBusy(true);
    setProblem("");
    const reply = await setDataFolder(folder.trim());
    setBusy(false);
    if (!reply.ok) {
      setProblem(reply.message || "That folder could not be used.");
      return;
    }
    setLines(reply.lines ?? []);
    setChanging(false);
    setFolder("");
    await load();
    onChanged?.();
  }

  return (
    <div>
      <p style={QUESTION}>{DATA_FOLDER_QUESTION}</p>
      <p style={QUIET}>{HELP}</p>
      {status ? (
        <>
          <div style={PATH}>{status.data_dir}</div>
          <p style={{ ...QUIET, fontSize: 13 }}>{status.free_space}</p>
        </>
      ) : (
        <p style={QUIET}>Asking your bot where it keeps them...</p>
      )}
      {changing ? (
        <div style={{ marginTop: 8 }}>
          <FolderPicker
            id="data-folder"
            value={folder}
            onChange={setFolder}
            networkLine="A network folder works while that computer is on."
            placeholder="D:\BuildBot data"
            disabled={busy}
          />
          <div className="flex flex-wrap gap-2" style={{ marginTop: 8 }}>
            <PuffButton kind="primary" label="Use this folder" onClick={() => void save()} disabled={busy || !folder.trim()} />
            <PuffButton label="Keep it as it is" onClick={() => setChanging(false)} disabled={busy} />
          </div>
          <p style={{ ...QUIET, fontSize: 13 }}>
            Your old folder is kept as it was. Nothing is moved or deleted, and its files are still found.
          </p>
        </div>
      ) : (
        <div style={{ marginTop: 8 }}>
          <PuffButton label="Change" onClick={() => setChanging(true)} />
        </div>
      )}
      {problem && <Notice tone="warn">{problem}</Notice>}
      {lines.length > 0 && (
        <Notice tone="ok">
          {lines.map((l) => (
            <div key={l}>{l}</div>
          ))}
        </Notice>
      )}
    </div>
  );
}
