/**
 * Reader transport + text extraction. Server-side only.
 *
 * WHY node:http INSTEAD OF fetch()
 * --------------------------------
 * Two things global fetch() cannot give us:
 *
 *  1. **IP pinning.** We resolve the hostname, approve the address, and then
 *     connect to THAT ADDRESS, passing the hostname only as a Host header and
 *     TLS SNI. fetch() re-resolves inside undici, which reopens the DNS
 *     rebinding window between "we approved 93.184.216.34" and "the socket
 *     landed on 127.0.0.1". With a pinned IP the socket can never reach an
 *     address urlGuard did not approve — the rebinding hole is closed at the
 *     transport, not papered over with a short time window.
 *  2. **A hard byte cap on the wire.** We destroy the socket the moment the
 *     response passes MAX_BYTES, rather than buffering a hostile 5 GB body.
 *
 * Redirects are handled by hand for the same reason: every hop is re-validated
 * from scratch (scheme, userinfo, port, DNS, address range), so a public host
 * that 302s to http://192.168.50.20:8123 is refused at the hop, not followed.
 *
 * EGRESS IDENTITY (Zone 3 / the privacy posture written on the Settings page)
 * The outbound request carries no cookies, no Referer, no household-specific
 * anything. The User-Agent is a fixed generic desktop string, identical on
 * every PuffPAI appliance: sending none at all gets a large fraction of news
 * sites to 403, and a PuffPAI-branded one would tell every publisher the
 * household runs this appliance. A constant, unremarkable string is the
 * privacy-maximising choice that still works.
 *
 * puff puff pass
 */
import http from "node:http";
import https from "node:https";
import type { IncomingMessage } from "node:http";
import {
  resolveRedirect,
  validateTarget,
  type TargetValidator,
  // Explicit .ts extension: this module is loaded directly by Node (not only
  // by the bundler) in app/_lib/server/__tests__/reader-guard.test.mjs, and
  // Node's ESM resolver does not guess extensions. Needs
  // allowImportingTsExtensions in tsconfig.
} from "./urlGuard.ts";

/* ── caps ──────────────────────────────────────────────────────────── */

/** Whole operation, redirects included. */
export const TOTAL_TIMEOUT_MS = 10_000;
/** Hard cap on bytes read off the wire. Socket is destroyed past this. */
export const MAX_BYTES = 2 * 1024 * 1024;
/** Redirect hops after the first request. */
export const MAX_REDIRECTS = 4;
/** Cap on the text handed back to the browser. */
export const MAX_TEXT_CHARS = 40_000;

const ALLOWED_CONTENT_TYPES = ["text/html", "text/plain"];

/**
 * Constant across every appliance — see EGRESS IDENTITY above. Not a lie about
 * being a browser so much as a refusal to be a fingerprint.
 */
const UA =
  "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36";

export interface ReaderArticle {
  title: string;
  host: string;
  paragraphs: string[];
  /** Final URL after redirects — the UI shows the host it actually read. */
  url: string;
  truncated: boolean;
}

export interface ReaderFail {
  ok: false;
  reason: string;
  detail?: string;
}
export type ReaderResult = ({ ok: true } & ReaderArticle) | ReaderFail;

export const FETCH_REASON = {
  TIMEOUT: "timeout",
  NETWORK: "network_error",
  TOO_MANY_REDIRECTS: "too_many_redirects",
  BAD_REDIRECT: "bad_redirect",
  CONTENT_TYPE: "unsupported_content_type",
  TOO_LARGE: "response_too_large",
  HTTP_ERROR: "http_error",
  EMPTY: "no_readable_text",
  /** Data for programs (an API's JSON, even inside an HTML "browsable API"
   *  page), not a page to read. Never rendered. */
  NOT_A_PAGE: "not_a_page",
} as const;

/* ── one hop ───────────────────────────────────────────────────────── */

interface HopResult {
  status: number;
  location: string | null;
  contentType: string;
  body: string;
}

/**
 * One request to a PRE-APPROVED ip. `url` supplies the path and the Host /
 * SNI name only — the socket goes to `ip` and nowhere else.
 */
