// The one summary, as pure functions — the browser half of wizard/proposal.py.
//
// One plain summary lists every download
// size and every outside service; ONE "Looks good" approves it; "Change
// something" opens any item. Nothing here decides what is on the plan — the
// backend does. These helpers only draw it and collect changes, and they are
// proven without a browser by scripts/wizard-review-proof.ts.
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.

import type {
  Summary,
  SummaryChanges,
  SummaryChoice,
  SummaryItem,
} from "../_lib/services/wizardService";

/** An item that is a statement, not a switch (proposal.INFO_KIND). */
export const INFO_KIND = "none";

/** At most this many choices at once in the simple wording (under 13). */
export const SIMPLE_MAX_CHOICES = 3;

/** The id "Change something" uses for "do not download a brain now". */
export const BRAIN_OFF = "__off__";

/** A GB is 1024 MB everywhere in this wizard (cards.format_bytes). */
export function formatBytes(bytes: number | null | undefined): string {
  const value = Number(bytes) || 0;
  if (value <= 0) return "";
  const GB = 1024 ** 3;
  const MB = 1024 ** 2;
  if (value >= GB) {
    const gb = value / GB;
    return gb >= 100 ? `${Math.round(gb)} GB` : `${Math.round(gb * 10) / 10} GB`;
  }
  if (value >= MB) return `${Math.round(value / MB)} MB`;
  return `${Math.max(1, Math.round(value / 1024))} kB`;
}

export function toggleable(item: SummaryItem): boolean {
  return item.apply?.kind !== INFO_KIND;
}

export function isBrain(item: SummaryItem): boolean {
  return item.section === "thinking" && ["model", "model_pull"].includes(item.apply?.kind);
}

/** Every line in the order it is drawn: the job groups of "What it will do"
 *  first, then the sections (wizard/proposal.py items_of). */
export function itemsOf(summary: Summary): SummaryItem[] {
  return [
    ...(summary.jobs ?? []).flatMap((group) => group.items),
    ...summary.sections.flatMap((section) => section.items),
  ];
}

/** Every item "Change something" may open, in summary order — the same
 *  numbering the terminal prints. */
export function numbered(summary: Summary): SummaryItem[] {
  return itemsOf(summary).filter(toggleable);
}

export function emptyChanges(): SummaryChanges {
  return { on: {} };
}

/** A new changes object with one item switched on or off. Never mutates. */
export function withToggle(
  changes: SummaryChanges,
  id: string,
  on: boolean,
): SummaryChanges {
  return { ...changes, on: { ...changes.on, [id]: on } };
}

/** A new changes object for a brain picked from the list (or none). */
/** A tick box on the plan. Ticking a line that needs another one (the locked
 *  box) ticks that one too, so the backend's rule "a line is off while what
 *  it needs is off" never undoes the owner's own tick. Never mutates. */
export function withTick(
  summary: Summary,
  changes: SummaryChanges,
  item: SummaryItem,
  on: boolean,
): SummaryChanges {
  let next = withToggle(changes, item.id, on);
  if (on && item.requires?.length) {
    for (const other of itemsOf(summary)) {
      if (!other.on && item.requires.includes(other.apply?.kind)) {
        next = withToggle(next, other.id, true);
      }
    }
  }
  return next;
}

export function withBrain(
  changes: SummaryChanges,
  item: SummaryItem,
  value: string,
): SummaryChanges {
  if (value === BRAIN_OFF) {
    const next: SummaryChanges = { ...changes, on: { ...changes.on, [item.id]: false } };
    delete next.model;
    return next;
  }
  return { ...changes, model: value, on: { ...changes.on, [item.id]: true } };
}

export function withReach(changes: SummaryChanges, value: string): SummaryChanges {
  return { ...changes, reach: value };
}

/** Is the town field drawn on this line? Only on the one line the backend
 *  named, and only while that line is on. */
export function townOnLine(summary: Summary, item: SummaryItem): boolean {
  return Boolean(summary.town?.ask && item.on && summary.town.item === item.id);
}

/** The changes "Looks good" sends: the town rides along only when the plan
 *  asked for it (an empty one is a skip). Never mutates. */
export function withTown(
  changes: SummaryChanges,
  summary: Summary,
  town: string,
): SummaryChanges {
  if (!summary.town?.ask) return changes;
  return { ...changes, town: town.trim().replace(/\s+/g, " ") };
}

/** The choices shown at once: all of them, or three at a time for a young
 *  builder, with `more` saying whether another page exists. */
