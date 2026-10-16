// The bot's hidden browser for integrated web apps.
// Copyright (c) Cypher Sisters. Apache-2.0 License.
//
// Started by puffpai/webapps/runner_host.py with Edge or Chrome by absolute
// path. The control channel is this process's stdin and stdout: the first line
// is the setup (browser path, profile, wall flags, the host's per-boot runner
// secret, the host port), then one JSON command per line, one JSON reply per
// line. There is no TCP server, no token and no "evaluate this" command: only
// the fixed verbs below, whose page code is fixed here and gets values only as
// JSON data.
//
// The wall (three layers, each enough on its own for the cases it covers):
//  1. browser flags from runner_host.WALL_FLAGS: every request that is not to
//     an installed app host goes to a refuse-all logging proxy, name lookups
//     for anything else fail, WebRTC may not send UDP outside a proxy;
//  2. on every tab and every frame or worker it starts: Fetch.enable with
//     pattern "*", and every request whose origin is not that tab's app origin
//     is failed (BlockedByClient) before it is sent; a top-level move to any
//     other address (file:, data:, chrome:, javascript:, another site) closes
//     the tab;
//  3. new targets start paused (auto-attach, wait for debugger): any page this
//     runner did not create (a popup) is closed before it runs; downloads are
//     denied.
// App page code is read and driven from an isolated world, so the app cannot
// change what the runner sees by replacing page functions.
//
// The runner secret rides only on the tab's own top-level load
// of one of the app's own pages (never an address under /__puffpai/, never a
// frame or worker, never anything else the page sends). The bot's copy of
// the app's storage is saved by this process itself, over its own connection
// to the host: it reads the page's storage as the browser holds it (DevTools,
// no script in the page) and sends it with the secret. The page's own script never saves in this tab (the host
// tells the shim so), so nothing an app's script sends can count as the bot's.
import { createInterface } from "node:readline";
import http from "node:http";
import { launch, sleep } from "./cdp.mjs";

const LABEL_RE = /^(?:[a-z0-9][a-z0-9-]{0,29}-[a-z2-7]{8}|chk-[a-z2-7]{16})$/;
const STEP_VERBS = new Set(["click", "click_text", "click_in_row", "fill", "fill_in_row", "choose",
  "choose_in_row", "check_in_row", "key", "wait", "wait_for", "accept_next_dialog", "read_rows",
  "read_storage", "set_files"]);
const KEYS = { Enter: 13, Tab: 9, Escape: 27 };
const IDLE_MS = 10 * 60 * 1000;

let CFG = null;
let cdp = null;
const pages = new Map();          // label -> page
const bySession = new Map();      // sessionId -> page (its own and its children's)
const claimed = new Set();        // targetIds this runner created
const waiting = new Map();        // targetId -> {sessionId, t} (paused, not yet claimed)
const walled = new Map();         // label -> {n, origins, left}: what the wall stopped (kept after a tab closes)

