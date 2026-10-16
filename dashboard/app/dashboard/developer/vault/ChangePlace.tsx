"use client";

/**
 * Developer, Zone 1: "Change where the vault lives".
 *
 *   Move my vault         copy it, prove the copy opens with the PIN, switch,
 *                         keep the old copy (POST /vault/move)
 *   Use another vault     a BuildBot vault already in another folder: its PIN,
 *                         and the PIN of the vault in use now
 *                         (POST /vault/location, choice "existing")
 *   New vault elsewhere   only while no vault is made yet
 *
 * Paired devices only, and the PIN for a move or a switch: the backend checks
 * both and says why not. PINs live in this component's state only and are
 * cleared the moment they are sent. No PIN is ever shown back.
 */

import { useEffect, useState } from "react";
import { Panel, PanelTitle } from "@/app/dashboard/apps/_components/ui";
import FolderPicker from "@/app/_components/VaultPlace/FolderPicker";
import { Choice, Notice, PuffButton, inputStyle } from "@/app/onboarding/ui";
import {
  moveVault,
  setVaultLocation,
  type PlaceReply,
  type VaultLocation,
} from "@/app/_lib/services/vaultPlaceService";
import { small } from "../_components/parts";

type Action = "move" | "use" | "new";

const LABEL = { ...small, display: "block", fontWeight: 700, color: "var(--text)", margin: "14px 0 4px" };

export default function ChangePlace({ place, onChanged }: { place: VaultLocation; onChanged: () => void }) {
  const made = !!place.made;
  const [action, setAction] = useState<Action>(made ? "move" : "new");
  const [folder, setFolder] = useState("");
  const [pin, setPin] = useState("");
  const [currentPin, setCurrentPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState<PlaceReply | null>(null);
  const [done, setDone] = useState<string[]>([]);

  useEffect(
    () => () => {
      setPin("");
      setCurrentPin("");
    },
    [],
  );

  if (!place.paired) {
    return (
      <Panel>
        <PanelTitle>Change where the vault lives</PanelTitle>
        <p style={small}>Pair this device first. Where your vault lives can only be changed from a paired device.</p>
      </Panel>
    );
  }

  async function go(makeSubfolder = false, chosen = folder) {
    setBusy(true);
    setDone([]);
    let sent: PlaceReply;
    if (action === "move") sent = await moveVault(chosen.trim(), pin, makeSubfolder);
    else if (action === "use")
      sent = await setVaultLocation({ choice: "existing", folder: chosen.trim(), pin, currentPin: made ? currentPin : undefined, makeSubfolder });
    else sent = await setVaultLocation({ choice: "folder", folder: chosen.trim(), makeSubfolder });
    setBusy(false);
    if (sent.code !== "bare_drive") {
      setPin("");
      setCurrentPin("");
    }
    if (sent.ok) {
      setReply(null);
      setFolder("");
      setDone(sent.lines ?? []);
      onChanged();
      return;
    }
    setReply(sent);
  }

  const needsPin = action !== "new";
  const ready = !busy && folder.trim() && (!needsPin || pin) && (action !== "use" || !made || currentPin);

  return (
    <Panel>
      <PanelTitle>Change where the vault lives</PanelTitle>
      <p style={small}>{place.encrypted_drive_line}</p>
      <p style={{ ...small, marginTop: 6 }}>{place.not_a_vault_line}</p>
      {made && (
        <Choice
          name="vault-place-action"
          value="move"
          checked={action === "move"}
          onSelect={() => setAction("move")}
          title="Move my vault to another folder"
          description="Your vault is copied there. The copy has to open with your PIN before anything is switched, and the old copy is kept for you to delete yourself."
        />
      )}
      <Choice
        name="vault-place-action"
        value="use"
        checked={action === "use"}
        onSelect={() => setAction("use")}
        title="Use the BuildBot vault in another folder"
        description="A folder that already holds a BuildBot vault. Nothing is copied, and the vault you use now is left as it is."
      />
      {!made && (
        <Choice
          name="vault-place-action"
          value="new"
          checked={action === "new"}
          onSelect={() => setAction("new")}
          title="Make my new vault in another folder"
          description="No vault is made yet. Set your PIN on the Vault page afterwards."
        />
      )}

      <label htmlFor="vault-place-folder" style={LABEL}>
        {action === "use" ? "The folder that holds the vault" : "The folder"}
      </label>
      <FolderPicker id="vault-place-folder" value={folder} onChange={setFolder} networkLine={place.network_line || "It must be reachable whenever BuildBot starts; if it is not, BuildBot will say so and will not make a second vault."} disabled={busy} />

      {needsPin && (
        <>
          <label htmlFor="vault-place-pin" style={LABEL}>
            {action === "use" ? "That vault's PIN" : "Your vault PIN"}
          </label>
          <input id="vault-place-pin" type="password" value={pin} onChange={(e) => setPin(e.target.value)} autoComplete="current-password" style={inputStyle} />
        </>
      )}
      {action === "use" && made && (
        <>
          <label htmlFor="vault-place-current-pin" style={LABEL}>
            The PIN of the vault you use now
          </label>
          <input
            id="vault-place-current-pin"
            type="password"
            value={currentPin}
            onChange={(e) => setCurrentPin(e.target.value)}
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
                disabled={busy || (needsPin && !pin)}
                onClick={() => {
                  setFolder(reply.suggestion!);
                  void go(true, reply.suggestion!);
                }}
              />
            </span>
          )}
        </Notice>
      )}
      {done.length > 0 && (
        <Notice tone="ok">
          {done.map((line) => (
            <span key={line} style={{ display: "block" }}>
              {line}
            </span>
          ))}
        </Notice>
      )}
      <div className="flex flex-wrap gap-2" style={{ marginTop: 14 }}>
        <PuffButton
          kind="primary"
          label={busy ? "Working ..." : action === "move" ? "Move my vault" : action === "use" ? "Use that vault" : "Save the folder"}
          disabled={!ready}
          onClick={() => void go()}
        />
      </div>
    </Panel>
  );
}
