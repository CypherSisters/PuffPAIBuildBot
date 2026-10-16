// The Mood board's folder reply, said plainly. Pure, with no React import,
// so the proof script runs it under plain node.
//
// Copyright (c) Cypher Sisters. Apache-2.0 License.

/** Said only when the bot gave no reason of its own (it did not answer). */
export const FOLDER_NO_ANSWER = "Your bot did not answer just now. Nothing was changed.";

/** The line to show after a folder was given: the bot's own words ("Looked at
 *  6 pictures ...", "Only folders inside Pictures, Documents, ...", "That
 *  folder is not there"), whether it worked or not. */
export function folderReply(ok: boolean, data: unknown): string {
  const d = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const said = [d.line, d.message].find((v) => typeof v === "string" && v.trim());
  if (typeof said === "string") return said.trim().slice(0, 300);
  return ok ? "" : FOLDER_NO_ANSWER;
}
