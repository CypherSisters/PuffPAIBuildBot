"use client";

/**
 * App-page primitives, in the dashboard's own design language.
 *
 * WHY THIS FILE EXISTS
 * --------------------
 * The five built-in app pages arrived from an admin template and were still
 * wearing it: `SectionMain` for the page frame, `CardBox` for every panel,
 * `SectionTitleLineWithButton` for the heading — plus `dark:` class pairs and
 * hex literals, because the template assumed a light theme with a dark variant
 * bolted on. The shell around them is dark-native and driven by the custom
 * properties in css/_theme.css (--card, --line, --text/2/3, --pink, --mint,
 * --lemon, --sky, --chip), so an app page in template clothes read as a
 * different product than the landing page it was reached from.
 *
 * `SectionTitleLineWithButton` was the worst of it: given no children it
 * renders a cog BUTTON THAT DOES NOTHING — a control the owner can click that
 * has never been wired to anything. It is not being restyled, it is being
 * dropped.
 *
 * These primitives mirror the ones the converted pages already use
 * (device/page.tsx `panelBase`/`cardBase`/`microLabel`, onboarding/ui.tsx,
 * Widgets/DesignTile) rather than inventing a sixth dialect. Nothing here
 * touches data: every page keeps the fetches, state and handlers it had.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

import type { CSSProperties, ReactNode } from "react";

export const FREDOKA = "var(--font-fredoka), sans-serif";
export const NUNITO = "var(--font-nunito), sans-serif";
export const JBM = "var(--font-jbmono), monospace";

/** Uppercase mono micro-label — the shell's section marker. */
export const microLabel: CSSProperties = {
  fontFamily: JBM,
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".14em",
  textTransform: "uppercase",
  color: "var(--text3)",
};

/** Glass panel, matching device/page.tsx `panelBase`. */
export const panelStyle: CSSProperties = {
  borderRadius: 24,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "20px 22px",
  boxShadow: "var(--shadow)",
};

/** The recessed inner block: rows, list items, wells inside a panel. */
export const wellStyle: CSSProperties = {
  borderRadius: 16,
  background: "var(--card2)",
  border: "1px solid var(--line)",
  padding: "12px 14px",
};

export const inputStyle: CSSProperties = {
  width: "100%",
  background: "var(--card2)",
  border: "1px solid var(--line)",
  borderRadius: 12,
  padding: "10px 12px",
  color: "var(--text)",
  fontFamily: NUNITO,
  fontSize: 15,
};

/**
 * Page frame. Replaces `SectionMain`, whose `p-6` + `containerMaxW` centred the
 * content in a column narrower than the rail it sits beside.
 */
export function AppPage({
  children,
  style,
}: {
  children: ReactNode;
  style?: CSSProperties;
}) {
  return (
    <section
      style={{
        padding:
          "clamp(16px,2.4vh,26px) clamp(18px,2vw,36px) clamp(18px,2.8vh,30px)",
        display: "flex",
        flexDirection: "column",
        gap: "clamp(12px,1.8vh,20px)",
        animation: "slide-up 0.3s ease-out",
        ...style,
      }}
    >
      {children}
    </section>
  );
}

/**
 * Page heading: title, one optional sentence, and whatever actions the page
 * supplies on the right. Replaces `SectionTitleLineWithButton` — same call
 * shape minus its `icon`, and WITHOUT the unwired cog it drew when a caller
 * passed no children.
 */
export function AppHeader({
  title,
  lede,
  children,
}: {
  title: string;
  lede?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="min-w-0">
        <h1
          style={{
            margin: 0,
            fontFamily: FREDOKA,
            fontWeight: 600,
            fontSize: "clamp(26px,2.4vw,40px)",
            letterSpacing: "-.8px",
            lineHeight: 1.05,
            color: "var(--text)",
          }}
        >
          {title}
        </h1>
        {lede && (
          <p
            style={{
              margin: "6px 0 0",
              fontFamily: NUNITO,
              fontWeight: 500,
              fontSize: 16,
              lineHeight: 1.4,
              color: "var(--text2)",
            }}
          >
            {lede}
          </p>
        )}
      </div>
      <div className="flex-1" />
      {children}
    </div>
  );
}

