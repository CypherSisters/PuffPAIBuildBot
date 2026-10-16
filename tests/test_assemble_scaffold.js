/*
 * Every view of the assembled app scaffold renders from the example specs in
 * tests/fixtures/assembled_specs, in plain node with a small DOM (no browser,
 * no packages). Run: node tests/test_assemble_scaffold.js
 * (tests/test_assemble_scaffold.py runs it under pytest.)
 */
"use strict";
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..");
const SCAFFOLD = path.join(ROOT, "src", "puffpai", "assemble", "scaffold");
const FIXTURES = path.join(__dirname, "fixtures", "assembled_specs");

// ── a small DOM, only what the views use ─────────────────────────────
class Node {
  constructor(tag, ns) {
    this.tagName = tag ? tag.toUpperCase() : "#text";
    this.ns = ns || "";
    this.childNodes = [];
    this.parentNode = null;
    this.attrs = {};
    this.listeners = {};
    this.className = "";
    this._text = "";
    this.value = "";
    this.checked = false;
    this.disabled = false;
  }
  get firstChild() { return this.childNodes[0] || null; }
  get textContent() { return this.tagName === "#text" ? this._text : this.childNodes.map((c) => c.textContent).join(""); }
  set textContent(v) { if (this.tagName === "#text") { this._text = String(v); return; } this.childNodes.forEach((c) => (c.parentNode = null)); this.childNodes = []; if (v !== "") this.appendChild(doc.createTextNode(v)); }
  appendChild(c) { if (c.parentNode) c.parentNode.removeChild(c); c.parentNode = this; this.childNodes.push(c); return c; }
  removeChild(c) { const i = this.childNodes.indexOf(c); if (i >= 0) this.childNodes.splice(i, 1); c.parentNode = null; return c; }
  remove() { if (this.parentNode) this.parentNode.removeChild(this); }
  setAttribute(k, v) { this.attrs[k] = String(v); if (k === "class") this.className = String(v); }
  getAttribute(k) { return k === "class" ? this.className : (k in this.attrs ? this.attrs[k] : null); }
  removeAttribute(k) { delete this.attrs[k]; }
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); }
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); }
  fire(t, extra) { const ev = Object.assign({ type: t, target: this, preventDefault() {} }, extra || {}); (this.listeners[t] || []).slice().forEach((f) => f(ev)); return ev; }
  click() { this.fire("click"); }
  scrollIntoView() {}
  canPlayType() { return ""; }
  all(pred) { const out = []; const walk = (n) => n.childNodes.forEach((c) => { if (pred(c)) out.push(c); walk(c); }); walk(this); return out; }
  tags(t) { return this.all((n) => n.tagName === t.toUpperCase()); }
  cls(c) { return this.all((n) => (" " + n.className + " ").indexOf(" " + c + " ") >= 0); }
  button(text) { return this.tags("button").find((b) => b.textContent === text); }
}
const doc = {
  listeners: {},
  createElement: (t) => new Node(t),
  createElementNS: (ns, t) => new Node(t, ns),
  createTextNode: (t) => { const n = new Node(null); n._text = String(t); return n; },
  addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
  removeEventListener(t, f) { this.listeners[t] = (this.listeners[t] || []).filter((x) => x !== f); },
  key(k) { (this.listeners.keydown || []).slice().forEach((f) => f({ key: k })); },
};

// ── load the scaffold the way the page does ──────────────────────────
require(path.join(SCAFFOLD, "vendor", "world.js"));
const V = require(path.join(SCAFFOLD, "views.js"));
assert.ok(globalThis.PUFF_WORLD_PATH && globalThis.PUFF_WORLD_PATH.startsWith("M"), "world outline loads");

const tick = async () => { for (let i = 0; i < 6; i++) await new Promise((r) => setImmediate(r)); };

function fixture(name) { return JSON.parse(fs.readFileSync(path.join(FIXTURES, name), "utf8")); }

