"use client";

/**
 * The bot's colour on the page. Its personality brings an accent colour (a
 * wedding bot and a 3D printing bot differ before anything is picked); once
 * she picks a look, the look wins, so the greeting and the wake-up wear the
 * look's own accent (--pink) instead. Also kept as --bot-accent on the page
 * for anything else that wants it.
 */

import { useEffect, useState } from "react";
import { LOOK_CHANGED, lookWorn } from "@/app/_lib/looks";

export function useBotAccent(personaAccent?: string): string {
  const [worn, setWorn] = useState(false);

  useEffect(() => {
    const check = () => setWorn(lookWorn());
    check();
    window.addEventListener(LOOK_CHANGED, check);
    return () => window.removeEventListener(LOOK_CHANGED, check);
  }, []);

  const accent = worn ? "var(--pink)" : personaAccent || "var(--sky)";

  const known = worn || Boolean(personaAccent);
  useEffect(() => {
    if (known) document.documentElement.style.setProperty("--bot-accent", accent);
  }, [known, accent]);

  return accent;
}