const out = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
const send = (m, p, s) => cdp.send(m, p || {}, s);
const originOf = (label) => `http://${label}.localhost:${CFG.port}`;
const sameOrigin = (page, url) => { try { return new URL(url).origin === page.origin; } catch { return false; } };
const pathOf = (url) => { try { return new URL(url).pathname; } catch { return ""; } };
// The only page requests that carry the runner secret: the tab's own
// top-level load of one of the app's own pages (a GET, never under
// /__puffpai/). Storage is never saved through the page (saveCopy below).
function runnerOwn(page, session, p) {
  if (session !== page.session || p.resourceType !== "Document") return false;
  if (p.frameId !== page.targetId || String(p.request.method || "").toUpperCase() !== "GET") return false;
  const path = pathOf(p.request.url);
  return path !== "" && !path.startsWith("/__puffpai/") && !/^\/__puffpai(?:$|[?#])/.test(path);
}

// One write of the page's storage to the bot's copy, from this process (the
// runner's own channel to the host on loopback; never through the page).
function hostPut(label, body) {
  return new Promise((resolve) => {
    let data;
    try { data = Buffer.from(JSON.stringify(body)); } catch { resolve(null); return; }
    const req = http.request({
      host: "127.0.0.1", port: +CFG.port, method: "PUT", path: "/__puffpai/storage", timeout: 8000,
      headers: { Host: `${label}.localhost:${CFG.port}`, Origin: originOf(label),
                 "Content-Type": "application/json", "Content-Length": data.length,
                 "X-PuffPAI-Runner": CFG.secret },
    }, (res) => { res.resume(); res.on("end", () => resolve({ status: res.statusCode })); });
    req.on("error", () => resolve(null));
    req.on("timeout", () => { req.destroy(); resolve(null); });
    req.end(data);
  });
}
const withTimeout = (p, ms) => Promise.race([p, new Promise((_r, rej) => setTimeout(() => rej(new Error("slow")), ms))]);

// The page's storage as the browser holds it (DevTools DOMStorage: no script
// runs in the page, so the app's code has no say in it, and it answers while
// a load of the page waits); the isolated world when that is not there.
async function readStorage(page, ms) {
  try {
    const r = await withTimeout(send("DOMStorage.getDOMStorageItems",
      { storageId: { securityOrigin: page.origin, isLocalStorage: true } }, page.session), ms);
    if (r && Array.isArray(r.entries)) {
      const o = {};
      for (const e of r.entries) if (Array.isArray(e) && typeof e[0] === "string") o[e[0]] = String(e[1] ?? "");
      return o;
    }
  } catch {}
  return await withTimeout(iso(page, call(pageStorage)), ms);
}

// Save what the page now holds to the bot's copy (if it changed), never
// through the app's own functions.
async function saveCopy(page, ms = 4000) {
  if (!page || page.bad) return;
  if (page.saving) return page.saving;
  page.saving = (async () => {
    let data = null;
    try { data = await readStorage(page, ms); } catch { return; }
    if (!data || typeof data !== "object") return;
    const text = JSON.stringify(data);
    if (text === page.saved) return;
    const r = await hostPut(page.label, { base: "served", data });
    if (r && r.status === 200) page.saved = text;
  })().finally(() => { page.saving = null; });
  return page.saving;
}
// only the origin (or the scheme) is kept, never a path or query
const originOnly = (url) => { try { const u = new URL(url); return /^https?:$/.test(u.protocol) ? u.origin : u.protocol; } catch { return "other"; } };
function wallNote(page, kind, url) {
  const rec = walled.get(page.label) || { n: 0, origins: [], left: [] };
  const o = originOnly(url);
  if (kind === "blocked") { rec.n++; if (rec.origins.length < 20 && !rec.origins.includes(o)) rec.origins.push(o); }
  else if (rec.left.length < 20 && !rec.left.includes(o)) rec.left.push(o);
  walled.set(page.label, rec);
}

// ---------------------------------------------------------------------------
// targets
// ---------------------------------------------------------------------------
function onEvent(m) {
  const s = m.sessionId;
  const p = m.params || {};
  if (m.method === "Target.attachedToTarget") {
    const info = p.targetInfo || {};
    if (!s) {                                   // a new top-level target
      if (info.type === "page") {
        if (claimed.has(info.targetId)) waiting.set(info.targetId, { sessionId: p.sessionId, t: Date.now() });
        else if (info.openerId || info.openerFrameId) closeTarget(info.targetId);
        else waiting.set(info.targetId, { sessionId: p.sessionId, t: Date.now() });
      } else {
        const owner = [...pages.values()].find((pg) => sameOrigin(pg, info.url));
        if (owner) attachChild(owner, p.sessionId);
        else closeTarget(info.targetId);
      }
      return;
    }
    const owner = bySession.get(s);           // a frame or worker of one of our tabs
    if (owner) attachChild(owner, p.sessionId);
    else send("Runtime.runIfWaitingForDebugger", {}, p.sessionId).catch(() => {});
    return;
  }
  if (m.method === "Fetch.requestPaused") {
    const page = bySession.get(s);
    if (page && sameOrigin(page, p.request.url)) {
      // The runner secret rides only on the runner's own requests: its tab's
      // own page load and the bot copy's storage writes the host must count
      // as the bot's. Never on the app's other requests (so the app's own
      // script can never pass a runner-only check, /__puffpai/files among
      // them), and never from a frame or worker the app made.
      if (runnerOwn(page, s, p)) {
        const headers = Object.entries(p.request.headers || {})
          .filter(([name]) => name.toLowerCase() !== "x-puffpai-runner")
          .map(([name, value]) => ({ name, value: String(value) }));
        headers.push({ name: "X-PuffPAI-Runner", value: CFG.secret });
        const go = () => send("Fetch.continueRequest", { requestId: p.requestId, headers }, s).catch(() => {});
        if (page.ownNav) {
          page.ownNav = false;      // this process's own load: saved before it started
          go();
        } else {
          // the app moved its own page (it saves and reloads at once): what
          // the leaving page holds is saved first, then the load goes on
          saveCopy(page, 1500).catch(() => {}).finally(go);
        }
      } else {
        send("Fetch.continueRequest", { requestId: p.requestId }, s).catch(() => {});
      }
    } else {
      if (page) { page.blocked++; wallNote(page, "blocked", p.request.url); }
      send("Fetch.failRequest", { requestId: p.requestId, errorReason: "BlockedByClient" }, s).catch(() => {});
    }
    return;
  }
  if (m.method === "Page.frameNavigated") {
    const page = bySession.get(s);
    if (page && page.session === s && !p.frame.parentId) {
      page.world = null;
      if (!sameOrigin(page, p.frame.url) && !(p.frame.url === "about:blank" && !page.started)) {
        page.bad = "the app tried to leave its own address";
        wallNote(page, "left", p.frame.url);
        closePage(page.label).catch(() => {});
      }
    }
    return;
  }
  if (m.method === "Runtime.executionContextsCleared") {
    const page = bySession.get(s);
    if (page && page.session === s) page.world = null;
    return;
  }
  if (m.method === "Page.javascriptDialogOpening") {
    const page = bySession.get(s);
    const accept = !!(page && page.autoDialog);
    if (page) page.dialogs.push({ type: p.type, message: String(p.message || "").slice(0, 200), answered: accept ? "yes" : "no" });
    send("Page.handleJavaScriptDialog", { accept }, s).catch(() => {});
    return;
  }
  if (m.method === "Target.targetDestroyed" || m.method === "Target.detachedFromTarget") {
    for (const page of pages.values()) {
      if (page.targetId === p.targetId || page.session === p.sessionId) {
        pages.delete(page.label);
        for (const [k, v] of bySession) if (v === page) bySession.delete(k);
      }
    }
  }
}

function closeTarget(targetId) {
  send("Target.closeTarget", { targetId }).catch(() => {});
}

async function attachChild(page, sessionId) {
  bySession.set(sessionId, page);
  page.children.add(sessionId);
  try {
    await send("Fetch.enable", { patterns: [{ urlPattern: "*" }] }, sessionId).catch(() => {});
    await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true }, sessionId).catch(() => {});
    await send("Page.enable", {}, sessionId).catch(() => {});
  } finally {
    send("Runtime.runIfWaitingForDebugger", {}, sessionId).catch(() => {});
  }
}

