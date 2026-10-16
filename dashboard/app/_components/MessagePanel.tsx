/**
 * One small panel for the pages that only ever say one thing: signed-out,
 * errored, not-found. Drawn in the shell's own vocabulary — a `glass-card`
 * (remapped to the theme palette in css/_theme.css) plus the onboarding
 * typography from `app/onboarding/ui.tsx`: JetBrains Mono micro-label, Nunito
 * prose, --text/--text2/--text3 for colour.
 *
 * Deliberately NOT a client component and deliberately not the retired admin
 * template's CardBox: these pages carry no state, no form and no handlers of
 * their own, and the error page's retry button is the one thing that does.
 */
import type { CSSProperties, ReactNode } from "react";

export const eyebrowStyle: CSSProperties = {
  fontFamily: "var(--font-jbmono), monospace",
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".14em",
  textTransform: "uppercase",
  color: "var(--text3)",
};

export const titleStyle: CSSProperties = {
  fontFamily: "var(--font-nunito), sans-serif",
  fontWeight: 800,
  fontSize: 22,
  color: "var(--text)",
  margin: "6px 0 0",
};

export const proseStyle: CSSProperties = {
  color: "var(--text2)",
  fontSize: 15,
  lineHeight: 1.5,
  margin: "10px 0 0",
};

export default function MessagePanel({
  eyebrow,
  title,
  className = "w-11/12 md:w-7/12 lg:w-6/12 xl:w-5/12",
  children,
}: {
  eyebrow: string;
  title: string;
  className?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`glass-card ${className}`} style={{ borderRadius: 18, padding: 22 }}>
      <div style={eyebrowStyle}>{eyebrow}</div>
      <h1 style={titleStyle}>{title}</h1>
      {children}
    </div>
  );
}