export function choicePage(
  choices: SummaryChoice[],
  mode: string,
  page: number,
): { shown: SummaryChoice[]; more: boolean } {
  if (mode !== "simple") return { shown: choices, more: false };
  const start = page * SIMPLE_MAX_CHOICES;
  const shown = choices.slice(start, start + SIMPLE_MAX_CHOICES);
  return { shown, more: start + SIMPLE_MAX_CHOICES < choices.length };
}

/** "Not included (you can add it): TED telescopes - about 301 MB, free for
 *  personal use only" -- the backend's words (proposal.off_line), which the
 *  terminal prints too. The fallback is the same prefix without a reason. */
export const OFF_PREFIX = "Not included (you can add it)";

export function offLine(item: SummaryItem): string {
  return item.off_line || `${OFF_PREFIX}: ${item.label}`;
}

/** The off line under a tick box whose title already names the line: the
 *  name is not said twice ("Not included (you can add it): off until you
 *  switch it on"). */
export function offLineShort(item: SummaryItem): string {
  const full = offLine(item);
  const head = `${OFF_PREFIX}: `;
  if (!full.startsWith(head)) return full;
  const rest = full.slice(head.length);
  const name = String(item.label ?? "").replace(/\s*\((?:about|up to) [^)]*\)\s*$/, "");
  if (!name || !rest.startsWith(name)) return full;
  const tail = rest.slice(name.length).replace(/^\s*-\s*/, "").trim();
  return tail ? `${head}${tail}` : OFF_PREFIX;
}

/** An unticked find made for the owner's jobs still shows what it is (its
 *  own words, stars, licence, how it opens): "Not included" alone hides it.
 *  The same rule as wizard/proposal.py detail_when_off. */
export function detailWhenOff(item: SummaryItem): boolean {
  return Boolean(item.detail) && Boolean(item.need || item.facts);
}

/** Every piece of text the summary puts in front of a person, flattened. The
 *  plain-words proof runs the banned list over exactly this. */
export function summaryTexts(summary: Summary): string[] {
  const out: string[] = [summary.title, summary.intro];
  out.push(...(summary.checked ?? []));
  if (summary.jobs?.length) out.push(summary.jobs_heading ?? "");
  for (const group of summary.jobs ?? []) {
    out.push(group.heading, ...group.notes, group.not_reached);
    for (const item of group.items) out.push(item.label, item.detail, offLine(item));
  }
  for (const section of summary.sections) {
    out.push(section.heading, section.empty, ...(section.notes ?? []));
    for (const item of section.items) out.push(item.label, item.detail, offLine(item));
  }
  out.push(summary.reach.label, ...summary.reach.choices.map((c) => c.label));
  out.push(...summary.model_choices.map((c) => c.label));
  out.push(...summary.actions.map((a) => a.label));
  out.push(summary.lines.total ?? "", summary.lines.leaves ?? "");
  if (summary.town?.ask) out.push(summary.town.question, summary.town.help, summary.town.skip);
  return out.filter(Boolean);
}

// ── plain words ──────────────────────────────────────────────────────────────
//
// The same list tests/test_wizard_plain_words.py holds the backend to. A word
// here is matched whole, case-insensitively.

export const BANNED_WORDS = [
  "door",
  "doors",
  "posture",
  "tier",
  "tiers",
  "surface",
  "surfaces",
  "manifest",
  "zone",
  "zones",
  "egress",
  "sealed",
  "island",
  "enumerated",
  "moat",
  "open_yard",
  "fenced",
  "drawbridge_up",
  "routing",
  "sourcing",
  "catalog",
  "catalogs",
] as const;

/** The banned words (and "status:" / bracketed ids) found in `text`. */
export function bannedIn(text: string): string[] {
  const found: string[] = [];
  for (const word of BANNED_WORDS) {
    if (new RegExp(`(^|[^A-Za-z_])${word}([^A-Za-z_]|$)`, "i").test(text)) found.push(word);
  }
  if (/status:/i.test(text)) found.push("status:");
  if (/\[[a-z_]+(:[a-z_]+)?\]/.test(text)) found.push("[bracketed id]");
  return found;
}

// ── the vault PIN ────────────────────────────────────────────────────────────
//
// The rules live in ../_lib/vaultPin.ts, shared with the Vault page's
// "Forgot your PIN?". Re-exported here so this file's callers are unchanged.

export { pinRefusal } from "../_lib/vaultPin";
