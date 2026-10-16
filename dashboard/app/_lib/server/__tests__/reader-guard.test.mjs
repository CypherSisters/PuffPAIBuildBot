/**
 * SSRF guard + reader transport assertions.
 *
 *   npm run test:reader   (or: node app/_lib/server/__tests__/reader-guard.test.mjs)
 *
 * Same house style as pairing-gate.test.mjs: plain Node, native TypeScript
 * type stripping (needs Node >=22.6), non-zero exit on any failure.
 *
 * Two halves:
 *  1. PURE — no network at all. Scheme/userinfo/port/hostname rejection and
 *     every private address family in IPv4 and IPv6, including the v4-embedding
 *     IPv6 forms. Plus the extractor, asserted to emit no markup.
 *  2. LIVE — a fixture HTTP server on 127.0.0.1 exercises the transport
 *     (redirects, content-type, size cap, timeout). To reach it the test hands
 *     fetchArticle a validator that permits ONLY that one loopback origin and
 *     DELEGATES EVERYTHING ELSE TO THE REAL validateTarget. So the
 *     redirect-to-private test is genuine: the redirect target is judged by the
 *     production guard, not by a stub.
 *
 * Runtime is ~12 s, dominated by the deliberate 10 s timeout assertion.
 *
 * Lives under app/_lib/ (underscore-private), so Next never routes it.
 */
import assert from "node:assert/strict";
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const {
  checkUrlSyntax,
  ipReason,
  validateTarget,
  resolveRedirect,
  ALLOWED_PORTS,
  REASON,
} = await import("../urlGuard.ts");
const { fetchArticle, extractArticle, extractPlain, decodeEntities, looksLikeData, MAX_BYTES } =
  await import("../readerFetch.ts");

let pass = 0;
let fail = 0;
const failures = [];

function ok(cond, label, extra = "") {
  if (cond) {
    pass++;
    console.log(`PASS  ${label}${extra ? "   " + extra : ""}`);
  } else {
    fail++;
    failures.push(label);
    console.log(`FAIL  ${label}${extra ? "   " + extra : ""}`);
  }
}

function eq(got, want, label) {
  ok(got === want, label, `got=${JSON.stringify(got)} want=${JSON.stringify(want)}`);
}

/* ═══════════════════════════════════════════════════════════════════════
   1. PURE — URL syntax
   ═══════════════════════════════════════════════════════════════════════ */

console.log("\n--- scheme allowlist: http/https ONLY ---");
for (const [u, why] of [
  ["file:///etc/passwd", "file:"],
  ["ftp://example.com/x", "ftp:"],
  ["gopher://example.com/", "gopher:"],
  ["data:text/html,<b>x</b>", "data:"],
  ["javascript:alert(1)", "javascript:"],
  ["blob:http://example.com/abc", "blob:"],
  ["ws://example.com/", "ws:"],
  ["jar:http://example.com/!/", "jar:"],
]) {
  const r = checkUrlSyntax(u);
  ok(!r.ok && r.reason === REASON.SCHEME, `scheme rejected: ${why}`, u);
}
ok(checkUrlSyntax("https://example.com/a").ok, "https accepted");
ok(checkUrlSyntax("http://example.com/a").ok, "http accepted");

console.log("\n--- userinfo rejected ---");
for (const u of [
  "http://user:pass@example.com/",
  "http://user@example.com/",
  "https://:pass@example.com/",
  // The classic disguise: looks like it goes to example.com, goes to evil.tld.
  "http://example.com@evil.tld/",
]) {
  const r = checkUrlSyntax(u);
  ok(!r.ok && r.reason === REASON.USERINFO, "userinfo rejected", u);
}

console.log("\n--- port allowlist ---");
eq(ALLOWED_PORTS.join(","), "80,443", "ALLOWED_PORTS is 80/443 only");
for (const u of [
  "http://example.com:8000/health", // the PuffPAI backend's port
  "http://example.com:8123/", // Home Assistant's port
  "http://example.com:3003/dashboard", // this dashboard's port
  "http://example.com:22/",
  "http://example.com:1880/",
  "http://example.com:8080/",
  "https://example.com:8443/",
  "http://example.com:0/",
]) {
  const r = checkUrlSyntax(u);
  ok(!r.ok && r.reason === REASON.PORT, "port rejected", u);
}
ok(checkUrlSyntax("http://example.com:80/").ok, "explicit :80 accepted");
ok(checkUrlSyntax("https://example.com:443/").ok, "explicit :443 accepted");

