"use client";

/**
 * The four live-card shapes - a picture, a number, a list, a gallery of
 * pictures - drawn from data
 * the backend already fetched and stored. Every value is rendered as text
 * (React escapes it); nothing from an outside service becomes markup or an
 * image address. A row's link is shown only when the backend kept it (a
 * secure address on the card's own service, see feeds.item_link) and opens
 * in a new tab. Pictures come only from the backend's own image route,
 * via feedImageUrl, which refuses anything not shaped like a stored name.
 *
 * `credit` is always drawn, whether or not there is data to show.
 */

import type { FeedCardData } from "@/app/_lib/services/feedService";
import { feedImageUrl } from "@/app/_lib/services/feedService";
import { itemLink } from "@/app/_lib/safeLink";

const NUNITO = "var(--font-nunito), Nunito, sans-serif";
const FREDOKA = "var(--font-fredoka), Fredoka, sans-serif";
const JBM = "var(--font-jbmono), 'JetBrains Mono', ui-monospace, monospace";

export function CreditLine({ credit }: { credit: string }) {
  return (
    <p
      style={{
        margin: "10px 0 0",
        fontFamily: NUNITO,
        fontWeight: 600,
        fontSize: 12,
        color: "var(--text3)",
      }}
    >
      {credit}
    </p>
  );
}

