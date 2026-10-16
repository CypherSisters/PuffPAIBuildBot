"use client";

/**
 * Small parts the three Developer pages share: a label and its value, a link
 * to the page that already does a job (the Developer area links to it rather
 * than copying it), and a plain paragraph.
 */

import Link from "next/link";
import type { ReactNode } from "react";
import MsIcon from "@/app/_components/MsIcon";
import { JBM, NUNITO, microLabel, wellStyle } from "@/app/dashboard/apps/_components/ui";

export const small: React.CSSProperties = {
  margin: 0,
  fontFamily: NUNITO,
  fontSize: 14,
  lineHeight: 1.5,
  color: "var(--text2)",
};

export function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1" style={{ padding: "6px 0" }}>
      <div style={{ ...microLabel, minWidth: 170 }}>{label}</div>
      <div
        style={{
          fontFamily: NUNITO,
          fontWeight: 600,
          fontSize: 15,
          color: "var(--text)",
          overflowWrap: "anywhere",
          flex: 1,
        }}
      >
        {children}
      </div>
    </div>
  );
}

export function Path({ children }: { children: ReactNode }) {
  return <span style={{ fontFamily: JBM, fontSize: 13, fontWeight: 500 }}>{children}</span>;
}

export function GoTo({ href, title, children }: { href: string; title: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="flex items-center gap-3 transition-transform hover:scale-[1.01]"
      style={{ ...wellStyle, color: "var(--text)", textDecoration: "none" }}
    >
      <div style={{ flex: 1 }}>
        <div style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 15 }}>{title}</div>
        <p style={{ ...small, fontSize: 13 }}>{children}</p>
      </div>
      <MsIcon name="arrow_forward" size={20} style={{ color: "var(--text3)" }} />
    </Link>
  );
}

export function BackToDeveloper() {
  return (
    <Link
      href="/dashboard/developer"
      className="flex items-center gap-1"
      style={{ ...small, fontSize: 13, color: "var(--text3)", textDecoration: "none" }}
    >
      <MsIcon name="arrow_back" size={16} />
      Developer
    </Link>
  );
}
