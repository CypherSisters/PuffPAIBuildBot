"use client";

/**
 * "Who should do Toolsmith's installs?", asked once after the GitHub question
 * and before the build. This computer is the default (and what Skip keeps).
 * Anthropic opens the Settings card's own model picker, key box and consent
 * text (ToolsmithModelCard), which saves through /toolsmith/model: the key
 * goes once, in a POST body, into the vault. A locked vault asks for her PIN
 * inside that card and tries the save again.
 *
 * The step is recorded as "cloud" only once the key really saved. When a save
 * is refused (`onRefused`), the card shows why, her wish for Anthropic is
 * kept (/wizard/toolsmith answers 409 "not_saved" and does NOT mark the step
 * done), and the build later waits with "You picked Anthropic for Toolsmith,
 * but its key isn't saved yet." until she saves it or picks this computer.
 */

import { useState } from "react";
import Link from "next/link";
import ToolsmithModelCard from "../dashboard/settings/ToolsmithModelCard";
import { chooseLocal, getToolsmithModel } from "@/app/_lib/services/toolsmithModelService";
import { chooseToolsmith } from "../_lib/services/wizardService";
import { PuffButton } from "./ui";

const H1 = { color: "var(--text)", fontSize: 24, fontWeight: 800, margin: 0, lineHeight: 1.25 };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "4px 0 0" };

export default function ToolsmithStep({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"ask" | "cloud">("ask");
  const [busy, setBusy] = useState(false);
  // A save to Anthropic was refused: her wish is kept, the step is not done.
  const [refused, setRefused] = useState(false);

  async function answer(choice: "local" | "cloud" | "skipped") {
    setBusy(true);
    if (choice !== "cloud") {
      // Back to this computer only if Anthropic was on (Settings, or a retry).
      const now = await getToolsmithModel();
      if (now.data?.where === "cloud") await chooseLocal();
    }
    const r = await chooseToolsmith(choice);
    setBusy(false);
    if (choice === "cloud" && !r.ok && r.status !== 0) {
      // Not saved after all: the wish is kept and the step stays not done.
      setRefused(true);
      return;
    }
    onDone();
  }

  async function keepWish() {
    setRefused(true);
    // Records her wish for Anthropic; the answer is 409 "not_saved" on purpose.
    await chooseToolsmith("cloud");
  }

  return (
    <div data-wizard-step="toolsmith">
      <h1 style={H1}>Who should do Toolsmith&rsquo;s installs?</h1>
      <p style={HELP}>
        Toolsmith finds and installs the apps for your bot. Everything else stays on this computer. You can change this
        any time in <Link href="/dashboard/settings">Settings</Link>.
      </p>
      {mode === "ask" ? (
        <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
          <PuffButton
            kind="primary"
            label="This computer (private, slower)"
            onClick={() => void answer("local")}
            disabled={busy}
          />
          <PuffButton label="Anthropic (faster, your choice)" onClick={() => setMode("cloud")} disabled={busy} />
          <PuffButton label="Skip" onClick={() => void answer("skipped")} disabled={busy} />
        </div>
      ) : (
        <div style={{ marginTop: 14, display: "grid", gap: 14 }}>
          <ToolsmithModelCard
            inWizard
            onChosen={(where) => where === "cloud" && void answer("cloud")}
            onRefused={() => void keepWish()}
          />
          {refused ? (
            <>
              <p style={HELP}>
                Your key is not saved yet, so Toolsmith still uses this computer. You can go on now: BuildBot asks for the
                key again before Toolsmith&rsquo;s first install.
              </p>
              <div className="flex flex-wrap gap-2">
                <PuffButton label="Go on, save the key later" onClick={onDone} disabled={busy} />
                <PuffButton label="Use this computer instead" onClick={() => void answer("local")} disabled={busy} />
              </div>
            </>
          ) : (
            <div className="flex flex-wrap gap-2">
              <PuffButton label="Back" onClick={() => setMode("ask")} disabled={busy} />
              <PuffButton label="Skip" onClick={() => void answer("skipped")} disabled={busy} />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
