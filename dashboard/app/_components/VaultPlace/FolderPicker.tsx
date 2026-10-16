"use client";

/**
 * A folder for the vault: typed, or picked from a simple list of drives and
 * folders on the bot's computer (GET /vault/location/browse, paired devices
 * only). Folder names only: the list never shows a file.
 */

import { useState } from "react";
import { inputStyle, PuffButton } from "@/app/onboarding/ui";
import { browseFolders, looksLikeNetwork, type BrowseReply } from "@/app/_lib/services/vaultPlaceService";

const QUIET = { color: "var(--text3)", fontSize: 13, lineHeight: 1.5, margin: "6px 0 0" };
const ROW = {
  display: "block",
  width: "100%",
  textAlign: "left" as const,
  padding: "6px 10px",
  borderRadius: 10,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  fontSize: 14,
  marginTop: 4,
  cursor: "pointer",
};

export default function FolderPicker({
  id,
  value,
  onChange,
  networkLine,
  disabled,
  placeholder = "E:\\PuffPAI Vault",
}: {
  id: string;
  value: string;
  onChange: (folder: string) => void;
  networkLine: string;
  disabled?: boolean;
  placeholder?: string;
}) {
  const [list, setList] = useState<BrowseReply | null>(null);
  const [open, setOpen] = useState(false);
  const [problem, setProblem] = useState("");

  async function look(folder?: string) {
    setProblem("");
    const reply = await browseFolders(folder);
    if (!reply.ok) {
      setProblem("message" in reply ? reply.message ?? "" : "");
      return;
    }
    setList(reply as BrowseReply);
    setOpen(true);
  }

  return (
    <div>
      <div className="flex flex-wrap gap-2" style={{ alignItems: "center" }}>
        <input
          id={id}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          autoComplete="off"
          spellCheck={false}
          disabled={disabled}
          style={{ ...inputStyle, flex: 1, minWidth: 240 }}
        />
        <PuffButton label={open ? "Close the list" : "Choose"} onClick={() => (open ? setOpen(false) : void look(value || undefined))} disabled={disabled} />
      </div>
      {looksLikeNetwork(value) && <p style={QUIET}>{networkLine}</p>}
      {problem && <p style={{ ...QUIET, color: "var(--text2)" }}>{problem}</p>}
      {open && list && (
        <div style={{ marginTop: 8, padding: 10, borderRadius: 14, border: "1px solid var(--line)" }}>
          {list.folder ? (
            <>
              <p style={{ ...QUIET, margin: 0 }}>Inside {list.folder}</p>
              <div className="flex flex-wrap gap-2" style={{ marginTop: 6 }}>
                <PuffButton
                  kind="primary"
                  label="Use this folder"
                  onClick={() => {
                    onChange(list.folder);
                    setOpen(false);
                  }}
                />
                {list.parent ? <PuffButton label="Up one folder" onClick={() => void look(list.parent)} /> : null}
                <PuffButton label="Drives" onClick={() => void look()} />
              </div>
              {list.vault && <p style={QUIET}>This folder holds a BuildBot vault.</p>}
              {list.folders.length === 0 && <p style={QUIET}>No folders in here.</p>}
              {list.folders.map((row) => (
                <button key={row.path} type="button" style={ROW} onClick={() => void look(row.path)}>
                  {row.name}
                  {row.vault ? "  (a BuildBot vault)" : ""}
                </button>
              ))}
              {list.more && <p style={QUIET}>Only the first folders are listed. Type the rest of the path.</p>}
            </>
          ) : (
            <>
              <p style={{ ...QUIET, margin: 0 }}>Drives on your bot's computer</p>
              {list.drives.map((row) => (
                <button key={row.path} type="button" style={ROW} onClick={() => void look(row.path)}>
                  {row.name}
                  {row.network ? "  (network)" : ""}
                </button>
              ))}
              {list.home && (
                <button type="button" style={ROW} onClick={() => void look(list.home)}>
                  Your home folder
                </button>
              )}
              <p style={QUIET}>A network folder on your own server or NAS is not listed here: type it, like \\mynas\vault.</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
