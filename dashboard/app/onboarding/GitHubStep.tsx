"use client";

/**
 * "Do you have a GitHub account?", asked once right after the goal, before
 * the plan looks for things. Connect opens the shared
 * GitHubConnect flow; Skip is remembered on the backend so it is not asked
 * again. Never blocks: if the answer cannot be recorded, the wizard goes on.
 * Already connected (from the Developer page): not asked at all.
 */

import { useEffect, useState } from "react";
import GitHubConnect from "@/app/_components/GitHub/GitHubConnect";
import { getGitHubStatus } from "@/app/_lib/services/githubService";
import { chooseGitHub } from "../_lib/services/wizardService";
import { PuffButton } from "./ui";

const H1 = { color: "var(--text)", fontSize: 24, fontWeight: 800, margin: 0, lineHeight: 1.25 };
const HELP = { color: "var(--text2)", fontSize: 14, lineHeight: 1.5, margin: "4px 0 0" };

export default function GitHubStep({ onDone }: { onDone: () => void }) {
  const [mode, setMode] = useState<"checking" | "ask" | "connect" | "connected">("checking");
  const [busy, setBusy] = useState(false);

  async function answer(choice: "connected" | "skipped") {
    setBusy(true);
    await chooseGitHub(choice);
    setBusy(false);
    onDone();
  }

  useEffect(() => {
    let alive = true;
    void getGitHubStatus().then((r) => {
      if (!alive) return;
      if (r.ok && r.data.connected) void answer("connected");
      else setMode("ask");
    });
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (mode === "checking") return <p style={HELP}>Loading...</p>;
  return (
    <div data-wizard-step="github">
      <h1 style={H1}>Do you have a GitHub account?</h1>
      <p style={HELP}>Your bot searches better and can use your own projects.</p>
      {mode === "ask" ? (
        <div className="flex flex-wrap gap-2" style={{ marginTop: 16 }}>
          <PuffButton kind="primary" label="Connect" onClick={() => setMode("connect")} disabled={busy} />
          <PuffButton label="Skip" onClick={() => void answer("skipped")} disabled={busy} />
        </div>
      ) : (
        <div style={{ marginTop: 14, display: "grid", gap: 14 }}>
          <GitHubConnect autoStart onConnected={() => setMode("connected")} onCancel={() => setMode("ask")} />
          {mode === "connected" ? (
            <div>
              <PuffButton kind="primary" label="Next" onClick={() => void answer("connected")} disabled={busy} />
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