// paused pages nobody claimed within 1.5 s are popups: close them
setInterval(() => {
  const now = Date.now();
  for (const [targetId, w] of waiting) {
    if (!claimed.has(targetId) && now - w.t > 1500) { waiting.delete(targetId); closeTarget(targetId); }
  }
  for (const page of [...pages.values()]) {
    if (now - page.lastUsed > IDLE_MS) closePage(page.label).catch(() => {});
  }
}, 500).unref();

// ---------------------------------------------------------------------------
// pages
// ---------------------------------------------------------------------------
async function openPage(label) {
  if (!LABEL_RE.test(String(label || ""))) throw new Error("bad app label");
  if (!CFG.labels.includes(label)) throw new Error("that app is not open to the runner");
  let page = pages.get(label);
  if (page) { page.lastUsed = Date.now(); return page; }
  const { targetId } = await send("Target.createTarget", { url: "about:blank" });
  claimed.add(targetId);
  let w = null;
  for (let i = 0; i < 60 && !(w = waiting.get(targetId)); i++) await sleep(50);
  if (!w) { closeTarget(targetId); throw new Error("the page did not start"); }
  waiting.delete(targetId);
  const session = w.sessionId;
  page = { label, origin: originOf(label), targetId, session, children: new Set(), dialogs: [],
           autoDialog: false, blocked: 0, bad: null, world: null, started: false, lastUsed: Date.now() };
  bySession.set(session, page);
  pages.set(label, page);
  try {
    await send("Runtime.enable", {}, session);
    await send("Page.enable", {}, session);
    await send("Network.enable", {}, session);
    await send("Fetch.enable", { patterns: [{ urlPattern: "*" }] }, session);
    await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true }, session);
    await send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 900, deviceScaleFactor: 1, mobile: false }, session);
    // every hidden tab acts as the focused one, so a key it is sent reaches
    // the field that has the focus (a form that saves on Enter)
    await send("Emulation.setFocusEmulationEnabled", { enabled: true }, session).catch(() => {});
    await send("IndexedDB.enable", {}, session).catch(() => {});
    await send("DOMStorage.enable", {}, session).catch(() => {});
  } finally {
    await send("Runtime.runIfWaitingForDebugger", {}, session).catch(() => {});
  }
  await gotoApp(page);
  return page;
}

async function gotoApp(page) {
  // what the page holds is saved before this process moves it (the page is
  // idle now; a save while the load waits would wait for the load itself)
  await saveCopy(page).catch(() => {});
  page.ownNav = true;
  page.world = null;
  page.started = true;
  try {
    const r = await send("Page.navigate", { url: page.origin + "/" }, page.session);
    if (r && r.errorText) throw new Error("the app did not open");
    for (let i = 0; i < 80; i++) {
      await sleep(100);
      if (page.bad) throw new Error(page.bad);
      try { if (await iso(page, "document.readyState") === "complete") { await sleep(300); return; } } catch {}
    }
    throw new Error("the app did not open");
  } finally {
    page.ownNav = false;
  }
}

async function closePage(label) {
  const page = pages.get(label);
  if (!page) return;
  // what the page holds is saved before it closes (a late save of the app's)
  await saveCopy(page).catch(() => {});
  pages.delete(label);
  for (const [k, v] of bySession) if (v === page) bySession.delete(k);
  let failed = null;
  await send("Target.closeTarget", { targetId: page.targetId }).catch((e) => { failed = e; });
  if (label.startsWith("chk-")) await clearOrigin(page.origin);
  // the caller proves an action's effect only once the page can write no more
  if (failed) throw new Error("the page did not close");
}

async function clearOrigin(origin) {
  await send("Storage.clearDataForOrigin", { origin, storageTypes: "all" }).catch(() => {});
}

