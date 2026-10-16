"use client";

/**
 * "Use CLI": the commands for one Developer page, in a side panel.
 *
 * Every word and every command comes from the backend's one list
 * (GET /developer/cli/<page>), so the panel shows only commands this build
 * really has, and never one that carries a PIN, a password or a key. A web
 * page cannot open a terminal by itself, so the panel says how to open one,
 * then paste.
 */

import { useEffect, useState } from "react";
import CopyCommand from "@/app/_components/CopyCommand";
import MsIcon from "@/app/_components/MsIcon";
import { JBM, NUNITO, FREDOKA, microLabel, wellStyle } from "@/app/dashboard/apps/_components/ui";
import { getCli, type CliPanel, type DevPage, type Shell } from "../developerService";

const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 13,
  lineHeight: 1.45,
  color: "var(--text2)",
};

const SHELL_NAMES: Record<Shell, string> = {
  windows: "Windows PowerShell",
  posix: "Mac or Linux",
};

function ShellTab({ on, label, onClick }: { on: boolean; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      style={{
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 13,
        padding: "6px 12px",
        borderRadius: 999,
        border: "1px solid var(--line)",
        background: on ? "linear-gradient(140deg, var(--pink), var(--magenta))" : "var(--card2)",
        color: on ? "#fff" : "var(--text2)",
        cursor: "pointer",
      }}
    >
      {label}
    </button>
  );
}

export default function UseCliPanel({ page }: { page: DevPage }) {
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<CliPanel | null>(null);
  const [failed, setFailed] = useState(false);
  const [shell, setShell] = useState<Shell>("windows");

  useEffect(() => {
    if (!open || panel) return;
    let alive = true;
    getCli(page).then((p) => {
      if (!alive) return;
      if (p) {
        setPanel(p);
        setShell(p.box);
      } else {
        setFailed(true);
      }
    });
    return () => {
      alive = false;
    };
  }, [open, panel, page]);

  return (
    <>
      <button
        type="button"
        data-use-cli=""
        onClick={() => setOpen(true)}
        className="flex items-center gap-2"
        style={{
          fontFamily: NUNITO,
          fontWeight: 700,
          fontSize: 14,
          padding: "8px 16px",
          borderRadius: 999,
          border: "1px solid var(--line)",
          background: "var(--card2)",
          color: "var(--text)",
          cursor: "pointer",
        }}
      >
        <MsIcon name="terminal" size={18} />
        Use CLI
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex justify-end"
          style={{ background: "rgba(0,0,0,.35)" }}
          onClick={() => setOpen(false)}
        >
          <aside
            role="dialog"
            aria-label="Commands for this page"
            data-cli-panel=""
            onClick={(e) => e.stopPropagation()}
            style={{
              width: "min(560px, 100vw)",
              height: "100%",
              overflowY: "auto",
              background: "var(--railBg)",
              backdropFilter: "blur(22px)",
              WebkitBackdropFilter: "blur(22px)",
              borderLeft: "1px solid var(--line)",
              padding: "24px 22px",
              display: "grid",
              alignContent: "start",
              gap: 14,
            }}
          >
            <div className="flex items-center gap-3">
              <h2 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 24, color: "var(--text)" }}>
                Commands for this page
              </h2>
              <div className="flex-1" />
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close"
                style={{ background: "none", border: "none", color: "var(--text2)", cursor: "pointer" }}
              >
                <MsIcon name="close" size={22} />
              </button>
            </div>

            {!panel && !failed && <p style={small}>Getting the commands from your bot.</p>}
            {failed && (
              <p style={small}>Your bot did not answer, so the commands cannot be shown right now.</p>
            )}

            {panel && (
              <>
                <div className="flex flex-wrap gap-2">
                  {(["windows", "posix"] as Shell[]).map((s) => (
                    <ShellTab key={s} on={shell === s} label={SHELL_NAMES[s]} onClick={() => setShell(s)} />
                  ))}
                </div>
                <p style={{ ...small, color: "var(--text)", fontWeight: 700 }} data-open-terminal="">
                  {panel.open_terminal[shell]}
                </p>
                {panel.go_to_folder[shell] ? (
                  <div style={{ ...wellStyle, display: "grid", gap: 6 }}>
                    <p style={small}>{panel.go_to_folder_line}</p>
                    <CopyCommand command={panel.go_to_folder[shell] as string} label={panel.go_to_folder_line} />
                  </div>
                ) : panel.go_to_folder_said?.[shell] ? (
                  <p style={small} data-go-to-folder-said="">{panel.go_to_folder_said[shell]}</p>
                ) : null}
                <div style={{ display: "grid", gap: 10 }}>
                  {panel.commands.map((c) => (
                    <div key={c.what} style={{ ...wellStyle, display: "grid", gap: 6 }}>
                      <p style={{ ...small, color: "var(--text)" }}>{c.what}</p>
                      <CopyCommand command={c[shell]} label={c.what} />
                      {c.fill && <p style={small}>{c.fill}</p>}
                      {c.asks && <p style={small}>It asks for {c.asks}.</p>}
                    </div>
                  ))}
                </div>
                {panel.no_command_yet.length > 0 && (
                  <div style={{ display: "grid", gap: 4 }}>
                    <div style={microLabel}>Not a command yet</div>
                    {panel.no_command_yet.map((line) => (
                      <p key={line} style={small}>{line}</p>
                    ))}
                  </div>
                )}
                <p style={{ ...small, fontFamily: JBM, fontSize: 12, color: "var(--text3)" }}>
                  {panel.secrets_line}
                </p>
              </>
            )}
          </aside>
        </div>
      )}
    </>
  );
}
