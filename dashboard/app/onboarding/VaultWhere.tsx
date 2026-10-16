"use client";

/**
 * The vault step's first question, before the PIN:
 * "Where should your vault live?"
 *
 *   On this computer (recommended)   today's place
 *   On a drive or folder I choose    an external drive, a drive she already
 *                                    locks, or a folder on her own server or NAS
 *   I already have a BuildBot vault  that vault opens with ITS PIN; no new one
 *
 * The words come from the backend (/wizard/flow, the same as the terminal).
 * Every check is the backend's (POST /vault/location); this page only shows
 * what it said. A PIN typed here lives in this component's state only and is
 * cleared once it is sent.
 */

import { useEffect, useState } from "react";
import FolderPicker from "@/app/_components/VaultPlace/FolderPicker";
import { setVaultLocation, type PlaceChoice, type PlaceReply } from "@/app/_lib/services/vaultPlaceService";
import { Choice, Notice, PuffButton, inputStyle } from "./ui";

export interface WhereWords {
  question: string;
  choices: { value: PlaceChoice; label: string; help: string }[];
  encrypted_drive_line: string;
  network_line: string;
  not_a_vault_line: string;
}

const QUESTION = { color: "var(--text)", fontSize: 18, fontWeight: 700, margin: "18px 0 4px" };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "8px 0" };

export default function VaultWhere({
  words,
  onPlaced,
  onExisting,
}: {
  words: WhereWords;
  /** A place for a NEW vault was saved: go on to the PIN. */
  onPlaced: (lines: string[]) => void;
  /** A vault she already has is now in use: the vault step is done. */
  onExisting: (lines: string[]) => void;
}) {
  const [choice, setChoice] = useState<PlaceChoice>("here");
  const [folder, setFolder] = useState("");
  const [pin, setPin] = useState("");
  const [reply, setReply] = useState<PlaceReply | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => () => setPin(""), []);

  async function save(makeSubfolder = false, as: PlaceChoice = choice) {
    setBusy(true);
    const sent = await setVaultLocation({
      choice: as,
      folder: as === "here" ? undefined : folder.trim(),
      makeSubfolder,
      pin: as === "existing" ? pin : undefined,
    });
    setBusy(false);
    if (as === "existing") setPin("");
    if (sent.ok) {
      setReply(null);
      (as === "existing" ? onExisting : onPlaced)(sent.lines ?? []);
      return;
    }
    setReply(sent);
  }

  const needsFolder = choice !== "here";
  const ready = !busy && (!needsFolder || folder.trim()) && (choice !== "existing" || pin);

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) void save();
      }}
    >
      <p style={QUESTION}>{words.question}</p>
      {words.choices.map((c) => (
        <Choice
          key={c.value}
          name="vault-where"
          value={c.value}
          checked={choice === c.value}
          onSelect={(v) => {
            setChoice(v as PlaceChoice);
            setReply(null);
          }}
          title={c.label}
          description={c.help}
        />
      ))}
      {needsFolder && (
        <>
          <label htmlFor="vault-folder" style={{ ...QUESTION, display: "block" }}>
            {choice === "existing" ? "The folder your vault is in" : "The folder for your vault"}
          </label>
          <FolderPicker id="vault-folder" value={folder} onChange={setFolder} networkLine={words.network_line} disabled={busy} />
          <p style={HELP}>{words.encrypted_drive_line}</p>
        </>
      )}
      {choice === "existing" && (
        <>
          <label htmlFor="vault-existing-pin" style={{ ...QUESTION, display: "block" }}>
            That vault's PIN
          </label>
          <input
            id="vault-existing-pin"
            type="password"
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            autoComplete="current-password"
            style={inputStyle}
          />
        </>
      )}
      {reply?.message && (
        <Notice tone="warn">
          {reply.message}
          {reply.code === "bare_drive" && reply.suggestion && (
            <span style={{ display: "block", marginTop: 8 }}>
              <PuffButton
                kind="primary"
                label="Yes, make that folder"
                onClick={() => {
                  setFolder(reply.suggestion!);
                  void save(true);
                }}
              />
            </span>
          )}
          {reply.code === "has_vault" && (
            <span style={{ display: "block", marginTop: 8 }}>
              <PuffButton label="Use that vault" onClick={() => setChoice("existing")} />
            </span>
          )}
        </Notice>
      )}
      <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
        <PuffButton kind="primary" type="submit" label="Next" disabled={!ready} />
      </div>
    </form>
  );
}
