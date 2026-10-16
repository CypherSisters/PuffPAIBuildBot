"use client";

/**
 * "Finding things for your bot", right after
 * the goal and the name. BuildBot looks on the skill shelves, the tool shelf,
 * the code sites and the free libraries, shows one list grouped by her jobs
 * and by type with nothing ticked but at most two small free books, she taps, says yes, the install lines appear, and the plan
 * comes next. Every word comes from the backend (GET/POST /wizard/find).
 * Once the search is done, the jobs the bot must be able to do (its
 * capabilities, finding/capabilities.py) are listed above the finds.
 */

import { useEffect, useRef, useState } from "react";
import ThingsPicker from "@/app/_components/ThingsPicker";
import {
  followJob,
  getWizardFind,
  wizardFindDone,
  wizardInstall,
  type FindResult,
  type JobView,
  type WizardCapability,
} from "../_lib/services/thingsService";
import { PuffButton } from "./ui";

const H1 = { color: "var(--text)", fontSize: 24, fontWeight: 800, margin: 0, lineHeight: 1.25 };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "6px 0 8px" };
const LINE = { color: "var(--text2)", fontSize: 14, lineHeight: 1.6, margin: 0 };

export default function FindStep({ onDone }: { onDone: () => void }) {
  const [title, setTitle] = useState("Finding things for your bot");
  const [intro, setIntro] = useState("");
  const [view, setView] = useState<JobView<FindResult> | null>(null);
  const [error, setError] = useState("");
  const [caps, setCaps] = useState<WizardCapability[]>([]);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void (async () => {
      const reply = await getWizardFind();
      if (!reply.ok || !reply.data?.find) {
        setError(reply.status === 0 ? "No answer from this computer." : "This step could not be drawn.");
        return;
      }
      setTitle(reply.data.title);
      setIntro(reply.data.intro);
      setView(reply.data.find);
      setCaps(reply.data.capabilities || []);
      if (!reply.data.find.done) {
        await followJob<FindResult>(reply.data.find.job, setView);
        const after = await getWizardFind();
        if (after.ok) setCaps(after.data?.capabilities || []);
      }
    })();
  }, []);

  async function finish() {
    const reply = await wizardFindDone();
    if (!reply.ok) {
      setError(reply.data?.message || "That was not accepted. Try again.");
      return;
    }
    onDone();
  }

  async function startInstall(ids: string[]): Promise<string> {
    const reply = await wizardInstall(ids);
    return reply.ok && reply.data?.install ? reply.data.install.job : "";
  }

  return (
    <div>
      <h1 style={H1}>{title}</h1>
      {intro ? <p style={HELP}>{intro}</p> : null}
      {view && !view.done ? (
        <div role="status" aria-live="polite">
          {view.lines.map((l, i) => (
            <p key={`${i}-${l}`} style={LINE}>
              {l}
            </p>
          ))}
        </div>
      ) : null}
      {view?.done && caps.length > 0 ? (
        <div style={{ margin: "4px 0 12px" }}>
          <p style={{ ...LINE, fontWeight: 700, color: "var(--text)" }}>Your bot needs to be able to</p>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>
            {caps.map((c) => (
              <li key={c.id} style={LINE}>
                <span style={{ fontWeight: 700, color: "var(--text)" }}>{c.name}</span>
                {c.does ? `: ${c.does}` : ""}
                {c.covered ? ` (${c.line})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {view?.done && view.result ? (
        <ThingsPicker result={view.result} startInstall={startInstall} onFinished={() => void finish()} />
      ) : null}
      {view?.done && !view.result ? (
        <div>
          <p style={LINE}>{view.error || "Nothing could be looked for."}</p>
          <div style={{ marginTop: 14 }}>
            <PuffButton kind="primary" label="Next" onClick={() => void finish()} />
          </div>
        </div>
      ) : null}
      {error ? (
        <p role="alert" style={{ ...LINE, color: "var(--puff-red)" }}>
          {error}
        </p>
      ) : null}
    </div>
  );
}
