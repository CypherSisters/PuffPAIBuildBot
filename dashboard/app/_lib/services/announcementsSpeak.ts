/**
 * Which of the bot's messages this page should read out loud.
 *
 * Pure so it can be proven without a browser (scripts/announcements-speak-proof.ts).
 * Rules:
 *  - The first load only remembers what is there: an old backlog is never
 *    read out when the page opens.
 *  - After that, a message is read once, the first time it shows up, oldest
 *    first, and only while "Read updates out loud" is on.
 *  - A message the computer already said itself (spoken: true) is not read again.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

export interface SpeakableMessage {
  id: string;
  text: string;
  spoken?: boolean;
}

/**
 * Returns the messages to read now (oldest first) and the updated seen set.
 * `seen` is null before the first load. `feed` is newest first, as the
 * backend sends it.
 */
export function pickToSpeak<T extends SpeakableMessage>(
  seen: Set<string> | null,
  feed: T[],
  speakAloud: boolean,
): { toSpeak: T[]; seen: Set<string> } {
  const ids = feed.map((a) => a.id);
  if (seen === null) return { toSpeak: [], seen: new Set(ids) };
  const fresh = feed.filter((a) => !seen.has(a.id));
  const next = new Set(seen);
  ids.forEach((id) => next.add(id));
  if (!speakAloud) return { toSpeak: [], seen: next };
  return { toSpeak: fresh.reverse().filter((a) => !a.spoken), seen: next };
}