console.log("\n--- hostname shapes that never resolve to public space ---");
for (const u of [
  "http://localhost/",
  "http://LOCALHOST/",
  "http://foo.localhost/",
  "http://orin/", // bare LAN short name
  "http://puffpai.local/",
  "http://nas.lan/",
  "http://printer.home/",
  "http://db.internal/",
]) {
  const r = checkUrlSyntax(u);
  ok(!r.ok && r.reason === REASON.HOSTNAME, "LAN-only hostname rejected", u);
}

console.log("\n--- oversize / empty input ---");
ok(!checkUrlSyntax("").ok, "empty string rejected");
ok(!checkUrlSyntax(null).ok, "null rejected");
ok(!checkUrlSyntax(undefined).ok, "undefined rejected");
ok(!checkUrlSyntax({ url: "http://example.com" }).ok, "object rejected");
ok(!checkUrlSyntax("http://example.com/" + "a".repeat(3000)).ok, "3 KB url rejected");
ok(!checkUrlSyntax("not a url").ok, "garbage rejected");
ok(!checkUrlSyntax("//example.com/x").ok, "protocol-relative rejected");

/* ═══════════════════════════════════════════════════════════════════════
   2. PURE — IP range classification
   ═══════════════════════════════════════════════════════════════════════ */

console.log("\n--- IPv4: every non-public family blocked ---");
const V4_BLOCKED = [
  ["0.0.0.0", "unspecified"],
  ["0.1.2.3", "this-network 0/8"],
  ["10.0.0.1", "private 10/8"],
  ["192.168.50.10", "THE APPLIANCE ITSELF"],
  ["192.168.50.20", "HOME ASSISTANT"],
  ["10.255.255.255", "private 10/8 top"],
  ["100.64.0.1", "CGNAT low"],
  ["100.127.255.255", "CGNAT high"],
  ["127.0.0.1", "loopback"],
  ["127.1.2.3", "loopback 127/8"],
  ["169.254.169.254", "link-local (cloud metadata)"],
  ["172.16.0.1", "private 172.16/12 low"],
  ["172.31.255.255", "private 172.16/12 high"],
  ["192.0.0.1", "IETF protocol 192.0.0/24"],
  ["192.0.2.1", "TEST-NET-1"],
  ["192.88.99.1", "6to4 relay"],
  ["192.168.1.1", "private 192.168/16"],
  ["198.18.0.1", "benchmark 198.18/15"],
  ["198.19.255.255", "benchmark 198.18/15 top"],
  ["198.51.100.1", "TEST-NET-2"],
  ["203.0.113.1", "TEST-NET-3"],
  ["224.0.0.1", "multicast"],
  ["239.255.255.250", "SSDP multicast"],
  ["240.0.0.1", "reserved 240/4"],
  ["255.255.255.255", "broadcast"],
];
for (const [ip, why] of V4_BLOCKED) {
  ok(ipReason(ip) !== null, `v4 blocked: ${why}`, ip);
}

console.log("\n--- IPv4: genuinely public addresses allowed ---");
for (const ip of [
  "1.1.1.1",
  "8.8.8.8",
  "93.184.216.34",
  "172.15.255.255", // just below 172.16/12
  "172.32.0.1", // just above 172.16/12
  "100.63.255.255", // just below CGNAT
  "100.128.0.1", // just above CGNAT
  "11.0.0.1", // just above 10/8
  "9.255.255.255", // just below 10/8
  "126.255.255.255", // just below 127/8
  "128.0.0.1", // just above 127/8
  "169.253.255.255", // just below link-local
  "169.255.0.1", // just above link-local
  "192.0.1.1", // between 192.0.0/24 and 192.0.2/24
  "223.255.255.255", // just below multicast
  "198.17.255.255", // just below benchmark
  "198.20.0.1", // just above benchmark
]) {
  ok(ipReason(ip) === null, "v4 allowed (boundary)", ip);
}