// evaluate fixed code in an isolated world of the app's own top frame
async function iso(page, expr) {
  if (page.bad) throw new Error(page.bad);
  for (let attempt = 0; attempt < 2; attempt++) {
    if (!page.world) {
      const { frameTree } = await send("Page.getFrameTree", {}, page.session);
      if (!sameOrigin(page, frameTree.frame.url)) throw new Error("the app is not on its own page");
      const { executionContextId } = await send("Page.createIsolatedWorld",
        { frameId: frameTree.frame.id, worldName: "puffpai-runner", grantUniveralAccess: false }, page.session);
      page.world = executionContextId;
    }
    try {
      const r = await send("Runtime.evaluate", { expression: expr, contextId: page.world, returnByValue: true, awaitPromise: true }, page.session);
      if (r.exceptionDetails) throw Object.assign(new Error(r.exceptionDetails.exception?.description || r.exceptionDetails.text), { page: true });
      return r.result.value;
    } catch (e) {
      if (e.page) throw e;
      page.world = null;
      if (attempt) throw e;
    }
  }
}
const call = (fn, ...args) => `(${fn.toString()})(...${JSON.stringify(args)})`;

// ---------------------------------------------------------------------------
// page code (runs in the isolated world; self-contained; values come as data)
// ---------------------------------------------------------------------------
function pageSnapshot(max) {
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const visible = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== "hidden" && s.display !== "none"; };
  const uniq = (sel) => { try { return document.querySelectorAll(sel).length === 1; } catch (x) { return false; } };
  const plain = (s) => !/[\\"'`]/.test(s);
  const cssPath = (e) => {
    const t = e.tagName.toLowerCase();
    const cands = [];
    if (e.id && /^[A-Za-z][\w-]*$/.test(e.id)) cands.push("#" + e.id);
    for (const a of ["name", "aria-label", "placeholder", "data-testid", "data-id", "data-action", "title"]) {
      const v = e.getAttribute(a);
      if (v && v.length <= 60 && plain(v)) cands.push(t + "[" + a + '="' + v + '"]');
    }
    const cls = [...e.classList].filter((c) => /^[A-Za-z][\w-]*$/.test(c)).map((c) => "." + c).join("");
    const anc = e.parentElement && e.parentElement.closest("[id]");
    if (anc && /^[A-Za-z][\w-]*$/.test(anc.id)) cands.push("#" + anc.id + " " + t + cls);
    if (cls) cands.push(t + cls);
    for (const c of cands) if (uniq(c)) return c;
    return null;
  };
  const labelOf = (e) => {
    if (e.getAttribute("aria-label")) return e.getAttribute("aria-label");
    const lb = e.getAttribute("aria-labelledby");
    if (lb) return lb.split(" ").map((id) => (document.getElementById(id) || {}).innerText || "").join(" ");
    if (e.id) { const l = document.querySelector('label[for="' + CSS.escape(e.id) + '"]'); if (l) return l.innerText; }
    const wrap = e.closest("label"); if (wrap && wrap !== e) return wrap.innerText;
    const prev = e.previousElementSibling;
    if (prev && !/^(INPUT|SELECT|TEXTAREA|BUTTON|A)$/.test(prev.tagName) && clean(prev.innerText).length > 0 && clean(prev.innerText).length < 40) return prev.innerText;
    return e.getAttribute("placeholder") || e.getAttribute("title") || e.getAttribute("name") || "";
  };
  const INTERACTIVE = 'a[href],button,input,select,textarea,summary,[role=button],[role=tab],[role=checkbox],[role=link],[role=menuitem],[role=switch],[contenteditable=true]';
  document.querySelectorAll("[data-pp-ref]").forEach((e) => e.removeAttribute("data-pp-ref"));
  const controls = []; const headings = []; let n = 0; let idx = 0;
  for (const e of document.querySelectorAll("body *")) {
    idx++;
    const tag = e.tagName.toLowerCase();
    if (/^(script|style|template|noscript|svg|path|option)$/.test(tag)) continue;
    if (/^(h[1-6]|legend|caption)$/.test(tag) || e.getAttribute("role") === "heading") {
      if (visible(e)) headings.push({ idx, text: clean(e.innerText).slice(0, 80) });
      continue;
    }
    if (!e.matches(INTERACTIVE) || !visible(e)) continue;
    if (controls.length >= max) break;
    const type = (e.getAttribute("type") || "").toLowerCase();
    if (tag === "input" && type === "hidden") continue;
    const ref = "e" + (++n); e.setAttribute("data-pp-ref", ref);
    const role = e.getAttribute("role") || (tag === "a" ? "link" : tag === "select" ? "choose" :
      tag === "textarea" ? "text" : tag === "input" ? (["checkbox", "radio"].includes(type) ? type :
      ["button", "submit", "reset", "image"].includes(type) ? "button" : type === "file" ? "file" :
      ["date", "time", "datetime-local", "month", "week", "number", "range", "color", "email", "tel", "url", "search", "password"].includes(type) ? type : "text") :
      tag === "button" ? "button" : "control");
    const field = ["input", "select", "textarea"].includes(tag);
    const name = clean(field ? labelOf(e) : (e.getAttribute("aria-label") || e.innerText || e.value || e.getAttribute("title") || labelOf(e))).slice(0, 60);
    const c = { ref, idx, tag, type, role, name, text: clean(e.innerText || (field ? "" : e.value)).slice(0, 60),
                sel: cssPath(e), disabled: !!e.disabled };
    if (tag === "select") c.options = [...e.options].slice(0, 40).map((o) => ({ v: o.value, t: clean(o.text).slice(0, 60), disabled: !!o.disabled }));
    if (field) {
      c.value = clean(e.value).slice(0, 60);
      if (e.min !== undefined && e.min !== "") c.min = e.min;
      if (e.max !== undefined && e.max !== "") c.max = e.max;
      if (e.step !== undefined && e.step !== "") c.step = e.step;
      if (e.maxLength > 0) c.maxLength = e.maxLength;
      c.required = !!e.required;
    }
    if (["checkbox", "radio"].includes(type)) c.checked = !!e.checked;
    controls.push(c);
  }
  return { title: document.title, controls, headings };
}

