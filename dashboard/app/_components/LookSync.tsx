"use client";

import { useEffect } from "react";
import { lookSwitchedOff, saveLook, savedLook, wearLook } from "../_lib/looks";
import { getLooks, wornVars } from "../_lib/services/looksService";

/**
 * Keeps this browser's saved look equal to the one the bot uses.
 *
 * The inline boot script (app/layout.tsx) paints the saved copy before first
 * paint; this asks the backend once per page load which look is really in use
 * (it may have been changed from the terminal or another device) and, if it
 * differs, saves and wears the backend's. A browser that is not paired yet
 * gets no answer and keeps what it has.
 */
export default function LookSync() {
  useEffect(() => {
    let live = true;
    getLooks()
      .then((state) => {
        if (!live) return;
        const want = wornVars(state);
        const have = savedLook();
        if (JSON.stringify(want) === JSON.stringify(have)) return;
        saveLook(want);
        if (!lookSwitchedOff()) wearLook(want);
      })
      .catch(() => {
        /* not paired or backend away: keep the saved look */
      });
    return () => {
      live = false;
    };
  }, []);
  return null;
}
