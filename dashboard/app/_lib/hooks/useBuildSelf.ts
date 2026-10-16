"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getBuildSelf,
  onBuildChanged,
  type BuildSelf,
} from "../services/buildService";

type UseBuildSelfReturn = {
  /** This build's own description, or null while loading / when unreachable. */
  build: BuildSelf | null;
  loading: boolean;
  /** True when the backend answered nothing. Callers say so rather than
   *  rendering zeros that look like real data. */
  unreachable: boolean;
  reload: () => Promise<void>;
};

/**
 * What this build IS, fetched once per mount.
 *
 * Deliberately not polled: a manifest changes when the owner runs the wizard,
 * not every thirty seconds, and the pending-pack scan reads ZIM headers off
 * disk. Callers that want it fresh call `reload` from their own Sync button.
 */
export function useBuildSelf(): UseBuildSelfReturn {
  const [build, setBuild] = useState<BuildSelf | null>(null);
  const [loading, setLoading] = useState(true);
  const [unreachable, setUnreachable] = useState(false);

  const apply = useCallback((data: BuildSelf | null) => {
    setBuild(data);
    setUnreachable(data === null);
    setLoading(false);
  }, []);

  /** Fetch again on demand - the Sync button, and the first-run return trip. */
  const reload = useCallback(async () => {
    apply(await getBuildSelf());
  }, [apply]);

  // The fetch-on-mount runs inline rather than calling reload(), so the state
  // lands in the async continuation instead of the effect body. Two reasons:
  // the lint rule cannot see through the await and reads reload() as a
  // synchronous setState, and - the real one - the old version had no
  // cancellation, so a slow answer arriving after the owner navigated away set
  // state on a component that no longer existed.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const data = await getBuildSelf();
      if (!cancelled) apply(data);
    })();
    return () => {
      cancelled = true;
    };
  }, [apply]);

  // ...and again when this browser installs or deletes an app. Not polling:
  // exactly one extra fetch, when a write in another component has already
  // changed the answer. Without it the sidebar rail — mounted in the layout
  // above every page, so it never remounts on a client navigation — kept
  // showing the build as it was before the persona existed.
  useEffect(() => onBuildChanged(() => void reload()), [reload]);

  return { build, loading, unreachable, reload };
}
