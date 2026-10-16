"use client";

/**
 * The command line on a page of its own: what "Own window" on the Developer
 * page opens. With ?window=1 the dashboard draws no menu around it (see
 * dashboard/layout.tsx), so the window is just the command line.
 */

import Link from "next/link";
import { AppPage, NUNITO } from "@/app/dashboard/apps/_components/ui";
import CommandLine from "../_components/CommandLine";

export default function TerminalPage() {
  return (
    <AppPage>
      <div className="flex items-center" style={{ gap: 10 }}>
        <h1 style={{ margin: 0, fontFamily: NUNITO, fontWeight: 800, fontSize: 20, color: "var(--text)" }}>
          Command line
        </h1>
        <div className="flex-1" />
        <Link href="/dashboard/developer" style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--sky)" }}>
          Developer page
        </Link>
      </div>
      <CommandLine height="calc(100dvh - 210px)" />
    </AppPage>
  );
}
