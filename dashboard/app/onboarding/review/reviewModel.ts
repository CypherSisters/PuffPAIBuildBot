// The review screen's arithmetic and its refusals, with no React in sight so a
// proof script can run them (scripts/wizard-review-proof.ts).
//
// Two rules live here and nowhere else:
//
//  * The total NEVER sums a row whose basis is `unknown`. If any row's time is
//    unknown the total says "time unknown" rather than quietly dropping that
//    row and presenting a confident number.
//  * Staleness is ONE collapsed banner, never a badge per row.
//
// The old third rule -- [Build it] disabled until every heavy row had been
// opened -- is gone: the wizard ends on ONE
// summary that spells out every size and every outside service, and one
// "Looks good" approves it (../summaryModel.ts, wizard/proposal.py).
//
// Nothing here builds a command string: a gap's `fix_command` arrives rendered
// by the backend's fix registry and is only ever displayed.

import type {
  CardEstimate,
  ConsequentialReason,
  PlainSkill,
  WizardCard,
} from "../../_lib/services/wizardService";

// NO SKILL LIST LIVES IN THIS FILE. The order, the ids and the owner's words
// for a skill with no card all arrive from GET /wizard/cards (`order`,
// `not_converted`), which the backend builds from the runner's own schedule. A
// second copy here is exactly how the CLI's fourteen steps and the dashboard's
// eight became two different wizards.

/** How a row is named: `discover`, or `secure:vault` for a skill that asks
 *  more than one question. The same id the backend puts in `order`. */
export function cardId(card: Pick<WizardCard, "skill" | "part">): string {
  return card.part ? `${card.skill}:${card.part}` : card.skill;
}

/** Why this row is heavy, in one clause the owner can act on. */
export const REASON_WORDS: Record<ConsequentialReason, string> = {
  heavy_bytes: "a heavy download",
  runtime_egress: "turns on internet access while it runs",
  vault_pin_unset: "the vault PIN is not set",
  destructive_rerun: "running it again replaces what is there",
};

export function reasonText(reasons: ConsequentialReason[] | undefined): string {
  const words = (reasons || []).map((r) => REASON_WORDS[r]).filter(Boolean);
  return words.join(" · ");
}

/** Bytes in the units a person reads. Empty string for nothing to download.
 *
 *  **A GB here is 1024 MB.** That is what the source registry's `size_mb` and
 *  the model catalog's `disk_gb` already mean, and what the console prints.
 *  Formatting the same figure in powers of a thousand is how one Sources row
 *  said "about 57.2 GB" in its own words and "61.4 GB" in the estimate beside
 *  it. This is the mirror of `cards.format_bytes`; the two have to agree. */
