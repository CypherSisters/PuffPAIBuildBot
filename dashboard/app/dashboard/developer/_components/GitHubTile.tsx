"use client";

/**
 * GitHub on the Developer page, beside the command line: whether it is
 * connected, Connect (the same flow the wizard uses), Disconnect with a
 * confirm, and how many GitHub searches are left.
 */

import MsIcon from "@/app/_components/MsIcon";
import GitHubConnect from "@/app/_components/GitHub/GitHubConnect";
import { FREDOKA, NUNITO, panelStyle } from "@/app/dashboard/apps/_components/ui";

export default function GitHubTile() {
  return (
    <section id="github" data-github-tile style={{ ...panelStyle, display: "grid", gap: 10, alignContent: "start", alignSelf: "start" }}>
      <div className="flex items-center" style={{ gap: 8 }}>
        <MsIcon name="code" size={24} style={{ color: "var(--sky)" }} />
        <h2 style={{ margin: 0, fontFamily: FREDOKA, fontWeight: 600, fontSize: 22, color: "var(--text)" }}>GitHub</h2>
      </div>
      <p style={{ margin: 0, fontFamily: NUNITO, fontSize: 13, color: "var(--text2)", lineHeight: 1.45 }}>
        Connect your account so your bot searches better and can use your own projects.
      </p>
      <GitHubConnect canDisconnect />
    </section>
  );
}
