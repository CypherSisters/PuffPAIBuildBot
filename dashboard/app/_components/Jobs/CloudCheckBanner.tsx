"use client";

/**
 * "Check again with the cloud model": when Toolsmith's model is now Anthropic
 * (GET /toolsmith/model says cloud) and some jobs were built with the model
 * on this computer (the job's built_with is "local"), one line and one
 * button. "Check all again" sends the job's own Check again (POST
 * /build/check/<job> {"again": true}) for each such job, one after another.
 *
 * Shown on the bot page and on the Build jobs tile.
 */

import { useEffect, useState } from "react";
import { NUNITO } from "@/app/dashboard/apps/_components/ui";
import type { Job } from "@/app/_lib/buildJobs";
import { checkJobAgain } from "@/app/_lib/services/buildService";
import { getToolsmithModel } from "@/app/_lib/services/toolsmithModelService";

export default function CloudCheckBanner({ jobs, onDone }: { jobs: Job[]; onDone?: () => void | Promise<void> }) {
  const [cloud, setCloud] = useState(false);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState("");

  useEffect(() => {
    let live = true;
    void getToolsmithModel().then((r) => {
      if (live) setCloud(!!r.data && r.data.where === "cloud");
    });
    return () => {
      live = false;
    };
  }, []);

  const local = jobs.filter((j) => j.builtWith === "local" && j.status !== "building");
  if (!cloud || (local.length === 0 && !said)) return null;

  async function checkAll() {
    setBusy(true);
    let started = 0;
    for (const job of local) {
      const r = await checkJobAgain(job.id, true);
      if (r.ok) started += 1;
    }
    setBusy(false);
    setSaid(
      started === local.length
        ? `Checking ${started} ${started === 1 ? "job" : "jobs"} again with Anthropic.`
        : `${started} of ${local.length} jobs are being checked again; the others could not be started.`,
    );
    await onDone?.();
  }

  return (
    <section
      aria-label="Check again with the cloud model"
      style={{
        border: "1px solid var(--line)",
        borderRadius: 18,
        background: "var(--card)",
        padding: "10px 16px",
        display: "flex",
        gap: 12,
        alignItems: "center",
        flexWrap: "wrap",
        fontFamily: NUNITO,
        fontSize: 14,
      }}
    >
      <span style={{ flex: "1 1 260px" }}>
        {said ||
          "Toolsmith now uses Anthropic. These jobs were built with the model on this computer. Check them again?"}
        {!said ? (
          <span style={{ display: "block", color: "var(--text2)", fontSize: 13 }}>
            {local.map((j) => j.name).join(", ")}
          </span>
        ) : null}
      </span>
      {!said ? (
        <button
          type="button"
          onClick={() => void checkAll()}
          disabled={busy}
          style={{
            border: "1px solid var(--line)",
            borderRadius: 12,
            padding: "6px 14px",
            background: "var(--bg)",
            cursor: busy ? "default" : "pointer",
            fontFamily: NUNITO,
          }}
        >
          {busy ? "Starting..." : "Check all again"}
        </button>
      ) : null}
    </section>
  );
}