function pageLocate(ref) {
  const e = document.querySelector('[data-pp-ref="' + ref + '"]');
  if (!e) return null;
  e.scrollIntoView({ block: "center", behavior: "instant" });
  const b = e.getBoundingClientRect();
  window.__ppLast = e;
  return { x: b.left + b.width / 2, y: b.top + b.height / 2, text: String(e.innerText || e.value || e.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim().slice(0, 80) };
}

function pageFind(sel, row, key, match, text, hiddenOk) {
  const norm = (t) => String(t || "").replace(/\s+/g, " ").trim();
  let scope = document;
  if (row) {
    // The row must be the ONLY one that matches: with two rows of the same
    // name, the bot cannot know which one the owner meant, so it touches none.
    const want = String(match).toLowerCase();
    const wordsOf = (r) => { const k = key ? r.querySelector(key) : r; return k ? norm(k.innerText || k.value).toLowerCase() : null; };
    const rows = [...document.querySelectorAll(row)];
    let hits = rows.filter((r) => wordsOf(r) === want);
    if (!key && hits.length === 0) hits = rows.filter((r) => { const w = wordsOf(r); return w !== null && w.includes(want); });
    if (hits.length > 1) return { ambiguous: hits.length };
    scope = hits[0];
    if (!scope) return null;
  }
  let els = [...scope.querySelectorAll(sel)];
  if (text) els = els.filter((e) => norm(e.innerText || e.value) === text);
  const e = els.find((x) => { if (hiddenOk) return true; const b = x.getBoundingClientRect(); return b.width > 0 && b.height > 0; }) || null;
  if (!e) return null;
  e.scrollIntoView({ block: "center", behavior: "instant" });
  const b = e.getBoundingClientRect();
  window.__ppLast = e;
  return { x: b.left + b.width / 2, y: b.top + b.height / 2, checked: !!e.checked,
           text: norm(e.innerText || e.value || e.getAttribute("aria-label")).slice(0, 80) };
}

function pageSetValue(v) {
  const e = window.__ppLast; if (!e) return "gone";
  const t = (e.type || "").toLowerCase();
  if (["date", "time", "datetime-local", "month", "week", "color", "range", "number"].includes(t)) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set.call(e, v);
    e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true }));
    return "set";
  }
  e.focus(); if (e.select) e.select(); if (e.isContentEditable) document.execCommand("selectAll");
  return "type";
}

function pageChanged() {
  const e = window.__ppLast; if (!e) return false;
  e.dispatchEvent(new Event("change", { bubbles: true })); return true;
}

function pageChoose(want) {
  const e = window.__ppLast; if (!e || e.tagName !== "SELECT") return false;
  const o = [...e.options].find((x) => x.value === want || x.text.trim() === want);
  if (!o) return false;
  e.value = o.value; e.dispatchEvent(new Event("input", { bubbles: true })); e.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function pageStorage() {
  const o = {};
  for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); o[k] = localStorage.getItem(k); }
  return o;
}

function pageCount(sel) {
  try { return { count: document.querySelectorAll(sel).length }; } catch (e) { return { error: "does not parse" }; }
}

function pageReadRows(sel, cells, max) {
  return [...document.querySelectorAll(sel)].filter((r) => r.getBoundingClientRect().height > 0).slice(0, max)
    .map((r) => [...r.querySelectorAll(cells)].map((c) => String(c.innerText || "").replace(/\s+/g, " ").trim().slice(0, 200)));
}

function pageGetItem(key) { return localStorage.getItem(key); }

