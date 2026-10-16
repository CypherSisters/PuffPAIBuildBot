"use client";

/**
 * The one home page and sidebar layout, shared by the rail and the home page.
 *
 * One store per browser tab: read from GET /dashboard/layout on first use,
 * again every 20 seconds and when the window gets focus (the brain may have
 * moved a tile from chat), and saved with PUT on every owner change. Until the
 * backend answers, the last copy this browser saw (or the defaults) is used.
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useEffect, useState } from "react";
import {
  cacheLayout,
  cachedLayout,
  defaultLayout,
  getLayout,
  putLayout,
  type DashLayout,
} from "../services/layoutService";

let current: DashLayout | null = null;
const listeners = new Set<(l: DashLayout) => void>();
let timer: ReturnType<typeof setInterval> | null = null;

function publish(next: DashLayout) {
  current = next;
  listeners.forEach((fn) => fn(next));
}

async function refresh() {
  const got = await getLayout();
  if (got) publish(got);
}

function start() {
  if (timer || typeof window === "undefined") return;
  void refresh();
  timer = setInterval(refresh, 20_000);
  window.addEventListener("focus", refresh);
}

/** Save one or more sections: shown at once, then written to the backend. */
export function saveLayout(patch: Partial<Pick<DashLayout, "tiles" | "sidebar" | "seen">>) {
  const base = current ?? defaultLayout();
  const next = { ...base, ...patch } as DashLayout;
  publish(next);
  cacheLayout(next);
  void putLayout(patch).then((saved) => {
    if (saved) publish(saved);
  });
}

/** Tell the backend what the page shows (ids and names), so the brain can
 *  find a tile or a menu item by the name she sees. Written only on change. */
export function reportSeen(section: "tiles" | "sidebar", seen: Record<string, string>) {
  const base = current;
  if (!base) return; // not read yet: report on the next render
  const before = base.seen?.[section] ?? {};
  if (JSON.stringify(before) === JSON.stringify(seen)) return;
  const nextSeen = { ...base.seen, [section]: seen };
  current = { ...base, seen: nextSeen };
  void putLayout({ seen: nextSeen });
}

export function useDashLayout(): { layout: DashLayout; loaded: boolean } {
  const [layout, setLayout] = useState<DashLayout | null>(current);
  useEffect(() => {
    if (!current) {
      const cached = cachedLayout();
      if (cached) setLayout(cached);
    }
    listeners.add(setLayout);
    start();
    return () => {
      listeners.delete(setLayout);
    };
  }, []);
  return { layout: layout ?? defaultLayout(), loaded: layout !== null };
}