function makeCtx(fx, over) {
  const calls = [];
  const saved = [];
  const fixes = [];
  const ctx = Object.assign({
    doc,
    spec: fx.spec,
    settings: Object.assign({}, fx.settings || {}),
    call: (id, input) => { calls.push([id, input]); return Promise.resolve(fx.rows[id] ? { rows: fx.rows[id] } : { error: "No such action: " + id }); },
    saveSetting: (k, v) => { saved.push([k, v]); return Promise.resolve({ ok: true, value: k === "home_town" ? Object.assign({ lat: 40.01, lon: -105.27 }, v) : v }); },
    postFix: (f) => { fixes.push(f); return Promise.resolve({ ok: true, message: "Switched on." }); },
    loadHls: () => Promise.resolve(null),
    openSettings: () => { ctx.opened = true; },
  }, over || {});
  ctx.calls = calls; ctx.saved = saved; ctx.fixes = fixes;
  return ctx;
}

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test("gallery: tiles, lightbox with prev/next and caption, search through the search action", async () => {
  const fx = fixture("pictures_gallery.json");
  const ctx = makeCtx(fx);
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  assert.deepStrictEqual(ctx.calls[0], ["latest_pictures", {}]);
  const tiles = el.cls("tile");
  assert.strictEqual(tiles.length, 3);
  assert.strictEqual(tiles[0].tags("img")[0].getAttribute("src"), "https://example.org/archive/a1.png");
  tiles[1].click();
  const box = el.cls("lightbox")[0];
  assert.ok(box, "lightbox opens");
  assert.strictEqual(box.cls("lb-title")[0].textContent, "Another view of the whole planet");
  assert.strictEqual(box.cls("lb-count")[0].textContent, "2 of 3");
  box.button("Next").click();
  assert.strictEqual(box.cls("lb-count")[0].textContent, "3 of 3");
  box.button("Next").click();
  assert.strictEqual(box.cls("lb-count")[0].textContent, "1 of 3");
  assert.ok(box.tags("a").some((a) => a.getAttribute("href") === "https://example.org/about/a1" && a.textContent === "Where it came from"));
  assert.ok(box.cls("lb-meta")[0].textContent.length > 0, "date shown");
  doc.key("ArrowLeft");
  assert.strictEqual(box.cls("lb-count")[0].textContent, "3 of 3");
  doc.key("Escape");
  assert.strictEqual(el.cls("lightbox").length, 0, "Escape closes");
  const input = el.tags("input")[0];
  input.value = "earth";
  el.tags("form")[0].fire("submit");
  await tick();
  assert.deepStrictEqual(ctx.calls[ctx.calls.length - 1], ["search_pictures", { q: "earth" }]);
  assert.strictEqual(el.cls("tile").length, 1);
});

test("gallery whose only action is a search opens with it, no words: the newest pictures", async () => {
  const fx = fixture("pictures_gallery.json");
  fx.spec.actions = fx.spec.actions.filter((a) => a.id === "search_pictures");
  fx.rows.search_pictures = fx.rows.latest_pictures;
  const ctx = makeCtx(fx);
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  assert.deepStrictEqual(ctx.calls[0], ["search_pictures", {}]);
  assert.strictEqual(el.cls("tile").length, 3, "pictures on open, not an error");
});

test("gallery without a search action filters what it has", async () => {
  const fx = fixture("pictures_gallery.json");
  fx.spec.actions = fx.spec.actions.filter((a) => a.id !== "search_pictures");
  const ctx = makeCtx(fx);
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  const input = el.tags("input")[0];
  input.value = "third";
  input.fire("input");
  assert.strictEqual(el.cls("tile").length, 1);
  assert.strictEqual(ctx.calls.length, 1, "no extra calls when filtering");
});

test("forecast: a card per period with icons and units; place setting asked for first", async () => {
  const fx = fixture("weather_forecast.json");
  const ctx = makeCtx(fx);
  assert.deepStrictEqual(V.missingSettings(ctx).map((s) => s.key), ["home_town"]);
  ctx.settings.home_town = { name: "Boulder", lat: 40.01, lon: -105.27 };
  assert.deepStrictEqual(V.missingSettings(ctx), []);
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  const cards = el.cls("card");
  assert.strictEqual(cards.length, 4);
  assert.deepStrictEqual(cards.map((c) => c.cls("card-icon")[0].textContent), ["⛅", "🌧", "☁", "☀"]);
  assert.strictEqual(cards[0].cls("card-temp")[0].textContent, "14°C");
  assert.ok(cards[1].textContent.includes("5 m/s"), "wind with its unit");
  assert.ok(el.textContent.includes("For Boulder"));
});