console.log("\n--- IPv6: every non-public family blocked ---");
const V6_BLOCKED = [
  ["::", "unspecified"],
  ["::1", "loopback"],
  ["0:0:0:0:0:0:0:1", "loopback, uncompressed"],
  ["fc00::1", "ULA fc00::/7 low"],
  ["fd00::1", "ULA fd00::/8 (the common one)"],
  ["fdff:ffff::1", "ULA top"],
  ["fe80::1", "link-local"],
  ["fe80::1%eth0", "link-local with zone id"],
  ["febf::1", "link-local top of /10"],
  ["ff02::1", "multicast all-nodes"],
  ["ff00::", "multicast base"],
  ["100::1", "discard-only 100::/64"],
  ["2001:db8::1", "documentation"],
  ["2001::1", "Teredo"],
  ["2002::1", "6to4"],
  ["2002:0a00:0001::1", "6to4 wrapping 10.0.0.1"],
];
for (const [ip, why] of V6_BLOCKED) {
  ok(ipReason(ip) !== null, `v6 blocked: ${why}`, ip);
}

console.log("\n--- IPv6: IPv4-mapped and IPv4-embedding forms unwrapped ---");
const V6_MAPPED_BLOCKED = [
  ["::ffff:127.0.0.1", "mapped loopback"],
  ["::ffff:7f00:1", "mapped loopback, hex spelling"],
  ["::ffff:192.168.50.10", "mapped THE APPLIANCE"],
  ["::ffff:10.0.0.1", "mapped 10/8"],
  ["::ffff:192.168.1.1", "mapped 192.168/16"],
  ["::ffff:169.254.169.254", "mapped link-local"],
  ["::ffff:172.16.0.1", "mapped 172.16/12"],
  ["::127.0.0.1", "IPv4-compatible loopback"],
  ["64:ff9b::127.0.0.1", "NAT64 loopback"],
  ["64:ff9b::10.0.0.1", "NAT64 10/8"],
];
for (const [ip, why] of V6_MAPPED_BLOCKED) {
  ok(ipReason(ip) !== null, `v6 mapped blocked: ${why}`, ip);
}
ok(ipReason("::ffff:8.8.8.8") === null, "mapped PUBLIC v4 allowed", "::ffff:8.8.8.8");
ok(ipReason("64:ff9b::8.8.8.8") === null, "NAT64 PUBLIC v4 allowed");
ok(ipReason("2606:4700:4700::1111") === null, "public v6 allowed (cloudflare)");
ok(ipReason("2a00:1450:4001::200e") === null, "public v6 allowed (google)");

console.log("\n--- non-IP input fails closed ---");
for (const v of ["", "example.com", "999.999.999.999", "0x7f000001", "::gg", null, undefined, "1.2.3", "1.2.3.4.5"]) {
  ok(ipReason(v) !== null, "non-IP / malformed fails closed", JSON.stringify(v));
}

/* ═══════════════════════════════════════════════════════════════════════
   3. validateTarget on IP-literal URLs — no DNS needed
   ═══════════════════════════════════════════════════════════════════════ */

console.log("\n--- validateTarget refuses the appliance's own neighbourhood ---");
for (const [u, why] of [
  ["http://127.0.0.1/", "loopback"],
  ["http://192.168.50.10/", "the dashboard host"],
  ["http://192.168.50.20/", "Home Assistant host"],
  ["http://192.168.1.1/", "a home router"],
  ["http://169.254.169.254/latest/meta-data/", "cloud metadata"],
  ["http://[::1]/", "loopback v6 literal"],
  ["http://[::ffff:127.0.0.1]/", "mapped loopback literal"],
  ["http://[fd00::1]/", "ULA literal"],
  // WHATWG normalises these into dotted-quad before we ever see them.
  ["http://2130706433/", "decimal loopback"],
  ["http://0x7f000001/", "hex loopback"],
  ["http://0177.0.0.1/", "octal loopback"],
  ["http://127.1/", "short-form loopback"],
  ["http://0/", "bare zero"],
]) {
  const r = await validateTarget(u);
  ok(
    !r.ok && (r.reason === REASON.PRIVATE_IP || r.reason === REASON.PORT || r.reason === REASON.HOSTNAME),
    `validateTarget refuses ${why}`,
    `${u} -> ${r.ok ? "ALLOWED" : r.reason + (r.detail ? " / " + r.detail : "")}`,
  );
}

console.log("\n--- resolveRedirect ---");
const base = new URL("https://news.example.com/story/1");
eq(resolveRedirect(base, "/other"), "https://news.example.com/other", "relative redirect resolved");
eq(resolveRedirect(base, "http://10.0.0.5/"), "http://10.0.0.5/", "absolute redirect preserved");
eq(resolveRedirect(base, ""), null, "empty Location rejected");
eq(resolveRedirect(base, "   "), null, "blank Location rejected");

