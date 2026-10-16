"use client";

/**
 * Old page, new home: a live card's own pages (/dashboard/apps/<feed id>,
 * /dashboard/things/feed-<id>) send you to the page of the job that uses it.
 * A card no job uses keeps its old page. `thingId` "" does nothing.
 */

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { getBuildStatus } from "../services/buildService";
import { jobForThing, jobHref } from "../buildJobs";

export { feedThingId } from "../buildJobs";

export function useJobRedirect(thingId: string): void {
  const router = useRouter();
  useEffect(() => {
    if (!thingId) return;
    let alive = true;
    void getBuildStatus().then((r) => {
      const job = jobForThing(r.data, thingId);
      if (alive && job) router.replace(jobHref(job.id));
    });
    return () => {
      alive = false;
    };
  }, [thingId, router]);
}

