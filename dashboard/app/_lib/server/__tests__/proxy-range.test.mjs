/**
 * Byte ranges through the /api/pai proxy: a thing's media file only.
 *
 *   npm run test:proxy-range
 *   node app/_lib/server/__tests__/proxy-range.test.mjs
 *
 * Requires Node >=22.6 for native TypeScript type stripping. Exits non-zero on
 * any failure.
 *
 * A video on a thing's page could not seek: the proxy dropped the browser's
 * Range header and the backend's 206 / Content-Range answer. Part 1 checks the
 * decision (proxyRange.ts); part 2 runs the decision and the route's header
 * pass-through against a real local server that answers ranges.
 *
 * Lives under app/_lib/ (underscore-private), so Next never routes it.
 *
 * puff puff pass
 */
import http from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const R = await import(pathToFileURL(join(here, "..", "proxyRange.ts")).href);
const route = readFileSync(join(here, "..", "..", "..", "api", "pai", "[...path]", "route.ts"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

let pass = 0;
let fail = 0;
function ok(name, cond) {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
}

const media = ["things", "skill-drawer", "media", "file"];

// 1. The decision
ok("the media file route is recognised", R.isThingMediaFile(media) === true);
ok("the media listing is not", R.isThingMediaFile(["things", "x", "media"]) === false);
ok("another route is not", R.isThingMediaFile(["chat", "stream"]) === false);
ok("an empty thing id is not", R.isThingMediaFile(["things", "", "media", "file"]) === false);
ok("a plain range is forwarded", R.rangeToForward("GET", media, "bytes=100-") === "bytes=100-");
ok("a closed range is forwarded", R.rangeToForward("GET", media, "bytes=0-1023") === "bytes=0-1023");
ok("a suffix range is forwarded", R.rangeToForward("get", media, "bytes=-500") === "bytes=-500");
ok("no range, nothing forwarded", R.rangeToForward("GET", media, null) === null);
ok("several ranges are not forwarded", R.rangeToForward("GET", media, "bytes=0-1,5-9") === null);
ok("junk is not forwarded", R.rangeToForward("GET", media, "bytes=0-1\r\nX-Evil: 1") === null);
ok("another unit is not forwarded", R.rangeToForward("GET", media, "items=0-1") === null);
ok("a POST gets no range", R.rangeToForward("POST", media, "bytes=0-1") === null);
ok("another route gets no range", R.rangeToForward("GET", ["chat", "send"], "bytes=0-1") === null);
ok("the answer headers include Content-Range",
  R.RANGE_RESPONSE_HEADERS.includes("content-range") && R.RANGE_RESPONSE_HEADERS.includes("accept-ranges"));

// The route uses the module (not a copy of the rule).
ok("the route forwards only rangeToForward's answer",
  /rangeToForward\(req\.method,\s*path,\s*req\.headers\.get\("range"\)\)/.test(route) &&
  /if \(range\) headers\["Range"\] = range;/.test(route));
ok("the route passes range answer headers only for the media file",
  /if \(req\.method === "GET" && isThingMediaFile\(path\)\) passed\.push\(\.\.\.RANGE_RESPONSE_HEADERS\)/.test(route));
ok("the upstream status is passed through", /status:\s*upstream\.status/.test(route));

// 2. Against a real server that answers ranges
const body = Buffer.from(Array.from({ length: 256 }, (_, i) => i));
const server = http.createServer((req, res) => {
  const m = /^bytes=(\d+)-(\d*)$/.exec(req.headers.range || "");
  if (!m) {
    res.writeHead(200, { "Content-Type": "video/mp4", "Accept-Ranges": "bytes", "Content-Length": body.length });
    res.end(body);
    return;
  }
  const start = Number(m[1]);
  const end = m[2] ? Number(m[2]) : body.length - 1;
  res.writeHead(206, {
    "Content-Type": "video/mp4",
    "Accept-Ranges": "bytes",
    "Content-Range": `bytes ${start}-${end}/${body.length}`,
    "Content-Length": end - start + 1,
  });
  res.end(body.subarray(start, end + 1));
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const base = `http://127.0.0.1:${server.address().port}`;

// The same steps the route takes, with its own module.
async function viaProxy(method, path, range) {
  const headers = {};
  const r = R.rangeToForward(method, path, range);
  if (r) headers["Range"] = r;
  const upstream = await fetch(`${base}/${path.join("/")}`, { method, headers });
  const passed = ["content-type", "cache-control", "content-disposition"];
  if (method === "GET" && R.isThingMediaFile(path)) passed.push(...R.RANGE_RESPONSE_HEADERS);
  const out = {};
  for (const h of passed) {
    const v = upstream.headers.get(h);
    if (v) out[h] = v;
  }
  return { status: upstream.status, headers: out, bytes: Buffer.from(await upstream.arrayBuffer()) };
}

try {
  const seek = await viaProxy("GET", media, "bytes=100-109");
  ok("a seek comes back as 206", seek.status === 206);
  ok("with its Content-Range", seek.headers["content-range"] === "bytes 100-109/256");
  ok("and Accept-Ranges", seek.headers["accept-ranges"] === "bytes");
  ok("and only those bytes", seek.bytes.equals(body.subarray(100, 110)));
  const whole = await viaProxy("GET", ["things", "x", "media", "file"], null);
  ok("no range is the whole file, 200", whole.status === 200 && whole.bytes.length === 256);
  const other = await viaProxy("GET", ["knowledge", "file"], "bytes=100-109");
  ok("another route never sends a range", other.status === 200 && other.bytes.length === 256);
  ok("another route never gets range headers", !("content-range" in other.headers) && !("accept-ranges" in other.headers));
} finally {
  server.closeAllConnections();
  await new Promise((resolve) => server.close(resolve));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exitCode = fail ? 1 : 0;
