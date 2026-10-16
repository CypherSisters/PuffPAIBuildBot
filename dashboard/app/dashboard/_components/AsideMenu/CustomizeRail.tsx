"use client";

/**
 * The rail's Customize panel: every menu item with up, down, pin, hide or
 * show, and rename. Saved to the one layout file (the `sidebar` section of
 * <state>/dashboard_layout.json, PUT /dashboard/layout); the brain changes
 * the same file from chat (arrange_dashboard).
 *
 * Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
 */

import { useState } from "react";
import MsIcon from "../../../_components/MsIcon";
import type { MenuAsideItem } from "../../../_interfaces";
import { saveLayout, useDashLayout } from "../../../_lib/hooks/useDashLayout";

const btn: React.CSSProperties = {
  display: "grid",
  placeItems: "center",
  width: 24,
  height: 24,
  borderRadius: 8,
  border: 0,
  background: "transparent",
  color: "var(--text3)",
  cursor: "pointer",
  flex: "none",
};

export default function CustomizeRail({
  rail,
  onDone,
}: {
  rail: MenuAsideItem[];
  onDone: () => void;
}) {
  const { layout } = useDashLayout();
  const sb = layout.sidebar;
  const [renaming, setRenaming] = useState("");
  const [text, setText] = useState("");
  const hrefs = rail.map((m) => m.href ?? "").filter(Boolean);

  const move = (href: string, by: -1 | 1) => {
    const item = rail.find((m) => m.href === href);
    const group = rail.filter((m) => m.railGroup === item?.railGroup).map((m) => m.href ?? "");
    const at = group.indexOf(href);
    const swap = group[at + by];
    if (!swap) return;
    const order = [...hrefs];
    const i = order.indexOf(href);
    const j = order.indexOf(swap);
    [order[i], order[j]] = [order[j], order[i]];
    saveLayout({ sidebar: { ...sb, order } });
  };
  const toggle = (key: "hidden" | "pinned", href: string) =>
    saveLayout({
      sidebar: {
        ...sb,
        [key]: sb[key].includes(href) ? sb[key].filter((h) => h !== href) : [...sb[key], href],
      },
    });
  const rename = (href: string) => {
    const names = { ...sb.names };
    const clean = text.trim().slice(0, 40);
    if (clean) names[href] = clean;
    else delete names[href];
    saveLayout({ sidebar: { ...sb, names } });
    setRenaming("");
  };

  return (
    <div className="flex flex-col" style={{ gap: 2 }} aria-label="Customize the menu">
      {rail.map((m) => {
        const href = m.href ?? "";
        const hidden = sb.hidden.includes(href);
        const pinned = sb.pinned.includes(href);
        return (
          <div
            key={href}
            className="flex items-center"
            style={{
              gap: 4,
              minHeight: 34,
              padding: "0 6px",
              borderRadius: 12,
              opacity: hidden ? 0.45 : 1,
              font: "600 13px var(--font-nunito), sans-serif",
              color: "var(--text2)",
            }}
          >
            {renaming === href ? (
              <form
                className="min-w-0 flex-1"
                onSubmit={(e) => {
                  e.preventDefault();
                  rename(href);
                }}
              >
                <input
                  aria-label="Menu item name"
                  autoFocus
                  value={text}
                  maxLength={40}
                  onChange={(e) => setText(e.target.value)}
                  onBlur={() => setRenaming("")}
                  style={{
                    width: "100%",
                    background: "var(--card2)",
                    border: "1px solid var(--line)",
                    borderRadius: 8,
                    padding: "3px 8px",
                    color: "var(--text)",
                    font: "600 13px var(--font-nunito), sans-serif",
                  }}
                />
              </form>
            ) : (
              <button
                type="button"
                title="Rename"
                onClick={() => {
                  setText(m.label);
                  setRenaming(href);
                }}
                className="min-w-0 flex-1 truncate text-left"
                style={{ ...btn, display: "block", width: "auto", height: "auto", color: "inherit", font: "inherit" }}
              >
                {m.label}
              </button>
            )}
            <button type="button" style={btn} aria-label={`Move ${m.label} up`} onClick={() => move(href, -1)}>
              <MsIcon name="arrow_upward" size={16} />
            </button>
            <button type="button" style={btn} aria-label={`Move ${m.label} down`} onClick={() => move(href, 1)}>
              <MsIcon name="arrow_downward" size={16} />
            </button>
            <button
              type="button"
              style={{ ...btn, color: pinned ? "var(--pink)" : btn.color }}
              aria-label={pinned ? `Unpin ${m.label}` : `Pin ${m.label}`}
              aria-pressed={pinned}
              onClick={() => toggle("pinned", href)}
            >
              <MsIcon name="push_pin" size={16} />
            </button>
            <button
              type="button"
              style={btn}
              aria-label={hidden ? `Show ${m.label}` : `Hide ${m.label}`}
              onClick={() => toggle("hidden", href)}
            >
              <MsIcon name={hidden ? "visibility_off" : "visibility"} size={16} />
            </button>
          </div>
        );
      })}
      <div className="flex items-center" style={{ gap: 8, padding: "8px 6px 0" }}>
        <button
          type="button"
          onClick={() => saveLayout({ sidebar: { order: [], hidden: [], pinned: [], names: {} } })}
          style={{ ...btn, width: "auto", padding: "0 6px", font: "700 12px var(--font-nunito), sans-serif" }}
        >
          Reset
        </button>
        <span className="flex-1" />
        <button
          type="button"
          onClick={onDone}
          className="cursor-pointer rounded-full border-0"
          style={{
            padding: "5px 14px",
            background: "linear-gradient(140deg, var(--pink), var(--magenta))",
            color: "#fff",
            font: "700 13px var(--font-nunito), sans-serif",
          }}
        >
          Done
        </button>
      </div>
    </div>
  );
}