export default function FeedCardBody({
  appId,
  card,
  compact = false,
  pictureNote,
  pictureLimit = 6,
}: {
  appId: string;
  card: FeedCardData;
  compact?: boolean;
  /** Pictures a compact gallery shows; the rest are on the card's page. */
  pictureLimit?: number;
  /** Shown where a picture would be when none is stored (e.g. a draft). */
  pictureNote?: string;
}) {
  if (card.kind === "value") {
    return (
      <div>
        <div style={{ fontFamily: JBM, fontSize: 11, fontWeight: 700,
                      letterSpacing: ".12em", textTransform: "uppercase",
                      color: "var(--text3)" }}>
          {card.label}
        </div>
        <div style={{ fontFamily: FREDOKA, fontWeight: 600,
                      fontSize: compact ? 30 : 40, lineHeight: 1.1,
                      color: "var(--text)" }}>
          {card.value}
          {card.unit ? (
            <span style={{ fontSize: compact ? 15 : 18, marginLeft: 6,
                           color: "var(--text2)" }}>
              {card.unit}
            </span>
          ) : null}
        </div>
        {card.caption && (
          <p style={{ margin: "4px 0 0", fontFamily: NUNITO, fontSize: 13,
                      color: "var(--text2)" }}>
            {card.caption}
          </p>
        )}
      </div>
    );
  }

  if (card.kind === "gallery") {
    const pictures = (card.items ?? [])
      .map((row) => ({ ...row, src: feedImageUrl(appId, row.image) }))
      .filter((row) => row.src);
    if (pictures.length > 0) {
      const shown = compact ? pictures.slice(0, pictureLimit) : pictures;
      return (
        <div>
          {card.caption && (
            <p style={{ margin: "0 0 8px", fontFamily: NUNITO, fontSize: 13,
                        lineHeight: 1.4, color: "var(--text2)" }}>
              {card.caption}
            </p>
          )}
          <ul
            className="grid"
            style={{
              margin: 0, padding: 0, listStyle: "none", gap: compact ? 6 : 10,
              gridTemplateColumns: compact
                ? "repeat(3, minmax(0, 1fr))"
                : "repeat(auto-fill, minmax(180px, 1fr))",
            }}
          >
            {shown.map((row, index) => (
              <li key={`${row.image}-${index}`} className="min-w-0">
                <figure style={{ margin: 0 }}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- a
                      local, backend-checked file */}
                  <img
                    src={row.src as string}
                    alt={row.title || "Picture"}
                    loading="lazy"
                    style={{ width: "100%", aspectRatio: compact ? "1 / 1" : "4 / 3",
                             objectFit: compact ? "cover" : "contain",
                             borderRadius: compact ? 10 : 14,
                             background: "var(--chip)", display: "block" }}
                  />
                  <figcaption className="min-w-0" style={{ marginTop: 4 }}>
                    <div className={compact ? "truncate" : undefined}
                         style={{ fontFamily: NUNITO, fontWeight: 700,
                                  fontSize: compact ? 11 : 13,
                                  color: "var(--text)" }}>
                      {row.title}
                    </div>
                    {!compact && row.detail && (
                      <div style={{ fontFamily: NUNITO, fontSize: 12,
                                    lineHeight: 1.4, color: "var(--text2)" }}>
                        {row.detail.length > 220
                          ? `${row.detail.slice(0, 219)}…`
                          : row.detail}
                      </div>
                    )}
                  </figcaption>
                </figure>
              </li>
            ))}
          </ul>
          {compact && pictures.length > shown.length && (
            <p style={{ margin: "6px 0 0", fontFamily: NUNITO, fontSize: 12,
                        color: "var(--text3)" }}>
              And {pictures.length - shown.length} more pictures on its page.
            </p>
          )}
        </div>
      );
    }
    // No stored pictures yet (a draft's preview): the words, as a list.
  }

  if (card.kind === "image") {
    const src = feedImageUrl(appId, card.image);
    return (
      <div>
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element -- a local,
          // backend-checked file; next/image would add nothing here.
          <img
            src={src}
            alt={card.headline || card.caption || "Picture"}
            style={{ width: "100%", maxHeight: compact ? 180 : 420,
                     objectFit: "contain", borderRadius: 14,
                     background: "var(--chip)" }}
          />
        ) : (
          pictureNote && (
            <div style={{ borderRadius: 14, padding: "18px 12px",
                          background: "var(--chip)", fontFamily: NUNITO,
                          fontSize: 13, color: "var(--text3)",
                          textAlign: "center" }}>
              {pictureNote}
            </div>
          )
        )}
        {card.headline && (
          <p style={{ margin: "8px 0 0", fontFamily: FREDOKA, fontWeight: 600,
                      fontSize: compact ? 15 : 18, color: "var(--text)" }}>
            {card.headline}
          </p>
        )}
        {card.caption && card.caption !== card.headline && (
          <p style={{ margin: "4px 0 0", fontFamily: NUNITO, fontSize: 13,
                      lineHeight: 1.45, color: "var(--text2)" }}>
            {compact && card.caption.length > 160
              ? `${card.caption.slice(0, 159)}…`
              : card.caption}
          </p>
        )}
        {card.image_credit && (
          <p style={{ margin: "4px 0 0", fontFamily: NUNITO, fontSize: 12,
                      color: "var(--text3)" }}>
            Picture: {card.image_credit}
          </p>
        )}
      </div>
    );
  }

  const rows = card.items ?? [];
  const shown = compact ? rows.slice(0, 5) : rows;
  return (
    <ul className="space-y-2" style={{ margin: 0, padding: 0, listStyle: "none" }}>
      {card.kind === "gallery" && pictureNote && (
        <li style={{ fontFamily: NUNITO, fontSize: 13, color: "var(--text3)" }}>
          {pictureNote}
        </li>
      )}
      {shown.map((row, index) => {
        const src = feedImageUrl(appId, row.image);
        return (
          <li key={index} className="flex items-start gap-2.5">
            {src && (
              // eslint-disable-next-line @next/next/no-img-element -- local file
              <img src={src} alt="" width={40} height={40}
                   style={{ borderRadius: 8, objectFit: "cover", flexShrink: 0 }} />
            )}
            <div className="min-w-0">
              <div style={{ fontFamily: NUNITO, fontWeight: 700, fontSize: 14,
                            color: "var(--text)" }}>
                {row.title}
                {itemLink(row.link) && (
                  <a href={itemLink(row.link)} target="_blank" rel="noreferrer noopener"
                     style={{ marginLeft: 8, fontWeight: 600, fontSize: 12, color: "var(--sky)" }}>
                    Open
                  </a>
                )}
              </div>
              {row.detail && (
                <div style={{ fontFamily: NUNITO, fontSize: 13, lineHeight: 1.4,
                              color: "var(--text2)" }}>
                  {compact && row.detail.length > 120
                    ? `${row.detail.slice(0, 119)}…`
                    : row.detail}
                </div>
              )}
            </div>
          </li>
        );
      })}
      {compact && rows.length > shown.length && (
        <li style={{ fontFamily: NUNITO, fontSize: 12, color: "var(--text3)" }}>
          And {rows.length - shown.length} more on its page.
        </li>
      )}
    </ul>
  );
}
