"use client";

/**
 * Her pictures folder for the Mood board: a box for the folder and one
 * button. Used on the home page tile and on the Mood board's own page.
 *
 * When the bot says no, she is told the bot's own reason ("Only folders
 * inside Pictures, Documents, ..." or "That folder is not there"), not a
 * blanket "could not be used".
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import { useState } from "react";
import { NUNITO } from "../Widgets/DesignTile";
import { apiPostRaw } from "@/app/_lib/api";
import { FOLDER_NO_ANSWER, folderReply } from "@/app/_lib/moodBoard";

export default function MoodBoardFolder({
  appId, label, onChosen,
}: { appId: string; label?: string; onChosen?: () => void }) {
  const [path, setPath] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const giveFolder = async () => {
    setBusy(true);
    setNote(null);
    try {
      const got = await apiPostRaw(`/integrations/${appId}/folder`, { path });
      setNote(folderReply(got.ok, got.data) || null);
      if (got.ok && onChosen) onChosen();
    } catch {
      setNote(FOLDER_NO_ANSWER);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="mt-2 flex flex-wrap gap-2">
        <input
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="Your pictures folder"
          aria-label={label || "Pictures folder"}
          className="min-w-[12rem] flex-1 rounded-[10px] border px-2 py-1"
          style={{ borderColor: "var(--line)", background: "var(--card)", color: "var(--text)" }}
        />
        <button type="button" disabled={busy || !path.trim()} onClick={giveFolder}
          className="rounded-full border px-3" style={{ borderColor: "var(--line)" }}>
          {label || "Choose a pictures folder"}
        </button>
      </div>
      {note && <p role="status" style={{ margin: "6px 0 0", font: `500 12px ${NUNITO}` }}>{note}</p>}
    </>
  );
}
