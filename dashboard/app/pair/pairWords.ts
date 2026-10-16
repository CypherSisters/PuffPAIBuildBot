// What the pairing screen says about WHERE it is and HOW to get a code.
//
// Pure functions and constants, no imports, so a plain Node test can load this
// file with type stripping (app/pair/__tests__/pair-truth.test.mjs).
//
// WHY THIS EXISTS. The screen said "Connected to:
// http://localhost:8000" for every bot. The page was built ahead of time, so
// the address was whatever the BUILD saw, usually nothing, and the proxy's
// fallback was printed as if it had been checked. It also told owners to run
// `npm run pair:code`, which works only from the dashboard folder of the code
// copy, with a different separator in PowerShell. The real ways are the
// window that started the bot, and `puffpai pair`.

/** The ways to get a fresh pairing code, in the order an owner meets them. */
export const FRESH_CODE_WAYS: readonly string[] = [
  "Look in the window that started your bot (the Start file, or puffpai start). It shows a new code each time the bot starts.",
  "Or type puffpai pair on the computer running your bot. It shows this bot's code and until when it works.",
];

/** One short hint for the "too many wrong codes" message. */
export const FRESH_CODE_SHORT =
  "type puffpai pair on the computer running your bot for this bot's code";

/** The fallback the proxy uses when nothing told it where the bot is.
 *  Keep in step with app/api/pai/[...path]/route.ts. */
export const DEFAULT_BACKEND_URL = "http://localhost:8000";

export interface WhereLines {
  page: string | null;
  bot: string;
  botNote: string | null;
}

/**
 * The two addresses, in plain words.
 *
 * `pageOrigin` is the address in THIS browser's address bar
 * (window.location.origin), so it is null until the page is running in the
 * browser. `backendUrl` is what the proxy forwards to, read on the server when
 * the page is asked for; `configured` is false when nothing set it and the
 * fallback is in use, which is said out loud rather than dressed up as a
 * checked connection.
 */
export function whereLines(
  pageOrigin: string | null | undefined,
  backendUrl: string | null | undefined,
  configured: boolean,
): WhereLines {
  const bot = (backendUrl || "").trim() || DEFAULT_BACKEND_URL;
  return {
    page: pageOrigin ? `This page: ${pageOrigin}` : null,
    bot: `Your bot, as this page reaches it: ${bot}`,
    botNote: configured
      ? null
      : "Nothing told this page where your bot is, so it tries the usual address. If that is not your bot, start it with puffpai start.",
  };
}
