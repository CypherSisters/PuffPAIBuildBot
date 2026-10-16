/**
 * Human-readable conversation titles.
 *
 * The backend stores conversations with no title field, so every list view
 * used to fall back to "Chat 14b96f6d". Until the backend grows real topic
 * titles, derive one from the first thing the user said — which is almost
 * always the topic ("can you see any smart home devices…").
 */

export interface TitledConversation {
  id: string;
  title?: string | null;
  created_at?: string;
  messages?: { role: string; content: string }[];
}

const MAX_LEN = 44;

export function conversationTitle(conv: TitledConversation): string {
  const stored = (conv.title ?? "").trim();
  if (stored) return stored;

  const firstUser = conv.messages?.find(
    (m) => m.role === "user" && m.content?.trim()
  );
  if (firstUser) {
    let clean = firstUser.content.replace(/\s+/g, " ").trim();
    clean = clean.charAt(0).toUpperCase() + clean.slice(1);
    if (clean.length <= MAX_LEN) return clean;
    const cut = clean.slice(0, MAX_LEN);
    const lastSpace = cut.lastIndexOf(" ");
    // Break at a word when one is reasonably close to the limit.
    return `${cut.slice(0, lastSpace > MAX_LEN / 2 ? lastSpace : MAX_LEN)}…`;
  }

  if (conv.created_at) {
    const d = new Date(conv.created_at);
    if (!isNaN(d.getTime())) {
      return `Chat, ${d.toLocaleDateString([], {
        month: "short",
        day: "numeric",
      })}`;
    }
  }
  return `Chat ${conv.id.slice(0, 8)}`;
}
