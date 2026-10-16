// The bot's own personality (src/puffpai/persona.py): who it is, how it
// talks, what it just learned, its icon and colour. Every dashboard looks and
// sounds like its own bot because these come from here.
//
//   GET  /bot/persona          the personality (made on first ask)
//   POST /bot/persona/refresh  bring its "what I can do" list up to date
//   POST /bot/persona/woke     the wake-up was shown for this build
import { apiGet, apiPost } from "../api";

export interface BotPersona {
  name: string;
  /** Plain kind phrase, like "wedding planner". */
  kind: string;
  traits: string[];
  tone: string;
  greeting: string;
  /** 2 to 4 sentences in its own words, ending "Let me show you around."
   *  The welcome tour's first step can read this. */
  intro: string;
  /** A Material Symbols name. */
  icon: string;
  /** A hex colour. */
  accent: string;
  things: string[];
  made_with: "brain" | "fallback";
  build: string;
  /** True until the wake-up was shown for this build. */
  wake: boolean;
}

export const PERSONA_CHANGED = "puffpai:persona-changed";

export async function getBotPersona(): Promise<BotPersona | null> {
  try {
    return await apiGet<BotPersona>("/bot/persona", { cache: "no-store" });
  } catch {
    return null;
  }
}

export async function markBotWoke(): Promise<BotPersona | null> {
  try {
    return await apiPost<BotPersona>("/bot/persona/woke");
  } catch {
    return null;
  }
}