const KB = 1024;
const MB = 1024 ** 2;
const GB = 1024 ** 3;

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes >= GB) {
    const gb = bytes / GB;
    return `${gb >= 100 ? Math.round(gb) : Math.round(gb * 10) / 10} GB`;
  }
  if (bytes >= MB) return `${Math.round(bytes / MB)} MB`;
  return `${Math.max(1, Math.round(bytes / KB))} kB`;
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "";
  // Zero is "no wait", not "1 sec": a second nobody measured is still a claim.
  if (seconds <= 0) return "";
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} sec`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  const hours = seconds / 3600;
  return `${hours >= 10 ? Math.round(hours) : Math.round(hours * 10) / 10} hr`;
}

/** One row's cost: bytes when there are any, then the time or the honest
 *  admission that nothing has measured it. */
export function rowCost(estimate: CardEstimate | undefined): string {
  const bytes = formatBytes(estimate?.bytes);
  if (!estimate) return "";
  if (estimate.basis === "unknown" || estimate.seconds === null) {
    return bytes ? `${bytes} · time unknown` : "time unknown";
  }
  const time = estimate.seconds ? formatSeconds(estimate.seconds) : "";
  return [bytes, time].filter(Boolean).join(" · ");
}

/** The total line.
 *
 *  **`basis` is about the TIME, not the bytes.** A card's bytes are a size the
 *  shipped registry (or the model catalog) declares for that download; what no
 *  build has measured is how long it takes on the owner's connection, and that
 *  is what `basis: "unknown"` says. So the bytes of an unknown-basis row are
 *  summed — leaving them out would under-count a 96 GB pack to zero — and the
 *  total says where its number comes from: "about 96 GB declared · time
 *  unknown". Time is stated only when every row knows its own; one unknown row
 *  and the whole total says so. The same rule is stated in
 *  `src/puffpai/wizard/cards.py`, which is where the estimate is built. */
export function totalLine(cards: WizardCard[]): string {
  let bytes = 0;
  let seconds = 0;
  let timeKnown = true;
  for (const card of cards) {
    const estimate = card.estimate;
    bytes += estimate?.bytes || 0;
    if (!estimate || estimate.basis === "unknown" || estimate.seconds === null) {
      timeKnown = false;
    } else {
      seconds += estimate.seconds;
    }
  }
  const size = bytes > 0 ? `about ${formatBytes(bytes)} declared` : "nothing to download";
  return `${size} · ${timeKnown ? formatSeconds(seconds) || "no wait" : "time unknown"}`;
}

/** What the screen says has happened so far.
 *
 *  It used to say "Nothing has been applied" whatever the state of the build,
 *  so an owner who had answered three rows was told none of them had taken.
 *  `applied` is the card ids the backend reports as settled (`GET
 *  /wizard/cards`). */
export function decidedLine(total: number, applied: Iterable<string> | undefined): string {
  const done = Array.from(new Set(applied || [])).length;
  if (done <= 0) return "Nothing has been applied. Open a row to change what it decided.";
  return `${done} of ${total} decided so far. Open a row to change what it decided.`;
}

/** One quiet block of `info` lines, with at most one small label.
 *
 *  Every info field carried its own label, so the starter-catalog suggestions
 *  read "SUGGESTED / Knowledge packs: / SUGGESTED / - Science…" — a label per
 *  line, each one looking like a suggestion the owner could answer. Consecutive
 *  info fields become one block: the first field is the block's heading when it
 *  is a label with nothing beside it, and a line repeating the label above it
 *  shows its text alone. An info line is never drawn as an answerable field. */
export interface InfoLine {
  id: string;
  label: string;
  text: string;
}

export interface InfoBlock {
  heading: string;
  lines: InfoLine[];
}

export function infoBlock(fields: { id: string; label?: string; current?: unknown }[]): InfoBlock {
  let heading = "";
  const lines: InfoLine[] = [];
  let previous = "";
  for (const field of fields) {
    const label = String(field.label ?? "");
    const text = field.current === null || field.current === undefined ? "" : String(field.current);
    if (!heading && !lines.length && !text) {
      heading = label;
      previous = label;
      continue;
    }
    // A block that has a heading carries that one label and no others: the
    // lines under it are the heading's own list, and labelling each of them
    // was the "SUGGESTED / SUGGESTED / SUGGESTED" noise.
    const repeats = Boolean(heading) || label === previous;
    lines.push({ id: field.id, label: repeats ? "" : label, text });
    if (label) previous = label;
  }
  return { heading, lines };
}

/** The card's fields in draw order: a run of `info` fields as one block, every
 *  other field on its own. */
export type FieldGroup<T> = { kind: "info"; fields: T[] } | { kind: "field"; field: T };

export function groupFields<T extends { kind: string }>(fields: T[]): FieldGroup<T>[] {
  const out: FieldGroup<T>[] = [];
  for (const field of fields) {
    if (field.kind !== "info") {
      out.push({ kind: "field", field });
      continue;
    }
    const last = out[out.length - 1];
    if (last && last.kind === "info") last.fields.push(field);
    else out.push({ kind: "info", fields: [field] });
  }
  return out;
}

/** ONE collapsed banner, never a badge per row. Null when nothing is stale. */
export function staleBanner(stale: Iterable<string> | undefined): string | null {
  const ids = Array.from(new Set(stale || []));
  if (ids.length === 0) return null;
  return ids.length === 1
    ? "1 thing needs rechecking"
    : `${ids.length} things need rechecking`;
}

export interface PlainRow {
  skill: string;
  title: string;
  note: string;
}

/** The skills this backend has no card for, exactly as the backend named them.
 *  They are listed rather than hidden: the step-by-step setup still answers
 *  them, and a row nobody can see is the failure this screen exists against. */
export function notConvertedRows(notConverted: PlainSkill[] | undefined): PlainRow[] {
  return (notConverted || []).map((row) => ({
    skill: row.skill,
    title: row.title || row.skill,
    note: "set in the step-by-step setup",
  }));
}

/** Which field a refusal is about, when the backend named one.
 *
 *  `cards.validate_answers` writes its refusals as "'field_id' takes ...", so
 *  the message can be shown beside the field that caused it instead of at the
 *  bottom of the card. A message naming nothing known stays a card-level error
 *  — it is never reworded here. */
export function fieldForError(message: string, fieldIds: Iterable<string>): string | null {
  const match = /'([a-z][a-z0-9_]{1,39})'/.exec(message || "");
  if (!match) return null;
  return new Set(fieldIds).has(match[1]) ? match[1] : null;
}

/** Cards in the backend's order, whatever order they arrived in. An id the
 *  order does not name sorts last rather than being dropped — a row nobody can
 *  see is worse than a row out of place. */
export function inSpecOrder(cards: WizardCard[], order: readonly string[]): WizardCard[] {
  const rank = new Map(order.map((id, i) => [id, i]));
  return [...cards].sort(
    (a, b) => (rank.get(cardId(a)) ?? 999) - (rank.get(cardId(b)) ?? 999),
  );
}