test("settings: town name with optional lat/lon, choice, key goes to the vault only", async () => {
  const fx = fixture("weather_forecast.json");
  const ctx = makeCtx(fx);
  let changed = 0;
  ctx.onSettings = () => changed++;
  const el = V.renderView({ type: "settings" }, ctx);
  const place = el.all((n) => n.attrs["data-kind"] === "place")[0];
  const town = place.all((n) => n.attrs.name === "home_town-name")[0];
  assert.ok(town && place.all((n) => n.attrs.name === "home_town-lat").length === 1 && place.all((n) => n.attrs.name === "home_town-lon").length === 1);
  town.value = "Boulder";
  place.tags("form")[0].fire("submit");
  await tick();
  assert.deepStrictEqual(ctx.saved[0], ["home_town", { name: "Boulder" }]);
  assert.strictEqual(ctx.settings.home_town.lat, 40.01, "server's position kept");
  assert.strictEqual(changed, 1);
  const lat = place.all((n) => n.attrs.name === "home_town-lat")[0];
  assert.strictEqual(lat.value, "40.01", "found position shown");
  const choice = el.all((n) => n.attrs["data-kind"] === "choice")[0];
  assert.strictEqual(choice.tags("option").length, 2);
  const key = el.all((n) => n.attrs["data-kind"] === "key")[0];
  assert.strictEqual(key.tags("input").length, 0, "no key field in the app");
  assert.ok(key.tags("a").some((a) => a.textContent === "Put it in your vault" && a.getAttribute("href") === fx.spec.vault_url));
  assert.strictEqual(el.all((n) => n.attrs.type === "password").length, 0);
});

test("player: HLS through hls.js and the media path, YouTube nocookie with its line, audio", async () => {
  const fx = fixture("live_player.json");
  const loaded = [];
  class FakeHls {
    static isSupported() { return true; }
    constructor(cfg) { this.cfg = cfg; FakeHls.last = this; }
    on() {}
    loadSource(u) { loaded.push(u); }
    attachMedia(m) { this.media = m; }
    destroy() { this.gone = true; }
  }
  FakeHls.Events = { ERROR: "hlsError" };
  const ctx = makeCtx(fx, { loadHls: () => Promise.resolve(FakeHls) });
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  assert.strictEqual(el.cls("chan").length, 3);
  assert.strictEqual(el.tags("video").length, 1);
  assert.deepStrictEqual(loaded, ["https://example.org/live/one/index.m3u8"]);
  assert.strictEqual(typeof FakeHls.last.cfg.xhrSetup, "function", "pieces go through the media path");
  const opened = [];
  FakeHls.last.cfg.xhrSetup({ open: (m, u) => opened.push(u) }, "https://example.org/live/one/seg1.ts");
  assert.deepStrictEqual(opened, ["/media?url=" + encodeURIComponent("https://example.org/live/one/seg1.ts")]);
  const first = FakeHls.last;
  el.button("Mission talk").click();
  assert.ok(first.gone, "old stream stopped");
  const frame = el.tags("iframe")[0];
  assert.strictEqual(frame.getAttribute("src"), "https://www.youtube-nocookie.com/embed/abcdefghijk");
  assert.ok(el.textContent.includes("Your browser talks to YouTube directly"));
  el.button("Radio").click();
  const audio = el.tags("audio")[0];
  assert.strictEqual(audio.getAttribute("src"), "/media?url=" + encodeURIComponent("https://example.org/radio.mp3"));
});

