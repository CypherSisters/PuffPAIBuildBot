"use client";

/**
 * The command line as a small tile on the Developer page. "Bigger" grows it
 * in place (the whole width, a taller screen); "Own window" opens it on its
 * own page (/dashboard/developer/terminal) in a separate window, without
 * the menu around it.
 */

import { useState } from "react";
import MsIcon from "@/app/_components/MsIcon";
import { FREDOKA, NUNITO, panelStyle } from "@/app/dashboard/apps/_components/ui";
import CommandLine from "./CommandLine";

export const TERMINAL_PAGE = "/dashboard/developer/terminal";

const tool: React.CSSProperties = {
  fontFamily: NUNITO,
  fontWeight: 700,
  fontSize: 13,
  padding: "5px 12px",
  borderRadius: 999,
  border: "1px solid var(--line)",
  background: "var(--card2)",
  color: "var(--text)",
  cursor: "pointer",
  display: "inline-flex",
  alignItems: "center",
  gap: 6,
};

export function popOut() {
  window.open(`${TERMINAL_PAGE}?window=1`, "puffpai-command-line", "popup,width=900,height=620");
}

export default function CliTile() {
  const [big, setBig] = useState(false);
  return (
    <section
      id="command-line"
      data-cli-tile={big ? "big" : "small"}
      style={{ ...panelStyle, display: "grid", gap: 10, gridColumn: big ? "1 / -1" : undefined }}
    >
      <div className="flex flex-wrap items-center" style={{ gap: 8 }}>
        <MsIcon name="terminal" size={24} style={{ color: "var(--mint)" }} />
        <h2 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 22, color: "var(--text)" }}>
          Command line
        </h2>
        <div className="flex-1" />
        <button type="button" style={tool} onClick={() => setBig((b) => !b)} aria-pressed={big}>
          <MsIcon name={big ? "close_fullscreen" : "open_in_full"} size={16} />
          {big ? "Smaller" : "Bigger"}
        </button>
        <button type="button" style={tool} onClick={popOut}>
          <MsIcon name="open_in_new" size={16} />
          Own window
        </button>
      </div>
      <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 13, color: "var(--text2)", lineHeight: 1.45 }}>
        A full command line on this computer. What you run here is not checked by the Bridge.
      </p>
      <CommandLine height={big ? 460 : 190} />
    </section>
  );
}