/* ═══════════════════════════════════════════════════════════════════════
   4. PURE — extraction never emits markup
   ═══════════════════════════════════════════════════════════════════════ */

console.log("\n--- extractArticle: chrome stripped, no markup escapes ---");
const HOSTILE = `<!doctype html><html><head>
<title>Hostile &amp; Fixture</title>
<style>body{background:url(http://192.168.50.10:8000/leak)}</style>
<script>fetch('http://127.0.0.1:8000/health').then(r=>r.text())</script>
</head><body>
<nav><a href="/x">Navigation crumb that is quite long indeed</a></nav>
<header>Site header text that is also long enough to survive</header>
<main><article>
<h1>The Real Headline Goes Here</h1>
<p>First paragraph with an &amp; entity, an &eacute; accent and a &#8212; dash.</p>
<p><img src="http://evil.tld/track.gif">Second paragraph after a tracking pixel.</p>
<p onclick="alert(1)">Third paragraph on an element with a handler attribute.</p>
<p>&lt;script&gt;alert(2)&lt;/script&gt; encoded script text inside a paragraph.</p>
<svg><text>svg text that should never appear anywhere</text></svg>
<noscript>Noscript prose is KEPT because this reader runs no JavaScript.</noscript>
</article></main>
<footer>Footer text that is long enough to survive the length filter</footer>
</body></html>`;

const ex = extractArticle(HOSTILE);
const joined = ex.paragraphs.join("\n");
eq(ex.title, "Hostile & Fixture", "title extracted and entity-decoded");
ok(ex.paragraphs.length >= 4, "paragraphs extracted", `n=${ex.paragraphs.length}`);
// A page whose PROSE literally reads "<script>alert(2)</script>" (written with
// entities) must keep that text — it is what the article says. That is safe
// because the overlay renders paragraphs as React text nodes and never calls
// dangerouslySetInnerHTML, so a '<' in a string is a character, not a tag.
// What must NEVER survive is a '<' that came from the page's own MARKUP.
ok(
  joined.indexOf("<script>alert(2)</script>") >= 0,
  "entity-encoded angle brackets survive as literal prose",
);
const fromMarkup = joined.split("<script>alert(2)</script>").join("");
ok(fromMarkup.indexOf("<") < 0, "NO markup-derived '<' in returned text");
ok(fromMarkup.indexOf(">") < 0, "NO markup-derived '>' in returned text");
ok(joined.indexOf("fetch(") < 0, "inline <script> body dropped");
ok(joined.indexOf("192.168.50.10") < 0, "inline <style> body dropped");
ok(joined.indexOf("evil.tld") < 0, "img src never surfaces");
ok(joined.indexOf("alert(1)") < 0, "onclick attribute never surfaces");
ok(joined.indexOf("svg text") < 0, "<svg> content dropped");
// KEPT on purpose: a no-JS reader is the audience <noscript> is written for.
// neverssl.com puts its entire page there.
ok(joined.indexOf("Noscript prose is KEPT") >= 0, "<noscript> prose KEPT (no-JS client)");
ok(joined.indexOf("Navigation crumb") < 0, "<nav> dropped");
ok(joined.indexOf("Site header") < 0, "<header> dropped");
ok(joined.indexOf("Footer text") < 0, "<footer> dropped");
ok(joined.indexOf("é") >= 0, "named entity decoded");
ok(joined.indexOf("—") >= 0, "numeric entity decoded");
ok(
  ex.paragraphs.every((p) => typeof p === "string"),
  "every paragraph is a plain string",
);

console.log("\n--- decodeEntities edge cases ---");
eq(decodeEntities("&amp;amp;"), "&amp;", "single-pass decode (no double-decoding)");
eq(decodeEntities("&#60;script&#62;"), "<script>", "numeric decode is literal text, not markup");
eq(decodeEntities("&#xD800;"), "", "lone surrogate dropped");
eq(decodeEntities("&#0;"), "", "NUL dropped");
eq(decodeEntities("&notarealentity;"), "&notarealentity;", "unknown entity left alone");