test("table: sort by a column, filter", async () => {
  const fx = fixture("records_table.json");
  const ctx = makeCtx(fx);
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  const firstName = () => el.tags("tbody")[0].tags("tr")[0].tags("td")[0].textContent;
  assert.strictEqual(el.tags("tbody")[0].tags("tr").length, 3);
  assert.strictEqual(firstName(), "Bravo");
  el.all((n) => n.attrs["data-col"] === "name")[0].click();
  assert.strictEqual(firstName(), "Alpha");
  el.all((n) => n.attrs["data-col"] === "name")[0].click();
  assert.strictEqual(firstName(), "Charlie");
  el.all((n) => n.attrs["data-col"] === "size")[0].click();
  assert.strictEqual(firstName(), "Alpha", "numbers sort as numbers");
  const input = el.tags("input")[0];
  input.value = "bra";
  input.fire("input");
  assert.strictEqual(el.tags("tbody")[0].tags("tr").length, 1);
});

test("map, list, form and chart render from rows", async () => {
  const fx = fixture("records_table.json");
  const ctx = makeCtx(fx);
  const m = V.renderView(fx.spec.views[1], ctx);
  await tick();
  const dots = m.tags("circle");
  assert.strictEqual(dots.length, 3);
  assert.ok(m.tags("path")[0].getAttribute("d").startsWith("M"), "world outline drawn");
  dots[1].click();
  assert.ok(m.cls("map-info")[0].textContent.startsWith("Alpha"));
  const l = V.renderView(fx.spec.views[2], ctx);
  await tick();
  assert.strictEqual(l.tags("li").length, 3);
  const f = V.renderView(fx.spec.views[3], ctx);
  assert.deepStrictEqual(f.tags("input").map((i) => i.attrs.name), ["name", "size"]);
  assert.strictEqual(f.tags("select")[0].tags("option").length, 2);
  f.tags("input")[0].value = "Al";
  f.tags("input")[1].value = "3";
  f.tags("select")[0].value = "small";
  f.tags("form")[0].fire("submit");
  await tick();
  assert.deepStrictEqual(ctx.calls[ctx.calls.length - 1], ["find_records", { name: "Al", size: 3, kind: "small" }]);
  assert.ok(f.textContent.includes("Alpha"));
  const wx = fixture("weather_forecast.json");
  const c = V.renderView(wx.spec.views[1], makeCtx(wx));
  await tick();
  const line = c.cls("line")[0];
  assert.ok(line && /^M[\d.]+,[\d.]+(L[\d.]+,[\d.]+){3}$/.test(line.getAttribute("d")), "4 points drawn");
});

test("links view and unknown view type", async () => {
  const fx = fixture("live_player.json");
  const el = V.renderView(fx.spec.views[1], makeCtx(fx));
  assert.strictEqual(el.tags("a")[0].getAttribute("href"), "https://example.org/streams");
  const bad = V.renderView({ type: "hologram" }, makeCtx(fx));
  assert.ok(bad.textContent.includes("does not have"));
});

test("an error shows the plain reason and a fix button that posts, then tries again", async () => {
  const fx = fixture("records_table.json");
  let n = 0;
  const ctx = makeCtx(fx, {
    call: () => (n++ === 0
      ? Promise.resolve({ error: "This service is switched off.", fix: { kind: "switch_on", label: "Switch it on", url: "/fix/switch_on?service=records" } })
      : Promise.resolve({ rows: fx.rows.all_records })),
  });
  const el = V.renderView(fx.spec.views[0], ctx);
  await tick();
  assert.ok(el.textContent.includes("This service is switched off."));
  el.button("Switch it on").click();
  await tick();
  assert.strictEqual(ctx.fixes.length, 1);
  assert.strictEqual(ctx.fixes[0].url, "/fix/switch_on?service=records");
  assert.strictEqual(el.tags("tbody")[0].tags("tr").length, 3, "loaded again after the fix");
  const outside = makeCtx(fx, { call: () => Promise.resolve({ error: "No.", fix: { kind: "key", url: "https://evil.example/steal" } }) });
  const el2 = V.renderView(fx.spec.views[0], outside);
  await tick();
  el2.button("Put the key in your vault").click();
  await tick();
  assert.strictEqual(outside.fixes.length, 0, "never posts outside this computer");
  const setting = makeCtx(fx, { call: () => Promise.resolve({ error: "Set your town.", fix: { kind: "setting", key: "home_town" } }) });
  const el3 = V.renderView(fx.spec.views[0], setting);
  await tick();
  el3.button("Open settings").click();
  assert.ok(setting.opened, "a setting fix opens settings");
  // A switch with no address here: the dashboard page around the app is asked
  // (it shows the card and does the switching); nothing is posted.
  const asked = [];
  const sw = makeCtx(fx, {
    call: () => Promise.resolve({ error: "Switched off.", fix: { kind: "switch_on", label: "Switch on Records now?", service: "card-records" } }),
    askDashboard: (f) => { asked.push(f); return true; },
  });
  const el4 = V.renderView(fx.spec.views[0], sw);
  await tick();
  el4.button("Switch on Records now?").click();
  await tick();
  assert.strictEqual(sw.fixes.length, 0, "never posted from the app's page");
  assert.strictEqual(asked.length, 1);
  assert.strictEqual(asked[0].service, "card-records");
  assert.ok(el4.textContent.includes("Look just above this app on your dashboard page."));
  const alone = makeCtx(fx, { call: () => Promise.resolve({ error: "Switched off.", fix: { kind: "switch_on", service: "card-records" } }) });
  const el5 = V.renderView(fx.spec.views[0], alone);
  await tick();
  el5.button("Switch it on").click();
  await tick();
  assert.ok(el5.textContent.includes("Do this from this app's page on your dashboard."));
});

