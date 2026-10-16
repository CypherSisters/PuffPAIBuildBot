/**
 * Handing one sentence to the chat page.
 *
 * The quick-search pill has done this since it was written: stash the text, go
 * to /dashboard/chat, and the page reads it once, clears it, and sends it as a
 * normal turn. Two more callers want the same thing — the "Try: add tomatoes to
 * the plantings list" chip on the home board, and the "Say" hints on an app's
 * own page — so the key and the stash live here instead of being imported out of
 * a client component that happens to have declared them first.
 *
 * sessionStorage, not a query string: the sentence is the owner's own words
 * about their own lists, and a URL is the one place on this box that gets
 * written to a history file and read back by anything that renders a link.
 *
 * Copyright (c) Cypher Sisters. Apache-2.0 License.
 */

/** sessionStorage key the chat page reads+clears to pick up a handed-off query. */
export const PENDING_CHAT_QUERY_KEY = "puffpai.pendingChatQuery";

/**
 * Stash `text` for the chat page. Returns false when it could not be stashed
 * (private mode, storage full), so the caller can stay put and say so rather
 * than navigating to a chat that will sit there empty.
 */
export function stashChatQuery(text: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  try {
    sessionStorage.setItem(PENDING_CHAT_QUERY_KEY, trimmed);
    return true;
  } catch {
    return false;
  }
}