console.log("\n--- extractPlain / empty inputs ---");
eq(extractArticle("").paragraphs.length, 0, "empty html -> no paragraphs");
eq(extractArticle("<html><body><p>tiny</p></body></html>").paragraphs.length, 0, "sub-24-char blocks filtered");
ok(extractPlain("A paragraph long enough to survive.\n\nAnother one here.").paragraphs.length === 2, "plain text split on blank lines");

console.log("\n--- data for programs is never shown as a page ---");
// Launch Library 2 sends a browser Django REST framework's "browsable API"
// page: text/html around the raw JSON (seen in the "Read" popup).
const DRF = `<html><head><title>Launch Detail - Launch Library 2</title></head><body>
<div class="page-header"><h1>Launch Detail</h1></div>
<div class="response-info"><pre class="prettyprint"><span class="meta nocode"><b>HTTP 200 OK</b>
<b>Allow:</b> GET, HEAD, OPTIONS
<b>Content-Type:</b> application/json
<b>Vary:</b> Accept</span>
{
    "id": "e3df2ecd-c239-472f-95e4-2b89b4f75800",
    "url": "https://ll.thespacedevs.com/2.3.0/launches/e3df2ecd/",
    "name": "Falcon 9 Block 5 | Starlink Group 10-12",
    "status": {"id": 1, "name": "Go for Launch", "abbrev": "Go"},
    "net": "2026-10-10T04:00:00Z", "window_start": "2026-10-10T04:00:00Z",
    "probability": 90, "pad": {"id": 80, "name": "Space Launch Complex 40"}
}</pre></div></body></html>`;
ok(looksLikeData(extractArticle(DRF).paragraphs), "a browsable-API page around JSON is data");
ok(looksLikeData(extractPlain('{"count": 7000, "next": "https://x.example/?offset=10", "results": []}').paragraphs),
  "a JSON body served as text/plain is data");
ok(!looksLikeData(extractArticle(`<html><body><article><p>"Go for launch," the director said: the
crew had waited three days for the weather to clear over the Cape, and nobody wanted another delay.</p>
<p>The rocket lifted off at dawn and the boosters came back to land on the pad a few minutes later.</p>
</article></body></html>`).paragraphs), "prose with quoted speech is a page");

/* ═══════════════════════════════════════════════════════════════════════
   5. LIVE — transport against a loopback fixture
   ═══════════════════════════════════════════════════════════════════════ */

const ARTICLE_HTML = `<!doctype html><html><head><title>Fixture Story</title></head>
<body><article>
<p>This is the first paragraph of the fixture story and it is long enough.</p>
<p>This is the second paragraph of the fixture story, also long enough.</p>
</article></body></html>`;

const server = http.createServer((req, res) => {
  const u = req.url || "/";
  if (u === "/ok") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    res.end(ARTICLE_HTML);
  } else if (u === "/plain") {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("A plain text paragraph that is long enough.\n\nAnd a second one here.");
  } else if (u === "/redir-ok") {
    res.writeHead(302, { Location: "/ok" });
    res.end();
  } else if (u === "/redir-private") {
    res.writeHead(302, { Location: "http://192.168.50.20:8123/" });
    res.end();
  } else if (u === "/redir-loopback") {
    res.writeHead(302, { Location: "http://127.0.0.1:8000/health" });
    res.end();
  } else if (u === "/redir-private-80") {
    // Port 80, so the PORT check cannot short-circuit it — this proves the
    // IP-range check itself is applied on redirect hops.
    res.writeHead(302, { Location: "http://192.168.50.20/" });
    res.end();
  } else if (u === "/redir-loopback-80") {
    res.writeHead(302, { Location: "http://127.0.0.1/" });
    res.end();
  } else if (u === "/redir-v6-loopback") {
    res.writeHead(302, { Location: "http://[::1]/" });
    res.end();
  } else if (u === "/redir-userinfo") {
    res.writeHead(302, { Location: "http://a:b@example.com/" });
    res.end();
  } else if (u === "/redir-loop") {
    res.writeHead(302, { Location: "/redir-loop" });
    res.end();
  } else if (u === "/redir-scheme") {
    res.writeHead(302, { Location: "file:///etc/passwd" });
    res.end();
  } else if (u === "/json") {
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end('{"a":1}');
  } else if (u === "/pdf") {
    res.writeHead(200, { "Content-Type": "application/pdf" });
    res.end("%PDF-1.4");
  } else if (u === "/huge-declared") {
    res.writeHead(200, {
      "Content-Type": "text/html",
      "Content-Length": String(MAX_BYTES * 3),
    });
    res.end("x".repeat(1024));
  } else if (u === "/huge-chunked") {
    res.writeHead(200, { "Content-Type": "text/html" });
    const chunk = "<p>" + "y".repeat(65_000) + "</p>";
    let sent = 0;
    const pump = () => {
      while (sent < MAX_BYTES * 2) {
        sent += chunk.length;
        if (!res.write(chunk)) {
          res.once("drain", pump);
          return;
        }
      }
      res.end();
    };
    pump();
    res.on("error", () => {});
  } else if (u === "/hang") {
    res.writeHead(200, { "Content-Type": "text/html" });
    // Never finishes — exercises the total timeout.
  } else if (u === "/404") {
    res.writeHead(404, { "Content-Type": "text/html" });
    res.end("<html><body>nope</body></html>");
  } else {
    res.writeHead(404);
    res.end();
  }
});
server.on("clientError", () => {});