function requestHop(
  url: URL,
  ip: string,
  family: 4 | 6,
  deadline: number,
): Promise<HopResult> {
  return new Promise((resolve, reject) => {
    // Every path below funnels through these two so a socket teardown can
    // never both resolve and reject (or fire twice).
    let settled = false;
    const done = (v: HopResult) => {
      if (settled) return;
      settled = true;
      resolve(v);
    };
    const failWith = (msg: string) => {
      if (settled) return;
      settled = true;
      reject(new Error(msg));
    };

    const isHttps = url.protocol === "https:";
    const mod = isHttps ? https : http;
    const remaining = deadline - Date.now();
    if (remaining <= 0) {
      failWith(FETCH_REASON.TIMEOUT);
      return;
    }

    const isIpHost = url.hostname[0] === "[" || /^[\d.]+$/.test(url.hostname);

    const req = mod.request(
      {
        host: ip,
        family,
        port: url.port ? parseInt(url.port, 10) : isHttps ? 443 : 80,
        method: "GET",
        path: url.pathname + url.search,
        // TLS identity is checked against the NAME, not the pinned IP — so
        // pinning does not weaken certificate validation. Omitted for IP-literal
        // hosts, where SNI is invalid and Node falls back to checking the cert
        // against the address itself (correct behaviour).
        servername: isHttps && !isIpHost ? url.hostname : undefined,
        rejectUnauthorized: true,
        headers: {
          // url.host keeps the port and the [] around an IPv6 literal.
          Host: url.host,
          "User-Agent": UA,
          Accept: "text/html,text/plain;q=0.9,*/*;q=0.1",
          "Accept-Language": "en",
          // identity, NOT gzip: the MAX_BYTES cap is applied to bytes on the
          // wire, so a compressed body would let a 2 MB cap admit a multi-GB
          // decompression bomb. Refusing compression keeps the cap honest.
          "Accept-Encoding": "identity",
          Connection: "close",
          // No Cookie. No Referer. No Origin. Nothing household-specific.
        },
      },
      (res: IncomingMessage) => {
        const status = res.statusCode || 0;
        const location =
          typeof res.headers.location === "string" ? res.headers.location : null;
        const contentType = String(res.headers["content-type"] || "");

        // A redirect: we only need the Location header. Kill the socket rather
        // than read a body we are going to throw away.
        if (status >= 300 && status < 400) {
          res.destroy();
          done({ status, location, contentType, body: "" });
          return;
        }

        const mime = contentType.split(";")[0].trim().toLowerCase();
        if (ALLOWED_CONTENT_TYPES.indexOf(mime) < 0) {
          res.destroy();
          req.destroy();
          failWith(FETCH_REASON.CONTENT_TYPE + ":" + (mime || "none"));
          return;
        }

        // Oversize is a HARD FAILURE, not a silent truncation — both when the
        // server declares it and when it only becomes apparent mid-stream. A
        // >2 MB HTML document is not an article, and quietly returning the
        // first 2 MB would hide the cap from the reader. The declared-length
        // check is just an early-out; the streaming counter is the real
        // enforcement, since Content-Length is attacker-controlled.
        const declared = parseInt(String(res.headers["content-length"] || ""), 10);
        if (Number.isFinite(declared) && declared > MAX_BYTES) {
          res.destroy();
          req.destroy();
          failWith(FETCH_REASON.TOO_LARGE);
          return;
        }

        const chunks: Buffer[] = [];
        let seen = 0;
        res.on("data", (chunk: Buffer) => {
          seen += chunk.length;
          if (seen > MAX_BYTES) {
            res.destroy();
            req.destroy();
            failWith(FETCH_REASON.TOO_LARGE);
            return;
          }
          chunks.push(chunk);
        });
        res.on("end", () => {
          done({
            status,
            location,
            contentType,
            body: Buffer.concat(chunks).toString("utf8"),
          });
        });
        res.on("close", () => failWith(FETCH_REASON.NETWORK));
        res.on("error", () => failWith(FETCH_REASON.NETWORK));
      },
    );

    req.setTimeout(remaining, () => {
      req.destroy(new Error(FETCH_REASON.TIMEOUT));
    });
    req.on("error", (e: Error) => {
      failWith(
        e.message === FETCH_REASON.TIMEOUT
          ? FETCH_REASON.TIMEOUT
          : FETCH_REASON.NETWORK,
      );
    });
    req.end();
  });
}

/* ── the redirect loop ─────────────────────────────────────────────── */

/**
 * Fetch and extract an article.
 *
 * `validate` exists ONLY so the test harness can point the transport at a
 * loopback fixture server. **Production callers must never pass it** — the
 * route calls `fetchArticle(url)` with one argument, so the real
 * validateTarget (which refuses every private address) is always in force.
 */
