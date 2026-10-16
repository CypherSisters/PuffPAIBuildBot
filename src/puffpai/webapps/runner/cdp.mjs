// Minimal DevTools-protocol client over --remote-debugging-pipe (Node 22.4+,
// no dependencies). Copyright (c) Cypher Sisters. Apache-2.0 License.
//
// The browser is started with --remote-debugging-pipe: it reads commands from
// its file descriptor 3 and writes replies and events to descriptor 4, each
// message a JSON text ended by a NUL byte. There is NO debugging port and no
// --remote-allow-origins, so nothing else on this computer (no web page, no
// other program) can reach the browser's controls.
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch({ browser, profile, flags = [], env }) {
  mkdirSync(profile, { recursive: true });
  const args = ["--remote-debugging-pipe", `--user-data-dir=${profile}`, ...flags, "about:blank"];
  const proc = spawn(browser, args, {
    stdio: ["ignore", "ignore", "ignore", "pipe", "pipe"],
    windowsHide: true,
    env,
  });
  const cdp = new CDP(proc.stdio[3], proc.stdio[4]);
  cdp.proc = proc;
  proc.on("exit", () => cdp.closed("the browser stopped"));
  const v = await Promise.race([
    cdp.send("Browser.getVersion"),
    sleep(20000).then(() => { throw new Error("the browser did not start"); }),
  ]);
  cdp.product = v.product;
  return cdp;
}

export class CDP {
  constructor(writable, readable) {
    this.id = 0;
    this.pending = new Map();
    this.handlers = [];
    this.out = writable;
    this.buf = Buffer.alloc(0);
    this.dead = null;
    readable.on("data", (chunk) => this.data(chunk));
    readable.on("error", () => this.closed("the browser pipe broke"));
    writable.on("error", () => this.closed("the browser pipe broke"));
  }
  data(chunk) {
    this.buf = Buffer.concat([this.buf, chunk]);
    let at;
    while ((at = this.buf.indexOf(0)) !== -1) {
      const text = this.buf.subarray(0, at).toString("utf8");
      this.buf = this.buf.subarray(at + 1);
      let msg;
      try { msg = JSON.parse(text); } catch { continue; }
      if (msg.id && this.pending.has(msg.id)) {
        const { ok, no } = this.pending.get(msg.id);
        this.pending.delete(msg.id);
        msg.error ? no(new Error(msg.error.message)) : ok(msg.result);
      } else if (msg.method) {
        for (const h of this.handlers) { try { h(msg); } catch {} }
      }
    }
  }
  closed(why) {
    if (this.dead) return;
    this.dead = why;
    for (const { no } of this.pending.values()) no(new Error(why));
    this.pending.clear();
  }
  send(method, params = {}, sessionId) {
    if (this.dead) return Promise.reject(new Error(this.dead));
    const id = ++this.id;
    const msg = JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) });
    return new Promise((ok, no) => {
      this.pending.set(id, { ok, no });
      this.out.write(msg + "\0");
    });
  }
  on(fn) { this.handlers.push(fn); }
  async close() {
    try { await Promise.race([this.send("Browser.close"), sleep(3000)]); } catch {}
    try { this.proc.kill(); } catch {}
  }
}