await new Promise((r) => server.listen(0, "127.0.0.1", r));
const PORT = server.address().port;
const FIX = `http://127.0.0.1:${PORT}`;

/**
 * TEST-ONLY validator. Permits exactly one loopback origin — the fixture — and
 * hands EVERY other URL to the real validateTarget. So a redirect off the
 * fixture is judged by production code, not by this stub.
 *
 * Production never passes a validator: app/api/reader/route.ts calls
 * fetchArticle(url) with a single argument (asserted below).
 */
const fixtureOnly = async (raw) => {
  let u = null;
  try {
    u = new URL(String(raw));
  } catch {
    u = null;
  }
  if (u && u.protocol === "http:" && u.hostname === "127.0.0.1" && u.port === String(PORT)) {
    return { ok: true, url: u, hostname: u.hostname, port: PORT, ip: "127.0.0.1", family: 4 };
  }
  return validateTarget(raw);
};

console.log("\n--- live transport: happy paths ---");
{
  const r = await fetchArticle(`${FIX}/ok`, fixtureOnly);
  ok(r.ok, "fixture article fetched", r.ok ? "" : r.reason);
  if (r.ok) {
    eq(r.title, "Fixture Story", "title from fixture");
    eq(r.host, "127.0.0.1", "host reported");
    eq(r.paragraphs.length, 2, "two paragraphs");
    ok(r.paragraphs[0].indexOf("first paragraph") > 0, "first paragraph text");
  }
}
{
  const r = await fetchArticle(`${FIX}/plain`, fixtureOnly);
  ok(r.ok && r.paragraphs.length === 2, "text/plain accepted");
}
{
  const r = await fetchArticle(`${FIX}/redir-ok`, fixtureOnly);
  ok(r.ok && r.title === "Fixture Story", "in-fixture redirect followed");
}

console.log("\n--- live transport: REDIRECT TO PRIVATE MUST BE REFUSED ---");
{
  const r = await fetchArticle(`${FIX}/redir-private`, fixtureOnly);
  ok(
    !r.ok && (r.reason === REASON.PRIVATE_IP || r.reason === REASON.PORT),
    "302 -> Home Assistant 192.168.50.20:8123 REFUSED",
    r.ok ? "ALLOWED!!" : `${r.reason} / ${r.detail || ""}`,
  );
}
{
  const r = await fetchArticle(`${FIX}/redir-loopback`, fixtureOnly);
  ok(
    !r.ok && (r.reason === REASON.PRIVATE_IP || r.reason === REASON.PORT),
    "302 -> 127.0.0.1:8000 (the backend) REFUSED",
    r.ok ? "ALLOWED!!" : `${r.reason} / ${r.detail || ""}`,
  );
}
// Port 80 versions: the port allowlist cannot mask the IP-range check here.
for (const [route, why, want] of [
  ["/redir-private-80", "302 -> 192.168.50.20:80 refused BY IP RANGE", REASON.PRIVATE_IP],
  ["/redir-loopback-80", "302 -> 127.0.0.1:80 refused BY IP RANGE", REASON.PRIVATE_IP],
  ["/redir-v6-loopback", "302 -> [::1]:80 refused BY IP RANGE", REASON.PRIVATE_IP],
  ["/redir-userinfo", "302 -> userinfo URL refused", REASON.USERINFO],
]) {
  const r = await fetchArticle(`${FIX}${route}`, fixtureOnly);
  ok(!r.ok && r.reason === want, why, r.ok ? "ALLOWED!!" : `${r.reason} / ${r.detail || ""}`);
}
{
  const r = await fetchArticle(`${FIX}/redir-scheme`, fixtureOnly);
  ok(!r.ok && r.reason === REASON.SCHEME, "302 -> file:// REFUSED", r.ok ? "ALLOWED!!" : r.reason);
}
{
  const r = await fetchArticle(`${FIX}/redir-loop`, fixtureOnly);
  ok(!r.ok && r.reason === "too_many_redirects", "redirect loop capped", r.ok ? "ALLOWED!!" : r.reason);
}