export async function fetchArticle(
  rawUrl: unknown,
  validate: TargetValidator = validateTarget,
): Promise<ReaderResult> {
  const deadline = Date.now() + TOTAL_TIMEOUT_MS;
  let next: unknown = rawUrl;

  for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
    // EVERY hop, including the first, goes through the full guard.
    const target = await validate(next);
    if (!target.ok) return target as ReaderFail;

    let res: HopResult;
    try {
      res = await requestHop(target.url, target.ip, target.family, deadline);
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      if (msg.indexOf(FETCH_REASON.CONTENT_TYPE) === 0) {
        return {
          ok: false,
          reason: FETCH_REASON.CONTENT_TYPE,
          detail: msg.slice(FETCH_REASON.CONTENT_TYPE.length + 1),
        };
      }
      if (msg === FETCH_REASON.TOO_LARGE) {
        return { ok: false, reason: FETCH_REASON.TOO_LARGE };
      }
      if (msg === FETCH_REASON.TIMEOUT) {
        return { ok: false, reason: FETCH_REASON.TIMEOUT };
      }
      return { ok: false, reason: FETCH_REASON.NETWORK };
    }

    if (res.status >= 300 && res.status < 400) {
      if (hop === MAX_REDIRECTS) {
        return { ok: false, reason: FETCH_REASON.TOO_MANY_REDIRECTS };
      }
      const loc = res.location ? resolveRedirect(target.url, res.location) : null;
      if (!loc) return { ok: false, reason: FETCH_REASON.BAD_REDIRECT };
      next = loc;
      continue;
    }

    if (res.status < 200 || res.status >= 300) {
      return {
        ok: false,
        reason: FETCH_REASON.HTTP_ERROR,
        detail: String(res.status),
      };
    }

    const mime = res.contentType.split(";")[0].trim().toLowerCase();
    const extracted =
      mime === "text/plain"
        ? extractPlain(res.body)
        : extractArticle(res.body);

    if (extracted.paragraphs.length === 0) {
      return { ok: false, reason: FETCH_REASON.EMPTY, detail: target.hostname };
    }
    if (looksLikeData(extracted.paragraphs)) {
      // A raw API body is never shown as if it were an article.
      return { ok: false, reason: FETCH_REASON.NOT_A_PAGE, detail: target.hostname };
    }
    return {
      ok: true,
      title: extracted.title || target.hostname,
      host: target.hostname,
      url: target.url.toString(),
      paragraphs: extracted.paragraphs,
      truncated: extracted.truncated,
    };
  }
  return { ok: false, reason: FETCH_REASON.TOO_MANY_REDIRECTS };
}

/* ── extraction (pure) ─────────────────────────────────────────────── */

const NAMED_ENTITIES: Record<string, string> = {
  amp: "&",
  lt: "<",
  gt: ">",
  quot: '"',
  apos: "'",
  nbsp: " ",
  ndash: "–",
  mdash: "—",
  lsquo: "‘",
  rsquo: "’",
  ldquo: "“",
  rdquo: "”",
  hellip: "…",
  eacute: "é",
  egrave: "è",
  agrave: "à",
  ccedil: "ç",
  uuml: "ü",
  ouml: "ö",
  auml: "ä",
  szlig: "ß",
  copy: "©",
  reg: "®",
  trade: "™",
  deg: "°",
  bull: "•",
  middot: "·",
  laquo: "«",
  raquo: "»",
  euro: "€",
  pound: "£",
  times: "×",
  minus: "−",
};

/** PURE. Decode the entity forms that actually show up in article prose. */
export function decodeEntities(s: string): string {
  return s.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]{1,31});/g, (m, ent: string) => {
    if (ent[0] === "#") {
      const hex = ent[1] === "x" || ent[1] === "X";
      const code = parseInt(hex ? ent.slice(2) : ent.slice(1), hex ? 16 : 10);
      if (!Number.isFinite(code) || code < 0 || code > 0x10ffff) return m;
      // Lone surrogates are unpaired garbage; C0 controls (NUL especially) are
      // never legitimate article text and a NUL in a JSON string is the kind of
      // thing that confuses whatever reads it next. Both are dropped, not
      // passed through.
      if (code >= 0xd800 && code <= 0xdfff) return "";
      if (code < 0x20 && code !== 0x09 && code !== 0x0a) return "";
      try {
        return String.fromCodePoint(code);
      } catch {
        return m;
      }
    }
    const named = NAMED_ENTITIES[ent.toLowerCase()];
    return named === undefined ? m : named;
  });
}

