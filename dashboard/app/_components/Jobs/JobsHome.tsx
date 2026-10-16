"use client";

/**
 * The Build jobs tile on the home page: the bot's jobs, one line each (its
 * name, its status in the answer's own words, why not yet, Open), the setup
 * list and her one Yes while it waits, and "Not used" folded at the bottom
 * with Remove for what is on this computer. Chat and Find more are tiles of
 * their own next to it.
 *
 * Backend: GET /build/status (src/puffpai/build/), GET /things for which
 * "Not used" finds can be removed, DELETE /things/<id> to remove one.
 *
 * puff puff pass
 */

import { useCallback, useEffect, useState } from "react";
import { FREDOKA, NUNITO } from "@/app/dashboard/apps/_components/ui";
import { useBuildStatus } from "@/app/_lib/hooks/useBuildStatus";
import { listThings, removeThing, type Thing } from "@/app/_lib/services/myThingsService";
import { JobRows, NotUsed, small } from "./BuildViews";
import { thingForRef } from "./jobParts";
import YesPanel from "./YesPanel";
import CloudCheckBanner from "./CloudCheckBanner";
import VaultWaitUnlock from "./VaultWaitUnlock";
import { hasBuildable, openRows } from "@/app/_lib/buildJobs";

export default function JobsHome({ botName }: { botName: string }) {
  const { status, failed, reload } = useBuildStatus();
  const [things, setThings] = useState<Thing[]>([]);
  const [busy, setBusy] = useState("");
  const [confirming, setConfirming] = useState("");
  const [said, setSaid] = useState("");

  const loadThings = useCallback(async () => {
    const r = await listThings();
    if (r.ok && Array.isArray(r.data)) setThings(r.data);
  }, []);

  useEffect(() => {
    void loadThings();
  }, [loadThings]);

  const thingOf = (ref: string) =>
    thingForRef(ref, status?.dropped.find((d) => d.ref === ref)?.name, things)?.id || "";

  async function remove(ref: string) {
    if (confirming !== ref) {
      setConfirming(ref);
      return;
    }
    setConfirming("");
    const tid = thingOf(ref);
    if (!tid) return;
    setBusy(ref);
    const r = await removeThing(tid);
    setBusy("");
    setSaid(r.ok ? "" : r.data.message || "It could not be taken out.");
    await loadThings();
    await reload();
  }

  if (failed && !status) {
    return <p style={small}>{botName}&apos;s jobs could not be read right now.</p>;
  }
  if (!status) {
    return <p style={{ ...small, padding: "8px 0 16px" }}>Opening...</p>;
  }
  if (!status.ready) {
    return <p style={{ ...small, padding: "8px 0 16px" }}>{status.line || "Your bot is getting ready."}</p>;
  }
  if (status.jobs.length === 0) {
    return <p style={small}>{botName} has no build jobs yet. Tell it what you want in Chat.</p>;
  }

  return (
    <section style={{ display: "grid", gap: 12 }} aria-label={`${botName} build jobs`}>
      <CloudCheckBanner jobs={status.jobs} onDone={reload} />
      <VaultWaitUnlock line={status.vaultWait} onOpened={reload} />
      <JobRows jobs={status.jobs} words={status.words} />

      {hasBuildable(status) && (!status.yes.given || openRows(status.setup, true).length > 0) ? (
        <section
          aria-label="Set up"
          style={{ border: "1px solid var(--line)", borderRadius: 18, background: "var(--card)", padding: "12px 18px" }}
        >
          <h3 style={{ margin: "0 0 4px", fontFamily: FREDOKA, fontWeight: 600, fontSize: 17, color: "var(--text)" }}>
            {status.yes.given ? "Still to set up" : "Set up, then say yes"}
          </h3>
          <YesPanel status={status} reload={reload} showLines={false} />
        </section>
      ) : null}

      <NotUsed
        dropped={status.dropped}
        removable={(ref) => Boolean(thingOf(ref))}
        onRemove={(ref) => void remove(ref)}
        busy={busy}
        confirming={confirming}
      />
      {said ? <p style={small}>{said}</p> : null}
    </section>
  );
}