// The repeated row that shows `text` in one of its cells, and the controls in
// it, each with a selector that works inside ANY such row (for the row
// compiler, puffpai/webapps/compile_rows.py). Returns null when there is none.
function pageRowControls(text, max) {
  const norm = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const want = norm(text).toLowerCase();
  if (!want) return null;
  const safe = (c) => /^[A-Za-z][\w-]{0,40}$/.test(c);
  const sig = (e) => e.tagName.toLowerCase() + [...e.classList].filter(safe).map((c) => "." + c).join("");
  const visible = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  // the deepest element whose whole text is the key text
  let cell = [...document.querySelectorAll("body *")].find((e) =>
    !/^(SCRIPT|STYLE|TEMPLATE|OPTION|SELECT|TEXTAREA)$/.test(e.tagName) && norm(e.innerText).toLowerCase() === want);
  if (!cell) return null;
  for (;;) {
    const kid = [...cell.children].find((k) => norm(k.innerText).toLowerCase() === want);
    if (!kid) break;
    cell = kid;
  }
  // the row: nearest tr, li, [role=row], or an element whose siblings share its signature
  let row = null;
  for (let e = cell.parentElement; e && e !== document.body; e = e.parentElement) {
    if (/^(TR|LI)$/.test(e.tagName) || e.getAttribute("role") === "row") { row = e; break; }
    const p = e.parentElement;
    if (p && [...p.children].filter((x) => x !== e && sig(x) === sig(e)).length >= 1 && sig(e).includes(".")) { row = e; break; }
  }
  if (!row) return null;
  const rowSig = sig(row);
  const parent = row.parentElement;
  const cands = [];
  if (row.getAttribute("role") === "row" && !rowSig.includes(".")) cands.push(row.tagName.toLowerCase() + "[role=row]");
  if (rowSig.includes(".")) cands.push(rowSig);
  if (parent && parent.id && safe(parent.id)) cands.push("#" + parent.id + " > " + rowSig);
  if (parent && [...parent.classList].some(safe)) cands.push(sig(parent) + " > " + rowSig);
  cands.push(rowSig);
  let rowSel = null;
  for (const c of cands) {
    let all; try { all = [...document.querySelectorAll(c)]; } catch (x) { continue; }
    if (all.includes(row) && all.every((r) => r.tagName === row.tagName)) { rowSel = c; break; }
  }
  if (!rowSel) return null;
  // the key cell, relative to the row
  const within = (base, sel, el) => { try { const f = base.querySelectorAll(sel); return f.length === 1 && f[0] === el; } catch (x) { return false; } };
  const relSel = (el) => {
    const t = el.tagName.toLowerCase();
    const out = [];
    const s1 = sig(el); if (s1.includes(".")) out.push(s1);
    for (const a of ["name", "title", "aria-label", "data-field", "data-name", "data-action"]) {
      const v = el.getAttribute(a);
      if (v && v.length <= 40 && /^[A-Za-z0-9 _-]+$/.test(v)) out.push(t + "[" + a + '="' + v + '"]');
    }
    if (t === "input" && el.type) out.push("input[type=" + el.type.toLowerCase().replace(/[^a-z-]/g, "") + "]");
    if (el.parentElement) {
      const same = [...el.parentElement.children].filter((x) => x.tagName === el.tagName);
      out.push(t + ":nth-of-type(" + (same.indexOf(el) + 1) + ")");
    }
    out.push(t);
    return out.find((c) => within(row, c, el)) || null;
  };
  if (cell === row) return null;
  const keySel = relSel(cell);
  if (!keySel) return null;
  const controls = [];
  for (const el of row.querySelectorAll("button, a[href], select, input[type=checkbox], [role=button]")) {
    if (controls.length >= max) break;
    if (!visible(el)) continue;
    const t = el.tagName.toLowerCase();
    const type = (el.getAttribute("type") || "").toLowerCase();
    const role = t === "select" ? "choose" : (t === "input" && type === "checkbox") ? "checkbox" : t === "a" ? "link" : "button";
    const sel = relSel(el);
    const c = { tag: t, type, role, sel, disabled: !!el.disabled,
                text: norm(el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title")).slice(0, 60),
                name: norm(el.getAttribute("aria-label") || el.getAttribute("title") || el.innerText).slice(0, 60) };
    if (t === "select") { c.options = [...el.options].slice(0, 60).map((o) => ({ v: o.value, t: norm(o.text).slice(0, 60), disabled: !!o.disabled })); c.value = norm(el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : ""); }
    if (role === "checkbox") c.checked = !!el.checked;
    controls.push(c);
  }
  return { row: rowSel, key: keySel, controls };
}

function pageClearAll() {
  localStorage.clear(); sessionStorage.clear();
  return true;
}

// ---------------------------------------------------------------------------
// input
// ---------------------------------------------------------------------------
async function clickAt(page, at) {
  for (const type of ["mouseMoved", "mousePressed", "mouseReleased"]) {
    await send("Input.dispatchMouseEvent", { type, x: at.x, y: at.y, button: "left", clickCount: 1 }, page.session);
  }
}
async function key(page, k) {
  if (!(k in KEYS)) throw new Error("key not allowed");
  // the same events a real keyboard sends (with "code" and the key's text),
  // so a form that saves on Enter submits (a to-do app's field)
  const text = k === "Enter" ? "\r" : undefined;
  await send("Input.dispatchKeyEvent", { type: text ? "keyDown" : "rawKeyDown", key: k, code: k,
    windowsVirtualKeyCode: KEYS[k], nativeVirtualKeyCode: KEYS[k], text, unmodifiedText: text }, page.session);
  await send("Input.dispatchKeyEvent", { type: "keyUp", key: k, code: k, windowsVirtualKeyCode: KEYS[k],
    nativeVirtualKeyCode: KEYS[k] }, page.session);
}

// ---------------------------------------------------------------------------
// compiled steps (data only; checked by Drawbridge before any real run)
// ---------------------------------------------------------------------------
const pick = (obj, path) => (path ? path.split(".") : []).reduce((acc, part) => {
  if (acc === null || acc === undefined) return acc;
  if (part === "*") return Array.isArray(acc) ? acc : Object.values(acc);
  return Array.isArray(acc) ? acc.flatMap((x) => (x && x[part] !== undefined ? (Array.isArray(x[part]) ? x[part] : [x[part]]) : [])) : acc[part];
}, obj);

function argValue(recipe, args, name) {
  const spec = (recipe.args || {})[name];
  if (!spec) throw new Error("the action uses an unknown detail");
  let v = args[name] ?? spec.default ?? "";
  if (v === "$today") { const d = new Date(); v = new Date(d - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10); }
  return v;
}

function checkArgs(recipe, args) {
  for (const k of Object.keys(args)) if (!(k in (recipe.args || {}))) throw new Error("unknown detail " + k);
  for (const [k, spec] of Object.entries(recipe.args || {})) {
    const v = args[k];
    if (v === undefined || v === "") { if (spec.required) throw new Error(k + " is needed"); continue; }
    if (spec.enum && !spec.enum.includes(v)) throw new Error(k + " must be one of the app's choices");
    if (spec.type === "number" && (typeof v !== "number" || v < (spec.min ?? -1e12) || v > (spec.max ?? 1e12))) throw new Error(k + " must be a number");
    if (spec.type === "string" && (typeof v !== "string" || v.length > (spec.maxLength || 200))) throw new Error(k + " is too long");
    if (spec.pattern && !new RegExp(spec.pattern).test(String(v))) throw new Error(k + " is not in the right form");
  }
}

async function runRecipe(page, recipe, args) {
  if (!recipe || !Array.isArray(recipe.steps) || recipe.steps.length > 40) throw new Error("not an action");
  checkArgs(recipe, args);
  if (recipe.start === "reload") await gotoApp(page);
  page.autoDialog = false;
  const before = page.dialogs.length;
  const results = {};
  const clicked = [];
  try {
    for (const step of recipe.steps) {
      if (!STEP_VERBS.has(step.do)) throw new Error("step not allowed");
      if (step.if_arg && (args[step.if_arg] === undefined || args[step.if_arg] === "")) continue;
      const val = step.arg ? String(argValue(recipe, args, step.arg)) : step.value !== undefined ? String(step.value) : undefined;
      const match = step.match_arg ? String(argValue(recipe, args, step.match_arg)) : "";
      const find = async () => {
        const at = await iso(page, call(pageFind, step.sel || "*", step.row || "", step.key || "", match, step.text || "", !!step.hidden_ok));
        if (at && at.ambiguous) throw new Error("more than one item matches that");
        if (!at) throw new Error("could not find " + (step.text || step.sel) + (match ? " for that item" : ""));
        return at;
      };
      if (["click", "click_text", "click_in_row"].includes(step.do)) {
        const at = await find();
        clicked.push(at.text);
        await clickAt(page, at);
      } else if (step.do === "check_in_row") {
        const at = await find();
        const want = step.arg ? ["true", "yes", "on", "1"].includes(String(argValue(recipe, args, step.arg)).toLowerCase()) : true;
        if (at.checked !== want) { clicked.push(at.text); await clickAt(page, at); }
      } else if (step.do === "fill" || step.do === "fill_in_row") {
        await find();
        const how = await iso(page, call(pageSetValue, val));
        if (how === "type") await send("Input.insertText", { text: String(val).slice(0, 2000) }, page.session);
        await iso(page, call(pageChanged));
      } else if (step.do === "choose" || step.do === "choose_in_row") {
        await find();
        if (!(await iso(page, call(pageChoose, val)))) throw new Error("that is not one of the choices");
      } else if (step.do === "key") {
        await key(page, step.key);
      } else if (step.do === "wait") {
        await sleep(Math.min(Math.max(+step.ms || 200, 0), 2000));
      } else if (step.do === "wait_for") {
        let ok = false;
        for (let i = 0; i < 40 && !ok; i++) { const r = await iso(page, call(pageCount, step.sel)); ok = !!(r && r.count); if (!ok) await sleep(150); }
        if (!ok) throw new Error("the app did not show what was expected");
      } else if (step.do === "accept_next_dialog") {
        page.autoDialog = true;
      } else if (step.do === "set_files") {
        throw new Error("giving an app files is not in this build yet");
      } else if (step.do === "read_rows") {
        results[step.as || "rows"] = await iso(page, call(pageReadRows, step.sel, step.cells || "td", Math.min(+step.max || 50, 200)));
      } else if (step.do === "read_storage") {
        const raw = await iso(page, call(pageGetItem, step.key));
        let v = null;
        try { v = pick(raw ? JSON.parse(raw) : null, step.path || ""); } catch { v = null; }
        if (Array.isArray(v) && Array.isArray(step.fields)) {
          v = v.slice(0, Math.min(+step.max || 100, 200)).map((x) => Object.fromEntries(step.fields.map((f) => [f, x && typeof x === "object" ? x[f] : undefined])));
        }
        results[step.as || step.key] = v;
      }
      await sleep(Math.min(Math.max(step.after_ms ?? 120, 0), 2000));
    }
    await sleep(250);
  } finally {
    page.autoDialog = false;
  }
  // the change is saved to the bot's copy at once, by this process
  await saveCopy(page).catch(() => {});
  const dialogs = page.dialogs.slice(before).map((d) => ({ type: d.type, message: d.message.slice(0, 160), answered: d.answered }));
  return { results, dialogs, clicked };
}

// ---------------------------------------------------------------------------
// verbs
// ---------------------------------------------------------------------------
async function handle(cmd) {
  const v = cmd.verb;
  if (v === "ping") return { product: cdp.product, open: [...pages.keys()] };
  if (v === "quit") { await quit(); return {}; }
  const label = String(cmd.label || "");
  if (!LABEL_RE.test(label)) throw new Error("bad app label");
  if (v === "open") { const p = await openPage(label); if (cmd.fresh) await gotoApp(p); return { opened: label }; }
  if (v === "close") { await closePage(label); return { closed: label }; }
  if (v === "blocked") {       // never opens the app: the record outlives a closed tab
    const rec = walled.get(label) || { n: 0, origins: [], left: [] };
    return { blocked: rec.n, origins: [...rec.origins], left: [...rec.left] };
  }
  if (v === "clear") {
    const p = pages.get(label);
    if (p) await iso(p, call(pageClearAll)).catch(() => {});
    await clearOrigin(originOf(label));
    return { cleared: label };
  }
  const page = await openPage(label);
  page.lastUsed = Date.now();
  if (v === "snapshot") return await iso(page, call(pageSnapshot, Math.min(Math.max(+cmd.max || 300, 10), 800)));
  if (v === "click") {
    if (!/^e\d{1,5}$/.test(String(cmd.ref || ""))) throw new Error("bad ref");
    const at = await iso(page, call(pageLocate, cmd.ref));
    if (!at) throw new Error("that control is not on screen any more");
    page.autoDialog = !!cmd.accept_dialog;
    const before = page.dialogs.length;
    await clickAt(page, at);
    await sleep(Math.min(Math.max(+cmd.wait || 400, 0), 3000));
    page.autoDialog = false;
    await saveCopy(page).catch(() => {});
    return { clicked: at.text, dialogs: page.dialogs.slice(before) };
  }
  if (v === "storage") {
    const ls = await iso(page, call(pageStorage));
    let idb = [];
    try {
      const r = await send("IndexedDB.requestDatabaseNames", { securityOrigin: page.origin }, page.session);
      idb = r.databaseNames.slice(0, 20);
    } catch { idb = []; }
    return { localStorage: ls, indexedDB: idb };
  }
  if (v === "count") return await iso(page, call(pageCount, String(cmd.sel || "")));
  if (v === "row_controls") {
    // compiling only: a check copy's address, never an installed app's
    if (!label.startsWith("chk-")) throw new Error("rows are read on a check copy only");
    return { info: await iso(page, call(pageRowControls, String(cmd.text || "").slice(0, 200), 12)) };
  }
  if (v === "reload") { await gotoApp(page); return { reloaded: label }; }
  if (v === "run") return await runRecipe(page, cmd.recipe, cmd.args || {});
  throw new Error("unknown verb");
}

async function quit() {
  for (const label of [...pages.keys()]) await closePage(label).catch(() => {});
  if (cdp) {
    // wait for the browser to be gone, so the next start finds its profile free
    const gone = new Promise((r) => { if (cdp.proc.exitCode !== null) r(); else cdp.proc.once("exit", r); });
    await cdp.close();
    await Promise.race([gone, sleep(8000)]);
  }
  setTimeout(() => process.exit(0), 50);
}

// ---------------------------------------------------------------------------
// the control channel
// ---------------------------------------------------------------------------
const rl = createInterface({ input: process.stdin, crlfDelay: Infinity });
let queue = Promise.resolve();
rl.on("line", (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (!CFG) {
    CFG = msg;
    queue = queue.then(async () => {
      try {
        if (!/^\d+$/.test(String(CFG.port)) || typeof CFG.secret !== "string" || !Array.isArray(CFG.labels)) throw new Error("bad setup");
        cdp = await launch({ browser: CFG.browser, profile: CFG.profile, flags: CFG.flags, env: process.env });
        cdp.on(onEvent);
        await send("Target.setDiscoverTargets", { discover: true });
        await send("Target.setAutoAttach", { autoAttach: true, waitForDebuggerOnStart: true, flatten: true });
        await send("Browser.setDownloadBehavior", { behavior: "deny" });
        // apps removed while no runner was running: forget what they kept here
        for (const gone of (Array.isArray(CFG.forget) ? CFG.forget : []).slice(0, 500)) {
          if (LABEL_RE.test(String(gone)) && !CFG.labels.includes(gone)) await clearOrigin(originOf(gone));
        }
        out({ event: "ready", product: cdp.product });
      } catch (e) {
        out({ event: "failed", error: String(e.message || e).slice(0, 200) });
        setTimeout(() => process.exit(1), 50);
      }
    });
    return;
  }
  const id = msg.id;
  queue = queue.then(async () => {
    const t0 = Date.now();
    try {
      const res = await handle(msg);
      out({ id, ok: true, ms: Date.now() - t0, ...res });
    } catch (e) {
      out({ id, ok: false, error: String(e.message || e).slice(0, 300), ms: Date.now() - t0 });
    }
  });
});
rl.on("close", () => { quit().catch(() => process.exit(0)); });