test("addresses and words stay safe", () => {
  assert.strictEqual(V.safeUrl("javascript:alert(1)"), "");
  assert.strictEqual(V.safeUrl("data:text/html,x"), "");
  assert.strictEqual(V.safeUrl("//evil.example/x"), "");
  assert.strictEqual(V.safeUrl("https://ok.example/a.png"), "https://ok.example/a.png");
  // An item opens its human page or nothing, never its raw API record.
  assert.strictEqual(V.pageUrl("https://ll.thespacedevs.com/2.3.0/launches/e3df2ecd/"), "");
  assert.strictEqual(V.pageUrl("https://api.example.org/items/1"), "");
  assert.strictEqual(V.pageUrl("https://example.org/data/items.json"), "");
  assert.strictEqual(V.pageUrl("https://example.org/x?format=json"), "");
  assert.strictEqual(V.pageUrl("https://www.example.org/launch/falcon-9"), "https://www.example.org/launch/falcon-9");
  assert.strictEqual(V.pageUrl("javascript:alert(1)"), "");
  assert.strictEqual(V.pageUrl("/local/page"), "");
  assert.strictEqual(V.youTube("https://youtu.be/abcdefghijk"), "https://www.youtube-nocookie.com/embed/abcdefghijk");
  assert.strictEqual(V.youTube("https://evil.example/watch?v=abcdefghijk"), "");
  const src = fs.readFileSync(path.join(SCAFFOLD, "views.js"), "utf8") + fs.readFileSync(path.join(SCAFFOLD, "app.js"), "utf8");
  assert.ok(!/innerHTML|outerHTML|insertAdjacentHTML|document\.write|eval\(/.test(src), "words are never parsed as HTML");
  const html = fs.readFileSync(path.join(SCAFFOLD, "index.html"), "utf8");
  assert.ok(!/https?:\/\/(?!www\.youtube-nocookie\.com)/.test(html.replace(/<meta http-equiv[^>]*>/, "")), "no outside addresses in the page");
  const words = src + fs.readFileSync(path.join(SCAFFOLD, "app.css"), "utf8") + html;
  assert.ok(!/—/.test(words), "no em dashes");
  assert.ok(!/\bdoors?\b/i.test(words) && !/\bbeta\b/i.test(words), "plain words");
  assert.ok(!/space|nasa|epic|7timer/i.test(fs.readFileSync(path.join(SCAFFOLD, "views.js"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/whitespace|white-space/g, "")), "views stay generic");
});

(async () => {
  let failed = 0;
  for (const [name, fn] of tests) {
    try { await fn(); console.log("ok   " + name); } catch (e) { failed++; console.log("FAIL " + name + "\n     " + (e && e.stack || e)); }
  }
  console.log(failed ? failed + " failed" : "all " + tests.length + " passed");
  process.exit(failed ? 1 : 0);
})();
