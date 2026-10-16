"use client";

/* ═══════════════════════════════════════════════════════════════
   SOURCES FOR THIS BUILD — the gap between what the wizard chose
   and what is on this shelf.

   The Library tab used to show an empty bookshelf and nothing else, even
   when the build record held five offered sources, one downloaded file and
   two chosen packs. This section is the missing half: every source and pack
   with one status each, read from GET /knowledge/sources.

   It never downloads. A pack that was chosen and not downloaded shows its
   size and the exact command to run — copyable, not clickable. Adding a
   book from the dashboard is FindMore.tsx's job: it searches, shows the
   size and license, and downloads only after the owner's yes.
   ═══════════════════════════════════════════════════════════════ */

import { useEffect, useState, type CSSProperties } from "react";
import MsIcon from "../../_components/MsIcon";
import CopyCommand from "../../_components/CopyCommand";
import { getBuildSources } from "../../_lib/services/knowledgeService";
import type { BuildSource, BuildSourceStatus } from "../../_lib/services/knowledgeService";

const FREDOKA = "var(--font-fredoka), sans-serif";
const NUNITO = "var(--font-nunito), sans-serif";
const JBMONO = "var(--font-jbmono), monospace";

const panelBase: CSSProperties = {
  borderRadius: 28,
  border: "1px solid var(--line)",
  background: "var(--card)",
  backdropFilter: "blur(18px)",
  WebkitBackdropFilter: "blur(18px)",
  padding: "20px 24px",
  boxShadow: "var(--shadow)",
};

const microLabel: CSSProperties = {
  fontFamily: JBMONO,
  fontWeight: 700,
  fontSize: 11,
  letterSpacing: ".14em",
  color: "var(--text3)",
  textTransform: "uppercase",
};

const tint = (color: string, pct: number) =>
  `color-mix(in srgb, ${color} ${pct}%, transparent)`;

type StatusStyle = { label: string; icon: string; color: string };

const STATUS: Record<BuildSourceStatus, StatusStyle> = {
  indexed: { label: "Indexed", icon: "database", color: "var(--mint)" },
  fetched_not_indexed: {
    label: "Downloaded, not indexed",
    icon: "hourglass_top",
    color: "var(--lemon)",
  },
  selected_not_downloaded: {
    label: "Chosen, not downloaded",
    icon: "cloud_download",
    color: "var(--sky)",
  },
  offered: { label: "Suggested", icon: "lightbulb", color: "var(--text3)" },
};

const ORDER: BuildSourceStatus[] = [
  "indexed",
  "fetched_not_indexed",
  "selected_not_downloaded",
  "offered",
];

function SourceRow({ item }: { item: BuildSource }) {
  const style = STATUS[item.status] ?? STATUS.offered;
  const live = item.form === "live_source";
  return (
    <div
      style={{
        padding: "12px 14px",
        borderRadius: 18,
        background: "var(--card2)",
        border: "1px solid var(--line)",
      }}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2.5">
          <MsIcon
            name={live ? "public" : style.icon}
            size={18}
            style={{ color: style.color, flex: "none" }}
          />
          <span
            className="truncate"
            style={{
              fontFamily: NUNITO,
              fontWeight: 700,
              fontSize: 15,
              color: "var(--text)",
            }}
          >
            {item.label}
          </span>
          {live && (
            <span
              style={{
                ...microLabel,
                fontSize: 10,
                color: style.color,
                background: tint(style.color, 14),
                border: `1px solid ${tint(style.color, 30)}`,
                borderRadius: 999,
                padding: "2px 8px",
              }}
            >
              Live source
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {item.status === "selected_not_downloaded" && (
            <span style={{ ...microLabel, color: "var(--text2)" }}>
              {item.size_label}
            </span>
          )}
          {item.license && (
            <span style={{ ...microLabel, fontSize: 10 }}>{item.license}</span>
          )}
        </div>
      </div>

      {(item.what_it_is || item.note) && (
        <p
          style={{
            fontFamily: NUNITO,
            fontWeight: 500,
            fontSize: 14,
            lineHeight: 1.45,
            color: "var(--text2)",
            margin: "8px 0 0",
          }}
        >
          {item.what_it_is || item.note}
        </p>
      )}

      {item.what_it_is && item.note && (
        <p
          style={{
            fontFamily: NUNITO,
            fontWeight: 500,
            fontSize: 13.5,
            color: "var(--text3)",
            margin: "4px 0 0",
          }}
        >
          {item.note}
        </p>
      )}

      {live && item.source_url && (
        <p
          style={{
            fontFamily: JBMONO,
            fontSize: 12,
            color: "var(--text3)",
            margin: "6px 0 0",
            overflowWrap: "anywhere",
          }}
        >
          {item.source_url}
        </p>
      )}

      {item.command && (
        <div className="mt-2.5">
          <CopyCommand command={item.command} />
        </div>
      )}
    </div>
  );
}

export default function BuildSources({ libraryCount }: { libraryCount: number }) {
  const [items, setItems] = useState<BuildSource[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let alive = true;
    getBuildSources().then((data) => {
      if (!alive) return;
      setItems(data);
      setLoaded(true);
    });
    return () => {
      alive = false;
    };
  }, []);

  if (!loaded || items.length === 0) return null;
  // A full shelf with nothing outstanding needs no explanation; an empty or
  // partly-filled one is exactly where the gap has to be visible.
  const outstanding = items.some((i) => i.status !== "indexed");
  if (libraryCount > 0 && !outstanding) return null;

  const groups = ORDER.map((status) => ({
    status,
    rows: items.filter((i) => i.status === status),
  })).filter((g) => g.rows.length > 0);

  return (
    <div style={{ ...panelBase, marginBottom: 20 }}>
      <div className="mb-1 flex items-center gap-2.5">
        <MsIcon name="inventory_2" size={21} style={{ color: "var(--pink)" }} />
        <div
          style={{
            fontFamily: FREDOKA,
            fontWeight: 600,
            fontSize: 23,
            color: "var(--text)",
          }}
        >
          Sources for this build
        </div>
      </div>
      <p
        style={{
          fontFamily: NUNITO,
          fontWeight: 500,
          fontSize: 14.5,
          color: "var(--text2)",
          margin: "0 0 14px",
        }}
      >
        What the wizard chose, against what is on this machine.
      </p>

      <div className="flex flex-col gap-4">
        {groups.map((group) => (
          <div key={group.status}>
            <div className="mb-2 flex items-center gap-2" style={microLabel}>
              <MsIcon
                name={STATUS[group.status].icon}
                size={14}
                style={{ color: STATUS[group.status].color }}
              />
              {STATUS[group.status].label}: {group.rows.length}
            </div>
            <div className="flex flex-col gap-1.5">
              {group.rows.map((item) => (
                <SourceRow key={item.id} item={item} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
