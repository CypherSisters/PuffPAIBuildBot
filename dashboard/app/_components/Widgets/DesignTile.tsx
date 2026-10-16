"use client";

/**
 * DesignTile: the shared tile shell for the design match.
 *
 * Every dashboard tile is a radius-28 glass card: 1px var(--line) border,
 * var(--card) background, 18px backdrop blur, and a cloud photo layer at
 * opacity var(--imgTile) behind the content (design file lines 628-787).
 * Header pattern: MsIcon 21px in an accent color + Fredoka-600-20 title +
 * right-aligned JetBrains Mono micro-stat.
 */

import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";
import MsIcon from "../MsIcon";

/* Shared font stacks (fonts are loaded by app/layout.tsx). */
export const FREDOKA = "var(--font-fredoka), Fredoka, sans-serif";
export const NUNITO = "var(--font-nunito), Nunito, sans-serif";
export const JBM = "var(--font-jbmono), 'JetBrains Mono', ui-monospace, monospace";

/* The design's honest-empty-state striping. */
export const STRIPES =
  "repeating-linear-gradient(135deg, var(--chip) 0 8px, transparent 8px 16px)";

/** JBMono 700 11px .14em UPPERCASE micro-label (design's tiny captions). */
export function MicroLabel({
  children,
  style,
  className,
}: {
  children: ReactNode;
  style?: CSSProperties;
  className?: string;
}) {
  return (
    <div
      className={className}
      style={{
        fontFamily: JBM,
        fontWeight: 700,
        fontSize: 11,
        letterSpacing: "0.14em",
        color: "var(--text3)",
        textTransform: "uppercase",
        ...style,
      }}
    >
      {children}
    </div>
  );
}

/** Right-aligned JBMono micro-stat used in tile headers. */
export function MicroStat({
  children,
  color = "var(--text3)",
  size = 11,
}: {
  children: ReactNode;
  color?: string;
  size?: number;
}) {
  return (
    <span
      style={{
        fontFamily: JBM,
        fontWeight: 700,
        fontSize: size,
        letterSpacing: "0.06em",
        color,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </span>
  );
}

/** Honest placeholder block for data that is unavailable right now. */
export function StripesBlock({
  label,
  className,
  style,
}: {
  label?: string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <div
      className={`grid place-items-center rounded-[14px] ${className ?? ""}`}
      style={{ background: STRIPES, minHeight: 64, ...style }}
    >
      {label && (
        <span
          style={{
            fontFamily: JBM,
            fontWeight: 600,
            fontSize: 11,
            letterSpacing: "0.1em",
            color: "var(--text3)",
            textTransform: "uppercase",
          }}
        >
          {label}
        </span>
      )}
    </div>
  );
}

export default function DesignTile({
  icon,
  iconColor = "var(--text2)",
  title,
  stat,
  img,
  imgOpacity,
  href,
  /* Kiosk density: the board has to fit 1080p in one screen, so tile chrome
     is deliberately tight — every px here is px the content doesn't get. */
  padding = "14px 18px",
  children,
  bodyClassName,
}: {
  /** Material Symbols ligature name for the header icon. */
  icon?: string;
  iconColor?: string;
  title?: string;
  /** Right-aligned header node (use MicroStat). */
  stat?: ReactNode;
  /** Cloud photo behind the glass, e.g. "/design-cloud-1.jpg". */
  img?: string;
  /** Defaults to var(--imgTile); weather uses var(--img3). */
  imgOpacity?: string;
  /** Whole tile becomes a link. */
  href?: string;
  padding?: string;
  children: ReactNode;
  bodyClassName?: string;
}) {
  const shellClass =
    "relative block h-full overflow-hidden rounded-[28px] border backdrop-blur-[18px]";
  const shellStyle: CSSProperties = {
    borderColor: "var(--line)",
    background: "var(--card)",
    padding,
  };

  const inner = (
    <>
      {img && (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={img}
          alt=""
          aria-hidden
          className="pointer-events-none absolute inset-0 h-full w-full object-cover"
          style={{ opacity: imgOpacity ?? "var(--imgTile)" }}
        />
      )}
      <div className="relative flex h-full min-h-0 flex-col">
        {(icon || title || stat) && (
          <div className="mb-2 flex flex-shrink-0 items-center gap-2">
            {icon && <MsIcon name={icon} size={19} style={{ color: iconColor }} />}
            {title && (
              <div
                style={{
                  fontFamily: FREDOKA,
                  fontWeight: 600,
                  fontSize: 18,
                  color: "var(--text)",
                  whiteSpace: "nowrap",
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  minWidth: 0,
                }}
              >
                {title}
              </div>
            )}
            <div className="flex-1" />
            {stat}
          </div>
        )}
        <div className={bodyClassName ?? "min-h-0 flex-1 overflow-hidden"}>
          {children}
        </div>
      </div>
    </>
  );

  if (href) {
    return (
      <Link href={href} className={shellClass} style={shellStyle}>
        {inner}
      </Link>
    );
  }
  return (
    <div className={shellClass} style={shellStyle}>
      {inner}
    </div>
  );
}