console.log("\n--- live transport: content-type allowlist ---");
{
  const r = await fetchArticle(`${FIX}/json`, fixtureOnly);
  ok(!r.ok && r.reason === "unsupported_content_type", "application/json refused", r.ok ? "!!" : r.detail);
}
{
  const r = await fetchArticle(`${FIX}/pdf`, fixtureOnly);
  ok(!r.ok && r.reason === "unsupported_content_type", "application/pdf refused", r.ok ? "!!" : r.detail);
}

console.log("\n--- live transport: size cap ---");
{
  const r = await fetchArticle(`${FIX}/huge-declared`, fixtureOnly);
  ok(!r.ok && r.reason === "response_too_large", "declared Content-Length > 2 MB refused", r.ok ? "!!" : r.reason);
}
{
  const r = await fetchArticle(`${FIX}/huge-chunked`, fixtureOnly);
  ok(!r.ok && r.reason === "response_too_large", "chunked body past 2 MB aborted", r.ok ? "!!" : r.reason);
}

console.log("\n--- live transport: HTTP status ---");
{
  const r = await fetchArticle(`${FIX}/404`, fixtureOnly);
  ok(!r.ok && r.reason === "http_error" && r.detail === "404", "404 surfaced honestly", r.ok ? "!!" : r.detail);
}

console.log("\n--- live transport: total timeout (this one takes ~10 s) ---");
{
  const t0 = Date.now();
  const r = await fetchArticle(`${FIX}/hang`, fixtureOnly);
  const dt = Date.now() - t0;
  ok(!r.ok && r.reason === "timeout", "hung response times out", `${dt} ms`);
  ok(dt < 14_000, "timeout fires within budget", `${dt} ms`);
}

console.log("\n--- default validator (NO stub) refuses the fixture itself ---");
{
  // The single most important assertion in this file: with production wiring,
  // loopback is unreachable.
  const r = await fetchArticle(`${FIX}/ok`);
  ok(
    !r.ok && (r.reason === REASON.PRIVATE_IP || r.reason === REASON.PORT || r.reason === REASON.HOSTNAME),
    "fetchArticle() with the REAL guard cannot reach 127.0.0.1",
    r.ok ? "ALLOWED!!" : r.reason,
  );
}

await new Promise((r) => server.close(r));

/* ═══════════════════════════════════════════════════════════════════════
   6. Wiring assertion — production must not pass a validator
   ═══════════════════════════════════════════════════════════════════════ */

console.log("\n--- route wiring ---");
{
  const here = path.dirname(fileURLToPath(import.meta.url));
  const routePath = path.resolve(here, "../../../api/reader/route.ts");
  const src = fs.readFileSync(routePath, "utf8");
  const calls = src.match(/fetchArticle\s*\(([^)]*)\)/g) || [];
  ok(calls.length === 1, "route calls fetchArticle exactly once", calls.join(" | "));
  ok(
    calls.length === 1 && calls[0].indexOf(",") < 0,
    "route calls fetchArticle with ONE argument (real guard in force)",
    calls[0] || "",
  );
  ok(src.indexOf("sameOriginStrict(req)") > 0, "route enforces strict same-origin");
  ok(src.indexOf('runtime = "nodejs"') > 0, "route pins the nodejs runtime");
  ok(src.indexOf('dynamic = "force-dynamic"') > 0, "route is force-dynamic");
  ok(src.indexOf('"Cache-Control": "no-store"') > 0, "route sends no-store");
}

/* ── result ────────────────────────────────────────────────────────── */

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) console.log("failed:\n  " + failures.join("\n  "));
assert.equal(fail, 0, `${fail} reader-guard assertions failed`);
