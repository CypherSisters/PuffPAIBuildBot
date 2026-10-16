"use client";

/**
 * One command in a pill, with a Copy button. Shared by the Knowledge page and
 * the Developer area's "Use CLI" panels.
 *
 * Copying is honest: a browser that reaches the bot over the home network
 * (plain http, not this computer) has no clipboard access for pages, so the
 * old button said "Copied" and copied nothing. Now it tries the clipboard,
 * then the older select-and-copy way, and if both fail it selects the text
 * and says to copy it by hand.
 */

import { useRef, useState } from "react";
import MsIcon from "./MsIcon";

const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";

async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the older way */
  }
  try {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    const done = document.execCommand("copy");
    document.body.removeChild(area);
    return done;
  } catch {
    return false;
  }
}

export default function CopyCommand({
  command,
  label,
}: {
  command: string;
  /** Read out by screen readers: what this command is for. */
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "by-hand">("idle");
  const codeRef = useRef<HTMLElement>(null);

  const onCopy = async () => {
    const ok = await copyText(command);
    if (!ok && codeRef.current) {
      const range = document.createRange();
      range.selectNodeContents(codeRef.current);
      const sel = window.getSelection();
      sel?.removeAllRanges();
      sel?.addRange(range);
    }
    setState(ok ? "copied" : "by-hand");
    setTimeout(() => setState("idle"), 2500);
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      <code
        ref={codeRef}
        data-command=""
        style={{
          fontFamily: JBMONO,
          fontSize: 12.5,
          color: "var(--text2)",
          background: "var(--card2)",
          border: "1px solid var(--line)",
          borderRadius: 12,
          padding: "6px 10px",
          overflowWrap: "anywhere",
          userSelect: "all",
        }}
      >
        {command}
      </code>
      <button
        type="button"
        aria-label={label ? `Copy: ${label}` : "Copy this command"}
        onClick={onCopy}
        className="flex items-center gap-1.5"
        style={{
          fontFamily: NUNITO,
          fontWeight: 700,
          fontSize: 12.5,
          color: "var(--text2)",
          background: "var(--card2)",
          border: "1px solid var(--line)",
          borderRadius: 999,
          padding: "6px 12px",
          cursor: "pointer",
        }}
      >
        <MsIcon name={state === "copied" ? "check" : "content_copy"} size={15} />
        {state === "copied" ? "Copied" : state === "by-hand" ? "Selected, now copy it" : "Copy"}
      </button>
    </div>
  );
}
