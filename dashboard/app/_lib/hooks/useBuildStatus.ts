"use client";

/**
 * GET /build/status for a page: the jobs, the setup list and the team's
 * lines. Reads again every few seconds while the build runs, slowly
 * otherwise (or never, with `poll` off), and when the tab comes back.
 *
 * `status` null = not known yet (or no answer); `failed` says which.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getBuildStatus } from "../services/buildService";
import type { BuildStatus } from "../buildJobs";

const FAST_MS = 2500;
const SLOW_MS = 20_000;

export function useBuildStatus(poll = true): {
  status: BuildStatus | null;
  failed: boolean;
  reload: () => Promise<void>;
} {
  const [status, setStatus] = useState<BuildStatus | null>(null);
  const [failed, setFailed] = useState(false);
  const alive = useRef(true);

  const reload = useCallback(async () => {
    const r = await getBuildStatus();
    if (!alive.current) return;
    if (r.data) {
      setStatus(r.data);
      setFailed(false);
    } else {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    void reload();
    const back = () => {
      if (document.visibilityState === "visible") void reload();
    };
    document.addEventListener("visibilitychange", back);
    return () => {
      alive.current = false;
      document.removeEventListener("visibilitychange", back);
    };
  }, [reload]);

  // Getting ready (the bot's start-up work) reads again soon, like a build.
  const running =
    status?.ready === false ||
    status?.running === true ||
    (status?.jobs ?? []).some((j) => j.status === "building" || j.status === "queued");
  useEffect(() => {
    if (!poll) return;
    const t = setTimeout(() => void reload(), running ? FAST_MS : SLOW_MS);
    return () => clearTimeout(t);
  }, [poll, running, status, reload]);

  return { status, failed, reload };
}