/**
 * Elements whose text is never article prose. Removed content-and-all.
 *
 * `<noscript>` is DELIBERATELY ABSENT. This reader executes no JavaScript, so
 * noscript content is written for exactly our kind of client — and some real
 * pages put everything there. neverssl.com is the whole page: dropping
 * noscript returned "no readable text" for a page that is 100% prose. Its tags
 * are stripped like any other markup and the content is kept.
 */
const DROP_ELEMENTS =
  /<(script|style|svg|math|template|iframe|object|embed|canvas|form|select|button|nav|header|footer|aside|figure|figcaption)\b[^>]*>[\s\S]*?<\/\1\s*>/gi;

/** Same, for the self-closing / unterminated stragglers. */
const DROP_SELF = /<(script|style|svg|iframe|link|meta|input)\b[^>]*\/?>/gi;

function stripChrome(html: string): string {
  let out = html;
  // Two passes: nested <div><nav>…</nav></div> style chrome often needs it.
  out = out.replace(DROP_ELEMENTS, " ");
  out = out.replace(DROP_ELEMENTS, " ");
  out = out.replace(DROP_SELF, " ");
  // <head> wholesale (after <title> has been read by the caller).
  out = out.replace(/<head\b[^>]*>[\s\S]*?<\/head\s*>/gi, " ");
  // Comments, including conditional ones.
  out = out.replace(/<!--[\s\S]*?-->/g, " ");
  return out;
}

/**
 * Where one run of prose ends and the next begins.
 *
 * WHY A BOUNDARY SPLIT AND NOT `<p>…</p>` PAIRS
 * Real pages do not close their paragraphs. The NPR story this feature was
 * built for has 196 `<p` opens and 8 `</p>` closes — HTML5 auto-closes `<p>`
 * at the next block element, so a matched-pair extractor sees 4% of the
 * article. Splitting on block boundaries handles closed and unclosed markup
 * identically, and inline tags (<a>, <em>, <strong>) simply merge into the
 * surrounding sentence instead of shattering it.
 */
const BLOCK_BOUNDARY =
  /<\/?(?:p|div|h[1-6]|li|ul|ol|dl|dd|dt|blockquote|pre|section|article|main|table|thead|tbody|tfoot|tr|td|th|br|hr|figcaption|address|details|summary|fieldset|legend|caption)\b[^>]*>/gi;

function blocksOf(region: string): string[] {
  // BLOCK_BOUNDARY has only non-capturing groups, so split() drops the
  // delimiters instead of interleaving them — no sentinel character needed.
  const parts = region.split(BLOCK_BOUNDARY);
  const out: string[] = [];
  for (let i = 0; i < parts.length && out.length < 4000; i++) {
    const t = cleanText(parts[i]);
    if (t) out.push(t);
  }
  return out;
}

interface Candidate {
  /** Raw markup length — a proxy for how much chrome comes with the prose. */
  size: number;
  blocks: string[];
  /** Characters of prose long enough to be prose. */
  chars: number;
}

function candidate(region: string): Candidate {
  const blocks = blocksOf(region);
  let chars = 0;
  for (let i = 0; i < blocks.length; i++) {
    if (blocks[i].length >= 24) chars += blocks[i].length;
  }
  return { size: region.length, blocks, chars };
}

/**
 * Pick the region that actually holds the article.
 *
 * NOT "prefer <article>, then <main>, then <body>" — that was the first
 * implementation and it failed on the very first real page tried. On NPR the
 * `<article>` element wraps the headline and the audio player and CLOSES 27
 * bytes before the story text begins; the prose lives in a sibling div. Blind
 * preference returned two paragraphs out of ninety.
 *
 * So: score every candidate by how much prose it yields, then choose the
 * NARROWEST region that still carries at least 60% of the best score. Narrow
 * means least surrounding chrome; the 60% floor stops a small teaser block
 * from beating the real body.
 */
function pickRegion(html: string): Candidate | null {
  const cands: Candidate[] = [];
  const add = (region: string | undefined, minSize: number) => {
    if (!region || region.length < minSize) return;
    cands.push(candidate(region));
  };

  let m: RegExpExecArray | null;
  const artRe = /<article\b[^>]*>([\s\S]*?)<\/article\s*>/gi;
  for (let n = 0; n < 8 && (m = artRe.exec(html)) !== null; n++) add(m[1], 400);
  const mainRe = /<main\b[^>]*>([\s\S]*?)<\/main\s*>/gi;
  for (let n = 0; n < 4 && (m = mainRe.exec(html)) !== null; n++) add(m[1], 400);

  const body = /<body\b[^>]*>([\s\S]*?)<\/body\s*>/i.exec(html);
  // The body fallback has no minimum — a short page is still a page.
  add(body ? body[1] : html, 0);

  if (cands.length === 0) return null;
  let best = cands[0];
  for (let i = 1; i < cands.length; i++) {
    if (cands[i].chars > best.chars) best = cands[i];
  }
  let chosen = best;
  for (let i = 0; i < cands.length; i++) {
    if (cands[i].chars >= best.chars * 0.6 && cands[i].size < chosen.size) {
      chosen = cands[i];
    }
  }
  return chosen;
}

