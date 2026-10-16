import type { KnowledgeLibrary, KnowledgeZone } from "../../_interfaces/api";

/* Presentation helpers shared by the bookshelf page and the standalone viewer.
   The /knowledge/libraries payload is heterogeneous: ZIM packs discovered on
   disk report zone "knowledge", a byte count for size, and no chunk count,
   while legacy ChromaDB rows report numeric zones and pre-formatted sizes.
   Everything here degrades to something printable rather than NaN/undefined. */

export type ZoneBadge = {
  /** Compact form for book spines and list rows. */
  short: string;
  /** Long form for headers and the About tab. */
  long: string;
  /** kb-zone-pill modifier class. */
  cls: string;
};

export function zoneBadge(zone: KnowledgeZone | undefined | null): ZoneBadge {
  const raw = typeof zone === "string" ? zone.trim().toLowerCase() : zone;

  if (raw === 1 || raw === "1" || raw === "vault" || raw === "personal")
    return { short: "Vault", long: "Your locked vault", cls: "kb-zone-pill-1" };

  if (raw === 2 || raw === "2" || raw === "general")
    return { short: "General", long: "General Knowledge", cls: "kb-zone-pill-2" };

  if (raw === "knowledge")
    return { short: "Knowledge", long: "Offline Knowledge Pack", cls: "kb-zone-pill-k" };

  return { short: "General", long: "General Knowledge", cls: "kb-zone-pill-2" };
}

/** Chunk count as a number, 0 when the row carries none. */
export function chunkCount(lib: Pick<KnowledgeLibrary, "chunk_count">): number {
  const n = Number(lib.chunk_count);
  return Number.isFinite(n) && n > 0 ? n : 0;
}

export function formatChunks(count: number | undefined | null): string {
  const n = Number(count);
  if (!Number.isFinite(n) || n <= 0) return "0";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return String(n);
}

/** ZIM rows send raw bytes; legacy rows send a already-formatted string. */
export function formatSize(size: string | number | undefined | null): string {
  if (size === null || size === undefined || size === "") return "";
  if (typeof size === "string") return size;
  const n = Number(size);
  if (!Number.isFinite(n) || n <= 0) return "";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 100 || i === 0 ? 0 : 1)} ${units[i]}`;
}
