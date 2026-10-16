/**
 * Looks in the browser: only allowlisted custom properties, only well-shaped
 * values, setProperty only, and the inline boot script follows the same rules
 * (including the ?puffLook=default escape hatch).
 *
 *   node app/_lib/__tests__/looks-apply.test.mjs
 *
 * Plain Node (>=22.6, TypeScript type stripping), no DOM library, no network.
 */
import assert from "node:assert/strict";

const L = await import("../looks.ts");

let pass = 0;
function ok(label, cond) {
  assert.ok(cond, label);
  pass++;
  console.log(`PASS  ${label}`);
}

function fakeStyle() {
  const props = new Map();
  const calls = [];
  return {
    props,
    calls,
    setProperty(n, v) { calls.push(["set", n, v]); props.set(n, v); },
    removeProperty(n) { calls.push(["remove", n]); const v = props.get(n) || ""; props.delete(n); return v; },
    getPropertyValue(n) { return props.get(n) || ""; },
  };
}

// -- cleanVars / setVars ------------------------------------------------------
const hostile = {
  "--bg": "#0b1026",
  "--text": "url(https://evil.test/x.png)",
  "--card": "rgba(20,26,58,.62)",
  "--line": "#FFFFFF",
  "--evil": "#ffffff",
  "background-image": "url(x)",
  "--shadow": "0 18px 50px rgba(0,0,0,.45)",
  "--puff-font-heading": "var(--font-jbmono)",
  "--puff-font-body": "Comic Sans MS",
  "--imgPage": "0",
  "--puff-radius-card": "14px",
  "--pink": "#ff00ff; background:url(x)",
  "--sky": "expression(alert(1))",
};
const clean = L.cleanVars(hostile);
ok("keeps allowed, well-shaped values",
   clean["--bg"] === "#0b1026" && clean["--card"] === "rgba(20,26,58,.62)" &&
   clean["--shadow"] && clean["--puff-font-heading"] && clean["--imgPage"] === "0" &&
   clean["--puff-radius-card"] === "14px");
ok("drops unknown names", !("--evil" in clean) && !("background-image" in clean));
ok("drops bad values", !("--text" in clean) && !("--line" in clean) && !("--pink" in clean) &&
   !("--sky" in clean) && !("--puff-font-body" in clean));

const style = fakeStyle();
style.setProperty("--text", "#123456");
L.setVars(style, hostile);
ok("setVars only ever sets allowlisted names",
   style.calls.filter((c) => c[0] === "set").every((c) => c[1] in L.LOOK_VAR_KINDS));
ok("setVars removes an allowed name the look does not set", !style.props.has("--text"));
const before = style.calls.length;
L.setVars(style, hostile);
ok("setVars is quiet when nothing changed (an observer cannot loop)", style.calls.length === before);
L.setVars(style, null);
ok("setVars(null) clears the look", style.props.size === 0);

// -- the boot script ------------------------------------------------------------
function runBoot({ search = "", cache = null, theme = "day", off = null }) {
  const style = fakeStyle();
  const attrs = new Map([["data-puff-theme", theme]]);
  const local = new Map(cache ? [[L.LOOK_CACHE_KEY, JSON.stringify(cache)]] : []);
  const session = new Map(off ? [[L.LOOK_OFF_KEY, off]] : []);
  const store = (m) => ({
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  });
  const observers = [];
  const doc = { documentElement: { style, getAttribute: (n) => attrs.get(n) ?? null } };
  const win = {};
  const fn = new Function(
    "window", "document", "localStorage", "sessionStorage", "location", "MutationObserver",
    "URLSearchParams", L.LOOK_BOOT_SCRIPT,
  );
  fn(win, doc, store(local), store(session), { search }, class {
    constructor(cb) { this.cb = cb; observers.push(this); }
    observe() {}
  }, URLSearchParams);
  return { style, attrs, session, win, observers };
}

const both = {
  day: { "--bg": "#eef2ff", "--text": "#1e1b4b", "--evil": "#000000" },
  night: { "--bg": "#0b1026", "--text": "url(x)" },
};
let r = runBoot({ cache: both, theme: "day" });
ok("boot applies the day set", r.style.props.get("--bg") === "#eef2ff" &&
   r.style.props.get("--text") === "#1e1b4b" && !r.style.props.has("--evil"));
r.attrs.set("data-puff-theme", "night");
r.observers[0].cb();
ok("boot follows a switch to night, re-checking values",
   r.style.props.get("--bg") === "#0b1026" && !r.style.props.has("--text"));
r = runBoot({ cache: both, search: "?puffLook=default" });
ok("?puffLook=default shows the bot's own look", r.style.props.size === 0 &&
   r.session.get(L.LOOK_OFF_KEY) === "1");
r = runBoot({ cache: both, off: "1" });
ok("the escape hatch lasts for the session", r.style.props.size === 0);
r = runBoot({ cache: both, off: "1", search: "?puffLook=on" });
ok("?puffLook=on ends it", r.style.props.get("--bg") === "#eef2ff");
r = runBoot({ cache: null });
ok("no saved look, nothing set", r.style.props.size === 0);
ok("boot script never writes style text or HTML",
   !/innerHTML|cssText|<style|insertRule|url\(/.test(L.LOOK_BOOT_SCRIPT));

// -- the background layers are look values too, and only amounts/colours -----
const bgVars = L.cleanVars({
  "--puff-stars": "1", "--puff-star": "rgba(255,255,255,.9)",
  "--puff-scene-glow1": "rgba(0,0,0,0)", "--puff-scene-end": "#0b1026",
  "--puff-vignette": "rgba(0,0,0,0)", "--puff-hero-glow": "0", "--imgTile": "0",
  "--puff-scene-glow2": "url(/design-cloud-1.jpg)", "--puff-bg-image": "url(x)",
});
ok("background layers: amounts and colours pass",
   bgVars["--puff-stars"] === "1" && bgVars["--puff-scene-end"] === "#0b1026" &&
   bgVars["--puff-hero-glow"] === "0" && bgVars["--imgTile"] === "0");
ok("background layers: no picture can be named",
   !("--puff-scene-glow2" in bgVars) && !("--puff-bg-image" in bgVars));
const bgStyle = fakeStyle();
L.setVars(bgStyle, { "--puff-stars": "1" });
L.setVars(bgStyle, null);
ok("going back clears the background values too", bgStyle.props.size === 0);

console.log(`\n${pass} passed`);
