"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { mdiMagnify, mdiMessageText } from "@mdi/js";
import Icon from "../../_components/Icon";
import FormField from "../../_components/FormField";
import { useBuildMenu } from "../_lib/useBuildMenu";
import { assistantName } from "../../_lib/surfaceMap";
import type { MenuAsideItem } from "../../_interfaces";

// Re-exported for the callers that already import it from here. The key and the
// stash now live in app/_lib/chatHandoff.ts, because the home board and the app
// pages hand off sentences too and none of them should have to import a search
// dialog to do it.
export { PENDING_CHAT_QUERY_KEY } from "../../_lib/chatHandoff";
import { PENDING_CHAT_QUERY_KEY } from "../../_lib/chatHandoff";

type Entry = {
  href: string;
  label: string;
  icon?: string;
  keywords: string[];
};

// Extra keyword aliases so common phrasings resolve to the right page. Keyed by
// href so it stays attached to the destination even if labels change.
const ALIASES: Record<string, string[]> = {
  "/dashboard/homeassistant": ["smart home", "home", "lights", "devices", "thermostat"],
  "/dashboard/apps/alarmflow": ["timers", "alarms", "alarm", "timer", "reminders"],
  "/dashboard/agents": ["agents", "team"],
  "/dashboard/chat": ["assistant", "ask", "talk"],
  "/dashboard/vault": ["passwords", "secrets", "private", "credentials"],
  "/dashboard/knowledge": ["docs", "library", "shelf"],
  "/dashboard/documents": ["files", "uploads"],
  "/onboarding": ["setup", "onboarding", "wizard", "first run", "install", "re-run setup"],
};

// Flatten the aside menu (including nested app entries) into searchable
// destinations. Derived from menuAside so new pages stay in sync automatically.
function buildEntries(items: MenuAsideItem[]): Entry[] {
  const out: Entry[] = [];
  for (const item of items) {
    if (item.href) {
      out.push({
        href: item.href,
        label: item.label,
        icon: item.icon,
        keywords: ALIASES[item.href] ?? [],
      });
    }
    if (item.menu) out.push(...buildEntries(item.menu));
  }
  return out;
}

// NOT a module constant any more. The searchable set is what THIS build
// carries: a route the build never loaded must not be reachable by typing its
// name either, or the gate on the rail is decoration. Same fail-open rule —
// an unknown build indexes everything.

function scoreEntry(entry: Entry, q: string): number {
  const label = entry.label.toLowerCase();
  if (label === q) return 100;
  if (label.startsWith(q)) return 80;
  if (label.includes(q)) return 60;
  if (entry.keywords.some((k) => k === q)) return 55;
  if (entry.keywords.some((k) => k.startsWith(q))) return 50;
  if (entry.keywords.some((k) => k.includes(q))) return 40;
  return -1;
}

export default function QuickSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);
  // The name the bot answers to, not the product mark. Falls back to PuffPAI.
  const { build, menu } = useBuildMenu();
  const botName = assistantName(build);
  const entries = useMemo(() => buildEntries(menu), [menu]);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [] as Entry[];
    return entries.map((e) => ({ e, score: scoreEntry(e, q) }))
      .filter((x) => x.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, 8)
      .map((x) => x.e);
  }, [entries, query]);

  // Keep the highlighted row valid as results change.
  useEffect(() => {
    setSelected(0);
  }, [results.length]);

  // Close on click outside.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  const goTo = (href: string) => {
    setQuery("");
    setOpen(false);
    router.push(href);
  };

  // No route matched → hand the raw text to the assistant. Stashed in
  // sessionStorage; the chat page reads + clears it and submits on mount.
  const handoffToChat = () => {
    const text = query.trim();
    if (!text) return;
    try {
      sessionStorage.setItem(PENDING_CHAT_QUERY_KEY, text);
    } catch {
      /* sessionStorage unavailable — fall through to a plain navigation */
    }
    setQuery("");
    setOpen(false);
    router.push("/dashboard/chat");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setOpen(false);
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((i) => Math.min(i + 1, Math.max(results.length - 1, 0)));
      return;
    }
    if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((i) => Math.max(i - 1, 0));
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (results.length > 0) {
        goTo(results[Math.min(selected, results.length - 1)].href);
      } else {
        handoffToChat();
      }
    }
  };

  const showDropdown = open && query.trim().length > 0;

  return (
    <div className="relative w-full" ref={containerRef}>
      <FormField isBorderless isTransparent icon={mdiMagnify}>
        {({ className }) => (
          <input
            type="text"
            placeholder={`Search or ask ${botName}`}
            className={className}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onFocus={() => setOpen(true)}
            onKeyDown={handleKeyDown}
            aria-label="Quick search"
            autoComplete="off"
          />
        )}
      </FormField>

      {showDropdown && (
        <div className="absolute left-0 top-full z-50 mt-1 w-64 overflow-hidden rounded-lg border border-(--line) bg-(--puff-surface-deep) shadow-xl shadow-black/40 backdrop-blur-sm sm:w-72">
          {results.length > 0 ? (
            <ul className="max-h-80 overflow-y-auto py-1">
              {results.map((entry, i) => (
                <li key={entry.href}>
                  <button
                    type="button"
                    onMouseEnter={() => setSelected(i)}
                    onClick={() => goTo(entry.href)}
                    className={`flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm transition ${
                      i === selected
                        ? "bg-(--sky)/40 text-(--sky)"
                        : "text-(--text) hover:bg-(--puff-card-hover)"
                    }`}
                  >
                    {entry.icon && (
                      <Icon path={entry.icon} size="18" className="flex-shrink-0 text-(--sky)" />
                    )}
                    <span className="flex-1 truncate">{entry.label}</span>
                    <span className="truncate text-[10px] text-(--text2)">{entry.href}</span>
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <button
              type="button"
              onClick={handoffToChat}
              className="flex w-full items-center gap-2.5 px-3 py-2.5 text-left text-sm text-(--text) transition hover:bg-(--puff-card-hover)"
            >
              <Icon path={mdiMessageText} size="18" className="flex-shrink-0 text-(--sky)" />
              <span className="flex-1 truncate">
                Ask {botName}: <span className="text-(--sky)">&ldquo;{query.trim()}&rdquo;</span>
              </span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