/** Zero-width / bidi / BOM characters some CMSes sprinkle through prose. */
const INVISIBLES = /[​-‏‪-‮⁠-⁤﻿]/g;

function cleanText(s: string): string {
  return decodeEntities(s.replace(/<[^>]*>/g, " "))
    .replace(INVISIBLES, "")
    .replace(/\s+/g, " ")
    .trim();
}

export interface Extracted {
  title: string;
  paragraphs: string[];
  truncated: boolean;
}

/**
 * PURE. HTML → title + plain-text paragraphs.
 *
 * There is deliberately no DOM here and the OUTPUT IS PLAIN STRINGS ONLY: the
 * overlay renders them as text nodes, never as markup. Nothing this function
 * returns can carry a tag, a style, a script or a remote image reference, so
 * a hostile page cannot get anything executable or network-touching into the
 * dashboard's origin. Any parsing bug here degrades to "worse text", never to
 * "injected content".
 */
export function extractArticle(html: string): Extracted {
  const src = String(html || "");
  const titleMatch = /<title\b[^>]*>([\s\S]*?)<\/title\s*>/i.exec(src);
  const ogTitle =
    /<meta\b[^>]*property\s*=\s*["']og:title["'][^>]*content\s*=\s*["']([^"']{1,300})["']/i.exec(
      src,
    );
  const h1 = /<h1\b[^>]*>([\s\S]*?)<\/h1\s*>/i.exec(src);

  const picked = pickRegion(stripChrome(src));
  const paragraphs = picked ? picked.blocks : [];

  // Drop boilerplate-length fragments and exact repeats (nav crumbs, share
  // links) while keeping headings.
  const seen: Record<string, true> = Object.create(null);
  const kept: string[] = [];
  let chars = 0;
  let truncated = false;
  for (let i = 0; i < paragraphs.length; i++) {
    const p = paragraphs[i];
    if (p.length < 24) continue;
    if (seen[p]) continue;
    seen[p] = true;
    if (chars + p.length > MAX_TEXT_CHARS) {
      truncated = true;
      break;
    }
    chars += p.length;
    kept.push(p);
  }

  const title = cleanText(
    (ogTitle && ogTitle[1]) || (titleMatch && titleMatch[1]) || (h1 && h1[1]) || "",
  ).slice(0, 300);

  return { title, paragraphs: kept, truncated };
}

/**
 * PURE. Is this extracted text data for programs rather than prose? A JSON
 * body (served as text/plain, or inside a "browsable API" HTML page like
 * Django REST framework's, which is what Launch Library sends a browser)
 * reads as many `"key":` pairs and few words. Prose with quoted speech has
 * almost none. Errs toward "it is prose": only a body that is plainly data
 * is refused.
 */
export function looksLikeData(paragraphs: string[]): boolean {
  const sample = paragraphs.join("\n").slice(0, 20_000);
  const keys = (sample.match(/"[A-Za-z_$][\w$.-]{0,60}"\s*:/g) || []).length;
  const words = (sample.match(/[A-Za-z]{3,}/g) || []).length || 1;
  if (/^\s*[[{]/.test(sample) && keys >= 2) return true;
  if (/\bContent-Type:\s*application\/(?:[\w.+-]*\+)?json\b/i.test(sample) && keys >= 2) return true;
  return keys >= 8 && keys / words > 0.15;
}

/** PURE. text/plain → paragraphs on blank lines. */
export function extractPlain(text: string): Extracted {
  const lines = String(text || "").split(/\n\s*\n/);
  const kept: string[] = [];
  let chars = 0;
  let truncated = false;
  for (let i = 0; i < lines.length; i++) {
    const p = lines[i].replace(/\s+/g, " ").trim();
    if (!p) continue;
    if (chars + p.length > MAX_TEXT_CHARS) {
      truncated = true;
      break;
    }
    chars += p.length;
    kept.push(p);
  }
  return { title: "", paragraphs: kept, truncated };
}