/**
 * Divider heading for a section WITHIN a page — the other thing
 * `SectionTitleLineWithButton` was being used for, at `text-2xl`, which made
 * every subsection compete with the page title. Here it is a mono micro-label
 * on a rule, with room for a count or a control on the right.
 */
export function SectionBar({
  title,
  children,
}: {
  title: string;
  children?: ReactNode;
}) {
  return (
    <div
      className="flex flex-wrap items-center gap-3"
      style={{ paddingTop: 4, borderTop: "1px solid var(--line)" }}
    >
      <div style={{ ...microLabel, paddingTop: 10 }}>{title}</div>
      <div className="flex-1" />
      {children}
    </div>
  );
}

/**
 * Glass panel. Replaces `CardBox`.
 *
 * `accent` is a theme colour variable and turns the panel into a notice: a
 * thick coloured left edge over a tint of the same hue. It is a PROP rather
 * than the `border-l-4 border-[…]` classes the template used, because the
 * panel sets `border` as an inline shorthand and an inline shorthand beats any
 * class — so those classes drew nothing at all. The five error and warning
 * panels across these pages were silently plain.
 */
export function Panel({
  children,
  className,
  style,
  hoverable = false,
  accent,
  onClick,
}: {
  children: ReactNode;
  className?: string;
  style?: CSSProperties;
  hoverable?: boolean;
  accent?: string;
  onClick?: (e: React.MouseEvent) => void;
}) {
  const tone: CSSProperties = accent
    ? {
        borderLeft: `4px solid var(${accent})`,
        background: `color-mix(in srgb, var(${accent}) 12%, var(--card))`,
        color: `var(${accent})`,
      }
    : {};
  return (
    <div
      className={`${hoverable ? "transition hover:-translate-y-0.5" : ""} ${className ?? ""}`}
      style={{ ...panelStyle, ...tone, ...style }}
      onClick={onClick}
    >
      {children}
    </div>
  );
}

/** Micro-label heading inside a panel. */
export function PanelTitle({
  children,
  style,
}: {
  children: ReactNode;
  style?: CSSProperties;
}) {
  return <div style={{ ...microLabel, marginBottom: 10, ...style }}>{children}</div>;
}

/**
 * What a panel says when it has nothing in it.
 *
 * Deliberately plain. An empty app is the normal first state — these apps ship
 * with no data — and the honest line is "there is nothing here yet", never a
 * suggestion to say a phrase no handler listens for.
 */
export function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <p
      style={{
        fontFamily: NUNITO,
        fontWeight: 500,
        fontSize: 14,
        lineHeight: 1.5,
        color: "var(--text2)",
        margin: 0,
      }}
    >
      {children}
    </p>
  );
}

/** Pill button. `tone` picks the shell's gradient or its quiet chip. */
export function Pill({
  children,
  onClick,
  tone = "quiet",
  disabled = false,
  type = "button",
  style,
  title,
}: {
  children: ReactNode;
  onClick?: (e: React.MouseEvent) => void;
  tone?: "quiet" | "accent" | "active";
  disabled?: boolean;
  type?: "button" | "submit";
  style?: CSSProperties;
  title?: string;
}) {
  const tones: Record<string, CSSProperties> = {
    quiet: { background: "var(--chip)", color: "var(--text2)" },
    active: { background: "var(--chip)", color: "var(--pink)" },
    accent: {
      background: "linear-gradient(140deg, var(--pink), var(--magenta))",
      color: "#fff",
    },
  };
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className="inline-flex cursor-pointer items-center gap-1.5 rounded-full border-0 transition hover:opacity-90 disabled:cursor-default disabled:opacity-50"
      style={{
        padding: "8px 16px",
        fontFamily: NUNITO,
        fontWeight: 700,
        fontSize: 14,
        ...tones[tone],
        ...style,
      }}
    >
      {children}
    </button>
  );
}
