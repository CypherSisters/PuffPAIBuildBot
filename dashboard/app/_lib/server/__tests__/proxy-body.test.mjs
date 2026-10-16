/**
 * The /api/pai proxy passes a backend 401 through as 401, not 502.
 *
 *   node app/_lib/server/__tests__/proxy-body.test.mjs
 *
 * Plain Node, no test runner, no build. Loads the real helper
 * (app/_lib/server/proxyBody.ts, type stripping), reads the route source, and
 * does a real round trip against a stub server on a loopback port.
 *
 * REGRESSION UNDER TEST: the proxy forwarded every
 * body as a stream. Node's fetch, following the Fetch standard, answers a 401
 * to a streamed-body request with a network error ("expected non-null body
 * source"), so a wrong vault PIN or a wrong recovery code reached the browser
 * as 502 "backend unreachable".
 */
import http from "node:http";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const B = await import(pathToFileURL(join(here, "..", "proxyBody.ts")).href);
const route = readFileSync(join(here, "..", "..", "..", "api", "pai", "[...path]", "route.ts"), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

let pass = 0;
let fail = 0;
function ok(name, cond) {
  if (cond) pass++;
  else fail++;
  console.log(`${cond ? "PASS" : "FAIL"}  ${name}`);
}

// The decision
ok("GET is never buffered", B.shouldBufferBody("GET", "10") === false);
ok("HEAD is never buffered", B.shouldBufferBody("HEAD", "10") === false);
ok("a small declared POST body is buffered", B.shouldBufferBody("POST", "57") === true);
ok("zero length is buffered", B.shouldBufferBody("POST", "0") === true);
ok("exactly the cap is buffered", B.shouldBufferBody("PUT", String(B.BUFFER_BODY_MAX_BYTES)) === true);
ok("over the cap streams", B.shouldBufferBody("POST", String(B.BUFFER_BODY_MAX_BYTES + 1)) === false);
ok("no declared length streams", B.shouldBufferBody("POST", null) === false);
ok("an empty length streams", B.shouldBufferBody("POST", "") === false);
ok("a junk length streams", B.shouldBufferBody("POST", "12abc") === false);
ok("a negative length streams", B.shouldBufferBody("POST", "-1") === false);
ok("lower-case method still counts", B.shouldBufferBody("post", "5") === true);

// The route uses it, and still streams the rest
ok("route decides with shouldBufferBody", /shouldBufferBody\(req\.method, declared\)/.test(route));
ok("route reads a buffered body whole", /await req\.arrayBuffer\(\)/.test(route));
ok("route still streams large bodies", /: req\.body/.test(route) && /duplex: hasBody && !buffered \? "half"/.test(route));
ok("the 50 MB ceiling is still checked first",
  route.indexOf("len > MAX_BODY_BYTES") > -1
  && route.indexOf("len > MAX_BODY_BYTES") < route.indexOf("shouldBufferBody(req.method"));

// A real 401 round trip on loopback
const server = http.createServer((req, res) => {
  req.resume();
  req.on("end", () => {
    res.writeHead(401, { "content-type": "application/json" });
    res.end(JSON.stringify({ message: "That recovery code does not match this vault." }));
  });
});
await new Promise((r) => server.listen(0, "127.0.0.1", r));
const url = `http://127.0.0.1:${server.address().port}/vault/recover`;
const text = JSON.stringify({ recovery_code: "x", new_pin: "y" });

let streamed = "no error";
try {
  await fetch(url, { method: "POST", body: new Response(text).body, duplex: "half" });
} catch (e) {
  streamed = String(e?.cause?.message || e?.message);
}
ok("(why) a streamed body turns the 401 into a fetch error", streamed !== "no error");

const buffered = await fetch(url, { method: "POST", body: new TextEncoder().encode(text).buffer });
const payload = await buffered.json();
ok("a buffered body gets the real 401", buffered.status === 401);
ok("and the backend's own words", /does not match/.test(payload.message));
server.close();

console.log(`\n${pass} passed, ${fail} failed`);
if (fail) process.exit(1);
