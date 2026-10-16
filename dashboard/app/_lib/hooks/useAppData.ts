"use client";

import { useState, useEffect, useCallback, useRef } from "react";
import {
  getAppData,
  setAppData,
  appAction,
  getAppUpdateStatus,
  type AppName,
} from "../services/appService";
import { getBuildSelf, onBuildChanged } from "../services/buildService";
import { appOnBuild } from "../surfaceMap";

// Whether this build has an app at all, asked once per page load and shared by
// every hook (the home page, the floating Puff and the widgets each read two
// apps). A build without AlarmFlow or Calendar must not be asked about them
// every two seconds.
let buildAnswer: ReturnType<typeof getBuildSelf> | null = null;
function thisBuild() {
  if (!buildAnswer) buildAnswer = getBuildSelf();
  return buildAnswer;
}
onBuildChanged(() => {
  buildAnswer = null;
});

type UseAppDataReturn<T> = {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  update: (newData: T) => Promise<void>;
  runAction: (action: string, params?: Record<string, unknown>) => Promise<unknown>;
};

/**
 * useAppData
 *
 * Loads app data on mount and lightly polls for updates so PuffPAI agent
 * actions (timers, tasks, etc. created via chat) appear in the dashboard
 * without a manual refresh.
 *
 * Polling strategy:
 *   - Hit /apps/<name>/updated every 2s (cheap, just returns last_updated)
 *   - Only reload the full payload when last_updated changes
 *   - Pause polling when the tab is hidden to save battery
 *   - Neither load nor poll an app this build does not have (appOnBuild);
 *     when the build is not known the app is read, as before (fail open)
 */
export function useAppData<T = any>(appName: AppName): UseAppDataReturn<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const lastUpdatedRef = useRef<string | null>(null);
  /** Null until the build has answered; false when it does not have the app. */
  const [present, setPresent] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ask = () =>
      thisBuild().then((build) => {
        if (!cancelled) setPresent(appOnBuild(build, appName));
      });
    void ask();
    const off = onBuildChanged(() => void ask());
    return () => {
      cancelled = true;
      off();
    };
  }, [appName]);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const result = await getAppData(appName);
      setData(result);
      // Track last_updated so polling knows when to refetch
      if (result && typeof result === "object" && "last_updated" in result) {
        lastUpdatedRef.current = (result as any).last_updated;
      }
    } catch (err: any) {
      setError(err.message || "Failed to load app data");
    } finally {
      setLoading(false);
    }
  }, [appName]);

  const update = useCallback(
    async (newData: T) => {
      try {
        await setAppData(appName, newData);
        setData(newData);
        if (newData && typeof newData === "object" && "last_updated" in newData) {
          lastUpdatedRef.current = (newData as any).last_updated;
        }
      } catch (err: any) {
        setError(err.message || "Failed to update app data");
        throw err;
      }
    },
    [appName],
  );

  const runAction = useCallback(
    async (action: string, params?: Record<string, unknown>) => {
      try {
        const result = await appAction(appName, action, params);
        await reload();
        return result;
      } catch (err: any) {
        setError(err.message || "Action failed");
        throw err;
      }
    },
    [appName, reload],
  );

  // Initial load, once the build says it has this app
  useEffect(() => {
    if (present === true) reload();
    else if (present === false) setLoading(false);
  }, [present, reload]);

  // Lightweight polling for external updates (e.g. PuffPAI chat actions)
  useEffect(() => {
    if (present !== true) return undefined;
    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const poll = async () => {
      if (cancelled) return;
      if (typeof document !== "undefined" && document.hidden) return;
      try {
        const status = await getAppUpdateStatus(appName);
        if (cancelled) return;
        const newStamp = status?.last_updated;
        if (newStamp && newStamp !== lastUpdatedRef.current) {
          await reload();
        }
      } catch {
        // Network blip — ignore and try next tick
      }
    };

    intervalId = setInterval(poll, 2000);

    const handleVisibility = () => {
      if (!document.hidden) {
        // Tab returned to focus — refresh immediately
        poll();
      }
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", handleVisibility);
    }

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", handleVisibility);
      }
    };
  }, [appName, present, reload]);

  return { data, loading, error, reload, update, runAction };
}
