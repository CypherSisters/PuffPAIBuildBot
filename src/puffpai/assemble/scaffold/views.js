/*
 * The views of an assembled app (spec `assembled_app/v1`).
 *
 * Every view is generic: it reads rows (plain objects) and the view's entry
 * in /spec.json, never anything about one service. Each render function
 * takes (view, ctx) and returns one DOM element; ctx carries the document,
 * the spec, the setting values and the calls to this app's own server:
 *
 *   ctx.doc            the document to build with
 *   ctx.spec           the parsed /spec.json
 *   ctx.settings       { key: value } (a place is { name, lat, lon })
 *   ctx.call(id, input)       -> Promise<{ rows: [...] } | { error, fix? }>
 *   ctx.saveSetting(key, val) -> Promise<{ ok, value?, message? } | { error, fix? }>
 *   ctx.postFix(fix)          -> Promise<{ ok?, message?, error? }>
 *   ctx.askDashboard(fix)     -> true when the dashboard page around it was asked
 *   ctx.loadHls()             -> Promise<Hls constructor | null>
 *   ctx.openSettings(key?)    switch to the settings view
 *
 * All words from the data are set as text, never as HTML. Links and media
 * addresses must be http(s) or a path on this app's own server.
 *
 * Plain JavaScript, no build step: the browser loads it as a script and
 * tests/test_assemble_scaffold.js loads it in node with a small DOM.
 *
 * puff puff pass
 */
(function (root) {
  "use strict";

  var SVG = "http://www.w3.org/2000/svg";

  // ── small helpers ─────────────────────────────────────────────────

  function h(ctx, tag, props) {
    var el = ctx.doc.createElement(tag);
    props = props || {};
    if (props.cls) el.className = props.cls;
    if (props.text !== undefined && props.text !== null) el.textContent = String(props.text);
    if (props.attrs) for (var k in props.attrs) if (props.attrs[k] !== undefined && props.attrs[k] !== null) el.setAttribute(k, String(props.attrs[k]));
    if (props.on) for (var e in props.on) el.addEventListener(e, props.on[e]);
    for (var i = 3; i < arguments.length; i++) add(ctx, el, arguments[i]);
    return el;
  }

  function s(ctx, tag, attrs) {
    var el = ctx.doc.createElementNS(SVG, tag);
    for (var k in attrs || {}) el.setAttribute(k, String(attrs[k]));
    return el;
  }

  /** Appends each child after `el` (nulls skipped, arrays flattened, text as text). */
  function add(ctx, el) {
    for (var i = 2; i < arguments.length; i++) {
      var child = arguments[i];
      if (child === undefined || child === null || child === false) continue;
      if (Array.isArray(child)) { for (var j = 0; j < child.length; j++) add(ctx, el, child[j]); continue; }
      if (typeof child === "string" || typeof child === "number") child = ctx.doc.createTextNode(String(child));
      el.appendChild(child);
    }
  }

  /** A picture that, when it will not load, becomes a quiet tile (never
   *  its alt text spilling out). */
  function picture(ctx, src, alt) {
    var img = h(ctx, "img", { attrs: { src: src, alt: alt || "", loading: "lazy", decoding: "async" } });
    img.addEventListener("error", function () {
      var tile = h(ctx, "div", { cls: "pic-missing", text: "No picture", attrs: { role: "img", "aria-label": alt || "No picture" } });
      if (img.parentNode) img.parentNode.replaceChild(tile, img);
    });
    return img;
  }

  function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); }

  /** A link or media address we will put on the page, or "". */
  function safeUrl(u) {
    if (typeof u !== "string") return "";
    u = u.trim();
    if (/^https?:\/\/[^\s]+$/i.test(u)) return u;
    if (/^\/(?!\/)[^\s]*$/.test(u)) return u;
    return "";
  }

  var API_PARTS = { api: 1, apis: 1, rest: 1, graphql: 1, ws: 1, json: 1, xml: 1, rpc: 1 };

  /** Does an outside address look like data for programs (an API record),
   *  not a page a person reads? The backend's rule (feeds.api_address). */
  function apiAddress(u) {
    var m = /^https?:\/\/([^\/?#]+)([^?#]*)(\?[^#]*)?/i.exec(u);
    if (!m) return true;
    var host = m[1].toLowerCase().replace(/:\d+$/, "");
    if (/(?:^|[.-])api(?:[.-]|$)/.test(host.split(".")[0]) || host.indexOf("api.") === 0) return true;
    var bits = m[2].split("/").filter(Boolean).map(function (b) { return b.toLowerCase(); });
    for (var i = 0; i < bits.length; i++) {
      if (API_PARTS[bits[i]] || /^v\d+(?:\.\d+)*$|^\d+(?:\.\d+)+$/.test(bits[i])) return true;
    }
    if (bits.length && /\.(?:json|xml|rss|atom|csv)$/.test(bits[bits.length - 1])) return true;
    return /(?:^|[?&])(?:format|fmt|output|f)=(?:json|xml)(?:&|$)/i.test(m[3] || "");
  }

  /** The page an item opens: its human page, or "" (never raw data). */
  function pageUrl(u) {
    u = safeUrl(u);
    if (!u || !/^https?:/i.test(u)) return "";
    return apiAddress(u) ? "" : u;
  }

  /** Outside media through this app's own media path when the spec names one. */
  function mediaUrl(ctx, u) {
    u = safeUrl(u);
    if (!u) return "";
    var proxy = ctx.spec && ctx.spec.media_proxy;
    if (proxy && /^https?:/i.test(u) && /^\/(?!\/)/.test(proxy)) return proxy + (proxy.indexOf("?") < 0 ? "?" : "&") + "url=" + encodeURIComponent(u);
    return u;
  }

  function get(row, path) {
    if (!row || path === undefined || path === null) return undefined;
    var cur = row;
    String(path).split(".").forEach(function (p) { cur = cur === undefined || cur === null ? undefined : cur[p]; });
    return cur;
  }

  var ALIASES = {
    title: ["title", "name", "caption", "headline", "label"],
    text: ["text", "caption", "description", "summary", "detail", "body"],
    image: ["image", "image_url", "img", "picture", "photo", "thumbnail", "thumb", "url"],
    thumb: ["thumb", "thumbnail", "image", "image_url"],
    when: ["when", "date", "time", "datetime", "timestamp", "published", "updated", "period"],
    link: ["link", "source", "source_url", "page", "href", "url"],
    lat: ["lat", "latitude"],
    lon: ["lon", "lng", "long", "longitude"],
    stream: ["stream", "stream_url", "hls", "src", "url", "link"],
    temp: ["temp", "temperature", "temp2m", "t"],
    temp_min: ["temp_min", "min", "low", "tmin"],
    temp_max: ["temp_max", "max", "high", "tmax"],
    sky: ["icon", "weather", "sky", "condition", "conditions", "summary", "prec_type", "cloud_words"],
  };

  /** The row's value for a field the view reads, view.fields first. */
  function field(view, row, name) {
    var map = (view && view.fields) || {};
    if (map[name]) return get(row, map[name]);
    var names = ALIASES[name] || [name];
    for (var i = 0; i < names.length; i++) {
      var v = get(row, names[i]);
      if (v !== undefined && v !== null && v !== "") return v;
    }
    return undefined;
  }

  function str(v) {
    if (v === undefined || v === null) return "";
    if (typeof v === "object") return "";
    return String(v);
  }

  function num(v) {
    if (typeof v === "number" && isFinite(v)) return v;
    if (typeof v === "string" && v.trim() !== "" && isFinite(Number(v))) return Number(v);
    return null;
  }

  function looksImage(u) { return /\.(png|jpe?g|gif|webp|avif|svg)(\?|#|$)/i.test(String(u || "")); }

  /** A date or time in her own words, or the value as it came. */
  function when(v) {
    if (v === undefined || v === null || v === "") return "";
    var d = null;
    var n = num(v);
    if (n !== null && n > 1e9) d = new Date(n > 1e11 ? n : n * 1000);
    else if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v)) d = new Date(v.replace(" ", "T"));
    if (!d || isNaN(d.getTime())) return str(v);
    var hasTime = !(typeof v === "string" && /^\d{4}-\d{2}-\d{2}$/.test(v));
    try {
      return hasTime
        ? d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })
        : d.toLocaleDateString(undefined, { dateStyle: "medium" });
    } catch (e) {
      return d.toISOString();
    }
  }

  // ── data loading, errors, fixes ───────────────────────────────────

  var FIX_WORDS = {
    switch_on: "Switch it on",
    setting: "Open settings",
    key: "Put the key in your vault",
    install: "Install what's missing",
  };

  /** A fix address we will post to: this app's own paths, or this computer. */
  function fixUrl(u) {
    if (typeof u !== "string") return "";
    if (/^\/(?!\/)[^\s]*$/.test(u)) return u;
    if (/^https?:\/\/(127\.0\.0\.1|localhost)(:\d{2,5})?\/[^\s]*$/i.test(u)) return u;
    return "";
  }

  /** The plain reason and, when the answer carries one, a fix button. */
  function problem(ctx, reply, retry) {
    var box = h(ctx, "div", { cls: "problem", attrs: { role: "alert" } });
    add(ctx, box, h(ctx, "p", { text: str(reply && reply.error) || "That did not work." }));
    var fix = reply && reply.fix;
    if (fix && typeof fix === "object") {
      var said = h(ctx, "p", { cls: "said" });
      var label = str(fix.label) || FIX_WORDS[fix.kind] || "Fix it";
      var btn = h(ctx, "button", {
        cls: "btn accent",
        text: label,
        attrs: { type: "button", "data-fix": str(fix.kind) },
        on: {
          click: function () {
            if (fix.kind === "setting" && !fixUrl(fix.url)) { ctx.openSettings(fix.key); return; }
            if (!fixUrl(fix.url) && (fix.kind === "switch_on" || fix.kind === "key")) {
              // Switching on and keys are hers to do on her dashboard: this page
              // asks the dashboard page around it to show the card.
              said.textContent = ctx.askDashboard && ctx.askDashboard(fix)
                ? "Look just above this app on your dashboard page."
                : "Do this from this app's page on your dashboard.";
              return;
            }
            if (!fixUrl(fix.url)) { said.textContent = "This fix has no address on this computer."; return; }
            btn.disabled = true;
            said.textContent = "Working on it...";
            Promise.resolve(ctx.postFix(fix)).then(function (r) {
              btn.disabled = false;
              r = r || {};
              said.textContent = str(r.message) || str(r.error) || (r.ok ? "Done." : "That did not work.");
              if (r.ok && retry) retry();
            });
          },
        },
      });
      add(ctx, box, h(ctx, "div", { cls: "row" }, btn), said);
    }
    if (retry) add(ctx, box, h(ctx, "button", { cls: "btn", text: "Try again", attrs: { type: "button" }, on: { click: retry } }));
    return box;
  }

  function actionsOf(ctx) { return (ctx.spec && Array.isArray(ctx.spec.actions)) ? ctx.spec.actions : []; }

  /** An action's inputs as [{ name, type, choices, required, help }]. */
  function inputsOf(action) {
    var input = (action && action.input) || {};
    var props = input.properties && typeof input.properties === "object" ? input.properties : (input.type === "object" ? {} : input);
    var required = Array.isArray(input.required) ? input.required : null;
    return Object.keys(props).map(function (name) {
      var p = props[name];
      var t = typeof p === "string" ? p : (p && (Array.isArray(p.type) ? p.type[0] : p.type)) || "string";
      var choices = Array.isArray(p) ? p : (p && Array.isArray(p.enum) ? p.enum : null);
      return {
        name: name,
        type: choices ? "choice" : t,
        choices: choices,
        required: required ? required.indexOf(name) >= 0 : false,
        help: p && typeof p === "object" ? str(p.description || p.title) : "",
        default: p && typeof p === "object" ? p.default : undefined,
      };
    });
  }

  /** Which action fills a view: its own, else one for its source with no
   *  required input (a list before a search; a search with no words gives
   *  the newest items), else the source id itself. */
  function actionFor(ctx, view) {
    if (view.action) return view.action;
    var acts = actionsOf(ctx).filter(function (a) { return a.source === view.source; });
    for (var i = 0; i < acts.length; i++) {
      var ins = inputsOf(acts[i]);
      if (!ins.some(function (x) { return x.required; }) && !isSearch(acts[i])) return acts[i].id;
    }
    return acts.length ? acts[0].id : view.source;
  }

  function isSearch(action) {
    if (!action) return false;
    if (/search|find|look/i.test(String(action.id || ""))) return true;
    var ins = inputsOf(action);
    return ins.some(function (x) { return /^(q|query|search|term|text|keyword)s?$/i.test(x.name); });
  }

  /** The search action for a view, or null (then the search box filters). */
  function searchFor(ctx, view) {
    if (view.search && typeof view.search === "string") {
      var named = actionsOf(ctx).filter(function (a) { return a.id === view.search; })[0];
      if (named) return named;
    }
    return actionsOf(ctx).filter(function (a) { return a.source === view.source && isSearch(a); })[0] || null;
  }

  /** Loads rows into `body` with fill(rows); shows waiting, nothing and errors. */
  function loader(ctx, body, fetchRows, fill) {
    function run() {
      clear(body);
      add(ctx, body, h(ctx, "p", { cls: "muted", text: "Getting it..." }));
      return Promise.resolve(fetchRows()).then(function (reply) {
        clear(body);
        if (!reply || reply.error || !Array.isArray(reply.rows)) {
          add(ctx, body, problem(ctx, reply && reply.error ? reply : { error: "This app's server sent nothing it could show." }, run));
          return;
        }
        if (!reply.rows.length) { add(ctx, body, h(ctx, "p", { cls: "muted", text: str(reply.note) || "Nothing came back yet." })); return; }
        fill(reply.rows);
        if (reply.note) add(ctx, body, h(ctx, "p", { cls: "muted small", text: str(reply.note) }));
      }, function () {
        clear(body);
        add(ctx, body, problem(ctx, { error: "This app's server is not answering." }, run));
      }).catch(function (err) {
        if (typeof console !== "undefined") console.error(err);
        clear(body);
        add(ctx, body, problem(ctx, { error: "This part could not be shown from what came back." }, run));
      });
    }
    return run;
  }

  function shell(ctx, view, kind) {
    var wrap = h(ctx, "section", { cls: "view view-" + kind, attrs: { "data-view": kind } });
    if (view.title) add(ctx, wrap, h(ctx, "h2", { cls: "view-title", text: view.title }));
    if (view.note) add(ctx, wrap, h(ctx, "p", { cls: "muted small", text: view.note }));
    return wrap;
  }

  function sourceLink(ctx, u, words) {
    var url = pageUrl(u);
    if (!url) return null;
    return h(ctx, "a", { cls: "src", text: words || "Where it came from", attrs: { href: url, target: "_blank", rel: "noreferrer noopener" } });
  }

  // ── gallery ───────────────────────────────────────────────────────

  function gallery(view, ctx) {
    var wrap = shell(ctx, view, "gallery");
    var search = view.search ? searchFor(ctx, view) : null;
    var all = [];
    var shown = [];
    var grid = h(ctx, "div", { cls: "grid" });
    var body = h(ctx, "div", { cls: "body" }, grid);
    var query = "";

    function imageOf(row) {
      var u = field(view, row, "image");
      if (!view.fields || !view.fields.image) { if (u && !looksImage(u) && field(view, row, "thumb")) u = field(view, row, "thumb"); }
      return mediaUrl(ctx, str(u));
    }

    function paint(rows) {
      shown = rows;
      clear(grid);
      if (!rows.length) { add(ctx, grid, h(ctx, "p", { cls: "muted", text: "Nothing matches that." })); return; }
      rows.forEach(function (row, i) {
        var img = imageOf(row);
        var cap = str(field(view, row, "title")) || str(field(view, row, "text"));
        var tile = h(ctx, "button", {
          cls: "tile",
          attrs: { type: "button", "aria-label": cap || "Picture " + (i + 1) },
          on: { click: function () { if (view.lightbox !== false) openBox(i); } },
        });
        if (img) add(ctx, tile, picture(ctx, img, cap));
        else add(ctx, tile, h(ctx, "span", { cls: "noimg", text: "No picture" }));
        var w = when(field(view, row, "when"));
        add(ctx, tile, h(ctx, "span", { cls: "cap" }, h(ctx, "span", { cls: "cap-t", text: cap }), w ? h(ctx, "span", { cls: "cap-w", text: w }) : null));
        add(ctx, grid, tile);
      });
    }

    function filter() {
      var q = query.trim().toLowerCase();
      if (!q) return paint(all);
      paint(all.filter(function (row) {
        return Object.keys(row).some(function (k) { return str(row[k]).toLowerCase().indexOf(q) >= 0; });
      }));
    }

    var box = null;
    function openBox(i) {
      if (box) box.remove();
      var at = i;
      var big = h(ctx, "img", { cls: "lb-img", attrs: { alt: "" } });
      var title = h(ctx, "p", { cls: "lb-title" });
      var meta = h(ctx, "p", { cls: "lb-meta" });
      var count = h(ctx, "span", { cls: "lb-count" });
      var links = h(ctx, "div", { cls: "lb-links" });
      function show() {
        var row = shown[at];
        var img = imageOf(row);
        if (img) big.setAttribute("src", img); else big.removeAttribute("src");
        var t = str(field(view, row, "title"));
        var x = str(field(view, row, "text"));
        big.setAttribute("alt", t || x);
        title.textContent = t || x;
        meta.textContent = [when(field(view, row, "when")), t && x && x !== t ? x : ""].filter(Boolean).join(" · ");
        count.textContent = (at + 1) + " of " + shown.length;
        clear(links);
        var src = field(view, row, "link");
        add(ctx, links, sourceLink(ctx, str(src) && str(src) !== str(field(view, row, "image")) ? str(src) : "", "Where it came from"));
        if (img) add(ctx, links, h(ctx, "a", { cls: "src", text: "Open the full picture", attrs: { href: img, target: "_blank", rel: "noreferrer noopener" } }));
      }
      function step(d) { at = (at + d + shown.length) % shown.length; show(); }
      function close() { if (box) { box.remove(); box = null; } ctx.doc.removeEventListener("keydown", keys); }
      function keys(e) {
        if (e.key === "Escape") close();
        else if (e.key === "ArrowRight") step(1);
        else if (e.key === "ArrowLeft") step(-1);
      }
      box = h(ctx, "div", {
        cls: "lightbox",
        attrs: { role: "dialog", "aria-modal": "true", "aria-label": "Picture" },
        on: { click: function (e) { if (e.target === box) close(); } },
      },
        h(ctx, "div", { cls: "lb-bar" },
          count,
          h(ctx, "span", { cls: "grow" }),
          h(ctx, "button", { cls: "btn", text: "Previous", attrs: { type: "button", "data-lb": "prev" }, on: { click: function () { step(-1); } } }),
          h(ctx, "button", { cls: "btn", text: "Next", attrs: { type: "button", "data-lb": "next" }, on: { click: function () { step(1); } } }),
          h(ctx, "button", { cls: "btn", text: "Close", attrs: { type: "button", "data-lb": "close" }, on: { click: close } })),
        h(ctx, "div", { cls: "lb-stage" }, big),
        h(ctx, "div", { cls: "lb-text" }, title, meta, links));
      ctx.doc.addEventListener("keydown", keys);
      show();
      add(ctx, wrap, box);
    }

    var input = null;
    if (view.search) {
      input = h(ctx, "input", { cls: "input", attrs: { type: "search", placeholder: search ? "Search" : "Filter these", "aria-label": "Search" } });
      var form = h(ctx, "form", { cls: "toolbar", on: { submit: function (e) { e.preventDefault(); query = input.value; if (search) run(); else filter(); } } },
        input, h(ctx, "button", { cls: "btn", text: search ? "Search" : "Filter", attrs: { type: "submit" } }));
      if (!search) input.addEventListener("input", function () { query = input.value; filter(); });
      add(ctx, wrap, form);
    }
    add(ctx, wrap, body);

    var run = loader(ctx, body, function () {
      if (search && query.trim()) {
        var key = (inputsOf(search).filter(function (x) { return x.type === "string"; })[0] || { name: "q" }).name;
        var inp = {}; inp[key] = query.trim();
        return ctx.call(search.id, inp);
      }
      return ctx.call(actionFor(ctx, view), {});
    }, function (rows) { all = rows; add(ctx, body, grid); paint(rows); });
    wrap._reload = run;
    wrap._open = openBox;
    run();
    return wrap;
  }

  // ── table ─────────────────────────────────────────────────────────

  function columnsOf(view, rows) {
    if (Array.isArray(view.columns) && view.columns.length) return view.columns.map(String);
    var seen = [];
    rows.slice(0, 25).forEach(function (r) {
      Object.keys(r || {}).forEach(function (k) { if (seen.indexOf(k) < 0 && (r[k] === null || typeof r[k] !== "object")) seen.push(k); });
    });
    return seen.slice(0, 12);
  }

  function words(k) { return String(k).replace(/[_-]+/g, " ").replace(/^./, function (c) { return c.toUpperCase(); }); }

  /** A column's label: the source's own words (view.labels), else its name. */
  function labelOf(view, k) {
    var l = view && view.labels && view.labels[k];
    return typeof l === "string" && l ? l : words(k);
  }

  /** What the values mean ("Cloud cover: 1 clear to 9 overcast"), or null. */
  function legend(ctx, view) {
    var m = (view && view.meanings) || {};
    var keys = Object.keys(m).filter(function (k) { return typeof m[k] === "string" && m[k]; });
    if (!keys.length) return null;
    return h(ctx, "ul", { cls: "meanings muted small" },
      keys.map(function (k) { return h(ctx, "li", { text: labelOf(view, k) + ": " + m[k] }); }));
  }

  function cell(ctx, v) {
    var u = typeof v === "string" ? safeUrl(v) : "";
    if (u && /^https?:/i.test(u)) {
      if (looksImage(u)) return h(ctx, "a", { text: "Picture", attrs: { href: u, target: "_blank", rel: "noreferrer noopener" } });
      // A row's own API record is data for programs: no link, no raw address.
      return pageUrl(u) ? h(ctx, "a", { text: "Open", attrs: { href: u, target: "_blank", rel: "noreferrer noopener" } }) : "";
    }
    if (typeof v === "boolean") return v ? "Yes" : "No";
    return str(v);
  }

  function table(view, ctx) {
    var wrap = shell(ctx, view, "table");
    var body = h(ctx, "div", { cls: "body" });
    var rowsAll = [];
    var sortBy = null;
    var desc = false;
    var limit = 100;
    var input = h(ctx, "input", { cls: "input", attrs: { type: "search", placeholder: "Filter", "aria-label": "Filter" } });
    input.addEventListener("input", function () { limit = 100; paint(); });
    add(ctx, wrap, h(ctx, "div", { cls: "toolbar" }, input), body);

    function paint() {
      clear(body);
      var cols = columnsOf(view, rowsAll);
      var q = input.value.trim().toLowerCase();
      var rows = rowsAll.filter(function (r) { return !q || cols.some(function (c) { return str(get(r, c)).toLowerCase().indexOf(q) >= 0; }); });
      if (sortBy) {
        rows = rows.slice().sort(function (a, b) {
          var x = get(a, sortBy), y = get(b, sortBy);
          var nx = num(x), ny = num(y);
          var c = nx !== null && ny !== null ? nx - ny : str(x).localeCompare(str(y), undefined, { numeric: true });
          return desc ? -c : c;
        });
      }
      var thead = h(ctx, "tr");
      cols.forEach(function (c) {
        var mark = sortBy === c ? (desc ? " ▼" : " ▲") : "";
        add(ctx, thead, h(ctx, "th", { attrs: { "aria-sort": sortBy === c ? (desc ? "descending" : "ascending") : "none" } },
          h(ctx, "button", { cls: "th", text: labelOf(view, c) + mark, attrs: { type: "button", "data-col": c }, on: { click: function () { if (sortBy === c) desc = !desc; else { sortBy = c; desc = false; } paint(); } } })));
      });
      var tbody = h(ctx, "tbody");
      rows.slice(0, limit).forEach(function (r) {
        var tr = h(ctx, "tr");
        cols.forEach(function (c) { add(ctx, tr, h(ctx, "td", {}, cell(ctx, get(r, c)))); });
        add(ctx, tbody, tr);
      });
      add(ctx, body, h(ctx, "div", { cls: "scroll" }, h(ctx, "table", { cls: "tbl" }, h(ctx, "thead", {}, thead), tbody)));
      add(ctx, body, h(ctx, "p", { cls: "muted small", text: rows.length + (rows.length === 1 ? " row" : " rows") + (rows.length > limit ? ", showing " + limit : "") }));
      var meant = legend(ctx, view);
      if (meant) add(ctx, body, meant);
      if (rows.length > limit) add(ctx, body, h(ctx, "button", { cls: "btn", text: "Show more", attrs: { type: "button" }, on: { click: function () { limit += 100; paint(); } } }));
    }
    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) { rowsAll = rows; paint(); });
    wrap._reload = run;
    run();
    return wrap;
  }

  // ── forecast ──────────────────────────────────────────────────────

  var SKY = [
    [/thunder|storm|\bts|tsrain|lightning/i, "⛈", "Storms"],
    [/snow|sleet|ice|flurr/i, "❄", "Snow"],
    [/rain|shower|drizzle|wet/i, "🌧", "Rain"],
    [/fog|mist|haze|humid/i, "🌫", "Fog"],
    [/pcloud|partly|few|scattered|mostly ?sunny/i, "⛅", "Partly cloudy"],
    [/cloud|overcast|grey|gray|mcloud/i, "☁", "Cloudy"],
    [/wind|gust/i, "💨", "Windy"],
    [/clear|sun|fair|fine/i, "☀", "Clear"],
  ];

  /** An icon and plain words for a period, from whatever its data says. */
  function skyOf(view, row) {
    var t = str(field(view, row, "sky"));
    for (var i = 0; i < SKY.length; i++) if (SKY[i][0].test(t)) return { icon: SKY[i][1], words: SKY[i][2] };
    var rain = num(get(row, "rain") !== undefined ? get(row, "rain") : get(row, "precip"));
    var cloud = num(get(row, "cloud") !== undefined ? get(row, "cloud") : get(row, "cloudcover"));
    if (rain !== null && rain > 0) return { icon: "🌧", words: "Rain" };
    if (cloud !== null) {
      var pct = cloud <= 9 ? cloud * 11 : cloud;
      if (pct >= 70) return { icon: "☁", words: "Cloudy" };
      if (pct >= 30) return { icon: "⛅", words: "Partly cloudy" };
      return { icon: "☀", words: "Clear" };
    }
    return { icon: "·", words: t };
  }

  /** "Tonight 9 PM", "Today 3 PM", "Sun 3 AM": a moment in her own time,
   *  short, for a forecast's column head. Anything that is not a moment is
   *  shown as it came. */
  function shortWhen(v) {
    if (typeof v !== "string" || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/.test(v)) return when(v);
    var d = new Date(v);
    if (isNaN(d.getTime())) return str(v);
    var hour;
    try { hour = d.toLocaleTimeString(undefined, { hour: "numeric" }); } catch (e) { hour = d.getHours() + ":00"; }
    var now = new Date();
    var day = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    var diff = Math.round((day - today) / 86400000);
    if (diff === 0) return (d.getHours() >= 18 ? "Tonight " : "Today ") + hour;
    if (diff === 1 && d.getHours() < 5) return "Tonight " + hour;
    var name;
    try { name = d.toLocaleDateString(undefined, { weekday: "short" }); } catch (e) { name = ""; }
    return (name ? name + " " : "") + hour;
  }

  /** "3 (good)": a scale value with the words its source gives for it. */
  function scaleWords(view, key, v) {
    var steps = view && view.value_words && view.value_words[key];
    var n = num(v);
    if (!Array.isArray(steps) || n === null) return "";
    for (var i = 0; i < steps.length; i++) {
      if (Array.isArray(steps[i]) && n <= num(steps[i][0])) return str(steps[i][1]);
    }
    return "";
  }

  var SKIP_FORECAST = ["when", "date", "time", "datetime", "timestamp", "period", "name", "title", "icon", "weather", "sky", "condition", "conditions", "summary", "temp", "temperature", "temp2m", "t", "temp_min", "temp_max", "min", "max", "low", "high", "tmin", "tmax", "units", "unit", "lat", "lon"];

  function forecast(view, ctx) {
    var wrap = shell(ctx, view, "forecast");
    var body = h(ctx, "div", { cls: "body" });
    add(ctx, wrap, body);
    var units = Object.assign({ temp: "°" }, (ctx.spec && ctx.spec.units) || {}, view.units || {});
    function unit(name, row) { return str(get(row, "units." + name)) || units[name] || ""; }
    function fmt(v, name, row) { var n = num(v); var u = unit(name, row); return n === null ? str(v) : (Math.round(n * 10) / 10) + (u ? (u.charAt(0) === "°" ? u : " " + u) : ""); }
    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) {
      var place = placeWords(ctx);
      if (place) add(ctx, body, h(ctx, "p", { cls: "muted small", text: "For " + place }));
      var strip = h(ctx, "div", { cls: "cards" });
      rows.slice(0, view.limit || 24).forEach(function (row) {
        var sky = skyOf(view, row);
        var t = field(view, row, "temp");
        var lo = field(view, row, "temp_min"), hi = field(view, row, "temp_max");
        var temp = t !== undefined ? fmt(t, "temp", row) : [hi !== undefined ? fmt(hi, "temp", row) : "", lo !== undefined ? fmt(lo, "temp", row) : ""].filter(Boolean).join(" / ");
        var extras = h(ctx, "dl", { cls: "extras" });
        Object.keys(row).filter(function (k) { return SKIP_FORECAST.indexOf(k) < 0 && (view.fields ? Object.keys(view.fields).map(function (f) { return view.fields[f]; }).indexOf(k) < 0 : true) && (typeof row[k] === "number" || (typeof row[k] === "string" && row[k].length < 24)); })
          .slice(0, 6).forEach(function (k) {
            var said = scaleWords(view, k, row[k]);
            add(ctx, extras, h(ctx, "dt", { text: labelOf(view, k) }),
              h(ctx, "dd", {}, h(ctx, "span", { cls: "small", text: fmt(row[k], k, row) }), said ? " " + said : ""));
          });
        add(ctx, strip, h(ctx, "article", { cls: "card" },
          h(ctx, "p", { cls: "card-when", text: shortWhen(field(view, row, "when")) || str(field(view, row, "title")) }),
          h(ctx, "p", { cls: "card-icon", text: sky.icon, attrs: { "aria-hidden": "true" } }),
          h(ctx, "p", { cls: "card-temp", text: temp }),
          sky.words ? h(ctx, "p", { cls: "card-sky", text: sky.words }) : null,
          extras));
      });
      add(ctx, body, strip);
      var meant = legend(ctx, view);
      if (meant) add(ctx, body, meant);
    });
    wrap._reload = run;
    run();
    return wrap;
  }

  // ── map ───────────────────────────────────────────────────────────

  function latLon(view, row) {
    var lat = num(field(view, row, "lat")), lon = num(field(view, row, "lon"));
    var c = get(row, "coordinates");
    if ((lat === null || lon === null) && Array.isArray(c) && c.length >= 2) { lon = num(c[0]); lat = num(c[1]); }
    if (lat === null || lon === null || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
    return { lat: lat, lon: lon };
  }

  function map(view, ctx) {
    var wrap = shell(ctx, view, "map");
    var body = h(ctx, "div", { cls: "body" });
    add(ctx, wrap, body);
    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) {
      var pts = rows.map(function (r) { var p = latLon(view, r); return p ? { p: p, row: r } : null; }).filter(Boolean);
      var svg = s(ctx, "svg", { viewBox: "0 0 360 180", class: "world", role: "img", "aria-label": "World map with " + pts.length + " places" });
      add(ctx, svg, s(ctx, "rect", { x: 0, y: 0, width: 360, height: 180, class: "sea" }));
      add(ctx, svg, s(ctx, "path", { d: root.PUFF_WORLD_PATH || "", class: "land" }));
      var info = h(ctx, "p", { cls: "map-info muted", text: pts.length ? "Pick a dot to see what is there." : "None of these have a position." });
      var dots = [];
      pts.forEach(function (pt) {
        var x = pt.p.lon + 180, y = 90 - pt.p.lat;
        var dot = s(ctx, "circle", { cx: x.toFixed(2), cy: y.toFixed(2), r: 1.6, class: "dot", tabindex: 0 });
        var label = str(field(view, pt.row, "title")) || pt.p.lat.toFixed(2) + ", " + pt.p.lon.toFixed(2);
        var t = s(ctx, "title", {}); t.textContent = label; add(ctx, dot, t);
        dot.addEventListener("click", function () {
          dots.forEach(function (d) { d.setAttribute("class", "dot"); });
          dot.setAttribute("class", "dot on");
          info.textContent = [label, when(field(view, pt.row, "when")), pt.p.lat.toFixed(3) + ", " + pt.p.lon.toFixed(3)].filter(Boolean).join(" · ");
        });
        dots.push(dot);
        add(ctx, svg, dot);
      });
      // Fit the dots when they sit close together; "Whole world" goes back.
      var fit = "0 0 360 180";
      if (pts.length) {
        var xs = pts.map(function (q) { return q.p.lon + 180; }), ys = pts.map(function (q) { return 90 - q.p.lat; });
        var x0 = Math.min.apply(null, xs), x1 = Math.max.apply(null, xs), y0 = Math.min.apply(null, ys), y1 = Math.max.apply(null, ys);
        var w = Math.max(x1 - x0, 20) * 1.4, hh = Math.max(w / 2, (y1 - y0) * 1.4);
        w = hh * 2;
        if (w < 300) fit = [((x0 + x1) / 2 - w / 2).toFixed(1), ((y0 + y1) / 2 - hh / 2).toFixed(1), w.toFixed(1), hh.toFixed(1)].join(" ");
      }
      svg.setAttribute("viewBox", fit);
      add(ctx, body, h(ctx, "div", { cls: "mapbox" }, svg), info,
        fit !== "0 0 360 180" ? h(ctx, "div", { cls: "row" },
          h(ctx, "button", { cls: "btn", text: "Whole world", attrs: { type: "button" }, on: { click: function () { svg.setAttribute("viewBox", "0 0 360 180"); } } }),
          h(ctx, "button", { cls: "btn", text: "Close in", attrs: { type: "button" }, on: { click: function () { svg.setAttribute("viewBox", fit); } } })) : null,
        h(ctx, "p", { cls: "muted small", text: "A plain world outline drawn on this computer. Street maps come later." }));
    });
    wrap._reload = run;
    run();
    return wrap;
  }

  // ── player ────────────────────────────────────────────────────────

  /** A YouTube video or channel live stream to embed, or "". */
  function youTube(u) {
    u = String(u || "");
    var m = /^https?:\/\/(?:www\.|m\.)?(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|live\/|shorts\/)|youtu\.be\/)([A-Za-z0-9_-]{11})(?:[?&#/].*)?$/.exec(u);
    if (m) return "https://www.youtube-nocookie.com/embed/" + m[1];
    var c = /^https?:\/\/(?:www\.)?youtube\.com\/embed\/live_stream\?channel=(UC[A-Za-z0-9_-]{22})/.exec(u);
    if (c) return "https://www.youtube-nocookie.com/embed/live_stream?channel=" + c[1];
    return "";
  }

  /** youtube | hls | audio | video: the address first, then the row's kind, then the view's mode. */
  function kindOf(view, row, u) {
    if (youTube(u)) return "youtube";
    if (/\.m3u8(\?|#|$)/i.test(u)) return "hls";
    if (/\.(mp3|aac|ogg|opus|m4a|wav|flac)(\?|#|$)/i.test(u)) return "audio";
    var kinds = ["hls", "audio", "video"];
    var k = str(get(row, "kind") || get(row, "type")).toLowerCase();
    if (kinds.indexOf(k) >= 0) return k;
    k = str(view.mode).toLowerCase();
    return kinds.indexOf(k) >= 0 ? k : "video";
  }

  function player(view, ctx) {
    var wrap = shell(ctx, view, "player");
    var body = h(ctx, "div", { cls: "body" });
    add(ctx, wrap, body);
    var stage = h(ctx, "div", { cls: "stage" });
    var nowLine = h(ctx, "p", { cls: "now" });
    var hls = null;

    function stop() {
      if (hls) { try { hls.destroy(); } catch (e) { /* gone */ } hls = null; }
      clear(stage);
    }

    function play(row) {
      stop();
      var raw = str(field(view, row, "stream"));
      var kind = kindOf(view, row, raw);
      nowLine.textContent = str(field(view, row, "title")) || "Playing";
      if (kind === "youtube") {
        add(ctx, stage, h(ctx, "iframe", { cls: "yt", attrs: {
          src: youTube(raw), title: nowLine.textContent, allow: "autoplay; encrypted-media; picture-in-picture; fullscreen",
          allowfullscreen: "", referrerpolicy: "strict-origin-when-cross-origin", loading: "lazy" } }));
        add(ctx, stage, h(ctx, "p", { cls: "muted small", text: "Your browser talks to YouTube directly for this video." }));
        return;
      }
      var u = kind === "hls" ? safeUrl(raw) : mediaUrl(ctx, raw);
      if (!u) { add(ctx, stage, problem(ctx, { error: "This one has no address it can play." })); return; }
      var el = h(ctx, kind === "audio" ? "audio" : "video", { attrs: { controls: "", autoplay: "", playsinline: "", preload: "metadata" } });
      add(ctx, stage, el);
      var oops = function () { add(ctx, stage, problem(ctx, { error: "It would not play. The stream may be off the air right now." }, function () { play(row); })); };
      el.addEventListener("error", oops);
      if (kind !== "hls") { el.setAttribute("src", u); return; }
      var proxy = ctx.spec && ctx.spec.media_proxy;
      Promise.resolve(ctx.loadHls()).then(function (Hls) {
        if (Hls && Hls.isSupported && Hls.isSupported()) {
          var cfg = {};
          if (proxy && /^https?:/i.test(u)) {
            // Every manifest and piece goes out through this app's media path.
            var via = function (url) { return mediaUrl(ctx, url); };
            cfg.xhrSetup = function (xhr, url) { if (/^https?:/i.test(url)) xhr.open("GET", via(url), true); };
            cfg.fetchSetup = function (context, init) { return new Request(/^https?:/i.test(context.url) ? via(context.url) : context.url, init); };
          }
          hls = new Hls(cfg);
          hls.on(Hls.Events.ERROR, function (_e, data) { if (data && data.fatal) { stop(); oops(); } });
          hls.loadSource(u);
          hls.attachMedia(el);
        } else if (el.canPlayType && el.canPlayType("application/vnd.apple.mpegurl")) {
          el.setAttribute("src", mediaUrl(ctx, u));
        } else {
          oops();
        }
      });
    }

    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) {
      var playable = rows.filter(function (r) { return str(field(view, r, "stream")); });
      add(ctx, body, nowLine, stage);
      if (!playable.length) { add(ctx, stage, h(ctx, "p", { cls: "muted", text: "Nothing here has something to play." })); return; }
      if (playable.length > 1) {
        var list = h(ctx, "div", { cls: "chans" });
        playable.forEach(function (r, i) {
          add(ctx, list, h(ctx, "button", { cls: "chan", text: str(field(view, r, "title")) || "Stream " + (i + 1), attrs: { type: "button" },
            on: { click: function () { play(r); } } }));
        });
        add(ctx, body, list);
      }
      play(playable[0]);
    });
    wrap._reload = run;
    wrap._stop = stop;
    run();
    return wrap;
  }

  // ── chart ─────────────────────────────────────────────────────────

  function chart(view, ctx) {
    var wrap = shell(ctx, view, "chart");
    var body = h(ctx, "div", { cls: "body" });
    add(ctx, wrap, body);
    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) {
      var xKey = view.x || null;
      var ys = view.y ? [].concat(view.y) : null;
      if (!ys) {
        var first = rows[0] || {};
        ys = Object.keys(first).filter(function (k) { return k !== xKey && num(first[k]) !== null && ALIASES.when.indexOf(k) < 0; }).slice(0, 3);
      }
      var data = rows.slice(0, 400);
      var vals = [];
      data.forEach(function (r) { ys.forEach(function (k) { var n = num(get(r, k)); if (n !== null) vals.push(n); }); });
      if (!vals.length) { add(ctx, body, h(ctx, "p", { cls: "muted", text: "There are no numbers here to draw." })); return; }
      var lo = Math.min(0, Math.min.apply(null, vals)), hi = Math.max.apply(null, vals);
      if (hi === lo) hi = lo + 1;
      var W = 600, H = 240, P = 28;
      var svg = s(ctx, "svg", { viewBox: "0 0 " + W + " " + H, class: "chart", role: "img", "aria-label": "Chart of " + ys.map(words).join(", ") });
      add(ctx, svg, s(ctx, "line", { x1: P, y1: H - P, x2: W - 4, y2: H - P, class: "axis" }));
      var top = s(ctx, "text", { x: 2, y: P - 6, class: "tick" }); top.textContent = String(Math.round(hi * 100) / 100); add(ctx, svg, top);
      var bot = s(ctx, "text", { x: 2, y: H - P + 14, class: "tick" }); bot.textContent = String(Math.round(lo * 100) / 100); add(ctx, svg, bot);
      var n = data.length;
      var xAt = function (i) { return P + (n <= 1 ? 0 : (i * (W - P - 8)) / (n - 1)); };
      var yAt = function (v) { return H - P - ((v - lo) * (H - 2 * P)) / (hi - lo); };
      var bar = view.mode === "bar" || view.chart === "bar";
      ys.forEach(function (k, si) {
        if (bar) {
          var bw = Math.max(1, (W - P - 8) / Math.max(n, 1) / ys.length - 1);
          data.forEach(function (r, i) {
            var v = num(get(r, k)); if (v === null) return;
            add(ctx, svg, s(ctx, "rect", { x: (P + i * (W - P - 8) / Math.max(n, 1) + si * bw).toFixed(1), y: yAt(Math.max(v, 0)).toFixed(1), width: bw.toFixed(1), height: Math.abs(yAt(v) - yAt(0)).toFixed(1), class: "s" + si }));
          });
        } else {
          var d = "";
          data.forEach(function (r, i) { var v = num(get(r, k)); if (v === null) return; d += (d ? "L" : "M") + xAt(i).toFixed(1) + "," + yAt(v).toFixed(1); });
          add(ctx, svg, s(ctx, "path", { d: d, class: "line s" + si }));
        }
      });
      var firstX = xKey ? get(data[0], xKey) : field(view, data[0], "when");
      var lastX = xKey ? get(data[n - 1], xKey) : field(view, data[n - 1], "when");
      add(ctx, body, svg, h(ctx, "p", { cls: "legend" },
        ys.map(function (k, si) { return h(ctx, "span", { cls: "key s" + si, text: labelOf(view, k) }); }),
        firstX !== undefined ? h(ctx, "span", { cls: "muted small", text: " " + when(firstX) + (n > 1 ? " to " + when(lastX) : "") }) : null));
    });
    wrap._reload = run;
    run();
    return wrap;
  }

  // ── list ──────────────────────────────────────────────────────────

  function listRows(ctx, view, rows) {
    var ul = h(ctx, "ul", { cls: "list" });
    rows.slice(0, view.limit || 200).forEach(function (r) {
      var title = str(field(view, r, "title"));
      var text = str(field(view, r, "text"));
      var link = pageUrl(str(field(view, r, "link")));
      add(ctx, ul, h(ctx, "li", {},
        link ? h(ctx, "a", { cls: "li-t", text: title || link, attrs: { href: link, target: "_blank", rel: "noreferrer noopener" } }) : h(ctx, "span", { cls: "li-t", text: title || text }),
        text && text !== title ? h(ctx, "span", { cls: "li-x", text: text }) : null,
        field(view, r, "when") !== undefined ? h(ctx, "span", { cls: "li-w", text: when(field(view, r, "when")) }) : null));
    });
    return ul;
  }

  function list(view, ctx) {
    var wrap = shell(ctx, view, "list");
    var body = h(ctx, "div", { cls: "body" });
    add(ctx, wrap, body);
    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) { add(ctx, body, listRows(ctx, view, rows)); });
    wrap._reload = run;
    run();
    return wrap;
  }

  // ── form ──────────────────────────────────────────────────────────

  /** Rows shown the best way for what they hold. */
  function autoRows(ctx, view, rows) {
    var first = rows[0] || {};
    if (field(view, first, "image") && looksImage(field(view, first, "image"))) {
      var g = h(ctx, "div", { cls: "grid" });
      rows.forEach(function (r) {
        var img = mediaUrl(ctx, str(field(view, r, "image")));
        var cap = str(field(view, r, "title"));
        add(ctx, g, h(ctx, "a", { cls: "tile", attrs: { href: img, target: "_blank", rel: "noreferrer noopener" } },
          picture(ctx, img, cap), h(ctx, "span", { cls: "cap" }, h(ctx, "span", { cls: "cap-t", text: cap }))));
      });
      return g;
    }
    if (field(view, first, "title") !== undefined) return listRows(ctx, view, rows);
    var cols = columnsOf(view, rows);
    var tb = h(ctx, "tbody");
    rows.slice(0, 200).forEach(function (r) { var tr = h(ctx, "tr"); cols.forEach(function (c) { add(ctx, tr, h(ctx, "td", {}, cell(ctx, get(r, c)))); }); add(ctx, tb, tr); });
    var hr = h(ctx, "tr"); cols.forEach(function (c) { add(ctx, hr, h(ctx, "th", { text: labelOf(view, c) })); });
    return h(ctx, "div", { cls: "scroll" }, h(ctx, "table", { cls: "tbl" }, h(ctx, "thead", {}, hr), tb));
  }

  function inputFor(ctx, inp) {
    var id = "f-" + inp.name;
    var el;
    if (inp.type === "choice") {
      el = h(ctx, "select", { cls: "input", attrs: { id: id, name: inp.name } });
      inp.choices.forEach(function (c) { add(ctx, el, h(ctx, "option", { text: str(c), attrs: { value: str(c) } })); });
    } else if (inp.type === "boolean") {
      el = h(ctx, "input", { attrs: { id: id, name: inp.name, type: "checkbox" } });
    } else {
      var t = inp.type === "number" || inp.type === "integer" ? "number" : inp.type === "date" ? "date" : "text";
      el = h(ctx, "input", { cls: "input", attrs: { id: id, name: inp.name, type: t, step: inp.type === "integer" ? "1" : t === "number" ? "any" : undefined, required: inp.required ? "" : undefined } });
      if (inp.default !== undefined) el.value = str(inp.default);
    }
    return el;
  }

  function form(view, ctx) {
    var wrap = shell(ctx, view, "form");
    var action = actionsOf(ctx).filter(function (a) { return a.id === view.action; })[0] || actionsOf(ctx).filter(function (a) { return a.source === view.source; })[0];
    if (!action) { add(ctx, wrap, problem(ctx, { error: "This form has no action to send to." })); return wrap; }
    var ins = inputsOf(action);
    var els = {};
    var out = h(ctx, "div", { cls: "body" });
    var f = h(ctx, "form", { cls: "form", on: { submit: function (e) { e.preventDefault(); run(); } } });
    ins.forEach(function (inp) {
      els[inp.name] = inputFor(ctx, inp);
      add(ctx, f, h(ctx, "label", { cls: "fld", attrs: { for: "f-" + inp.name } },
        h(ctx, "span", { cls: "lbl", text: words(inp.name) + (inp.required ? "" : " (if you like)") }), els[inp.name],
        inp.help ? h(ctx, "span", { cls: "muted small", text: inp.help }) : null));
    });
    add(ctx, f, h(ctx, "button", { cls: "btn accent", text: view.button || words(action.title || action.id), attrs: { type: "submit" } }));
    add(ctx, wrap, f, out);
    var run = loader(ctx, out, function () {
      var values = {};
      ins.forEach(function (inp) {
        var el = els[inp.name];
        if (inp.type === "boolean") values[inp.name] = !!el.checked;
        else if (el.value !== "") values[inp.name] = inp.type === "number" || inp.type === "integer" ? Number(el.value) : el.value;
      });
      return ctx.call(action.id, values);
    }, function (rows) { add(ctx, out, autoRows(ctx, view, rows)); });
    wrap._send = run;
    return wrap;
  }

  // ── settings ──────────────────────────────────────────────────────

  function placeWords(ctx) {
    var sets = (ctx.spec && ctx.spec.settings) || [];
    for (var i = 0; i < sets.length; i++) {
      if (sets[i].kind !== "place") continue;
      var v = (ctx.settings || {})[sets[i].key];
      if (v && (v.name || (num(v.lat) !== null && num(v.lon) !== null))) return v.name ? str(v.name) : Number(v.lat).toFixed(2) + ", " + Number(v.lon).toFixed(2);
    }
    return "";
  }

  /** Settings the spec's sources need that have no value yet. */
  function missingSettings(ctx) {
    var sets = (ctx.spec && ctx.spec.settings) || [];
    var used = JSON.stringify((ctx.spec && ctx.spec.sources) || []);
    return sets.filter(function (st) {
      if (st.kind === "key") { var kv = (ctx.settings || {})[st.key]; return !!st.required && !(kv && kv.set); }
      var v = (ctx.settings || {})[st.key];
      var needed = st.required || used.indexOf("setting:" + st.key) >= 0;
      if (!needed) return false;
      if (st.kind === "place") return !(v && (v.name || (num(v.lat) !== null && num(v.lon) !== null)));
      return v === undefined || v === null || v === "";
    });
  }

  function settingLabel(st) { var l = str(st.label) || words(st.key); return l.charAt(0).toUpperCase() + l.slice(1); }

  function settings(view, ctx) {
    var wrap = shell(ctx, view, "settings");
    var sets = (ctx.spec && ctx.spec.settings) || [];
    if (!sets.length) { add(ctx, wrap, h(ctx, "p", { cls: "muted", text: "This app has nothing to set." })); return wrap; }
    sets.forEach(function (st) {
      var val = (ctx.settings || {})[st.key];
      var said = h(ctx, "p", { cls: "said" });
      var box = h(ctx, "div", { cls: "setting", attrs: { id: "set-" + st.key, "data-kind": st.kind } },
        h(ctx, "p", { cls: "lbl", text: settingLabel(st) }),
        st.help ? h(ctx, "p", { cls: "muted small", text: str(st.help) }) : null);
      function save(value) {
        said.textContent = "Saving...";
        return Promise.resolve(ctx.saveSetting(st.key, value)).then(function (r) {
          r = r || {};
          if (r.error) { clear(said); add(ctx, said, problem(ctx, r)); return; }
          ctx.settings[st.key] = r.value !== undefined ? r.value : value;
          said.textContent = str(r.message) || "Saved.";
          if (ctx.onSettings) ctx.onSettings(st.key);
        }, function () { said.textContent = "This app's server is not answering."; });
      }
      if (st.kind === "place") {
        var v = val && typeof val === "object" ? val : {};
        var town = h(ctx, "input", { cls: "input", attrs: { type: "text", placeholder: "Your town or city", "aria-label": "Town or city", autocomplete: "address-level2", name: st.key + "-name" } });
        town.value = str(v.name);
        var lat = h(ctx, "input", { cls: "input", attrs: { type: "number", step: "any", min: "-90", max: "90", placeholder: "Latitude", "aria-label": "Latitude", name: st.key + "-lat" } });
        var lon = h(ctx, "input", { cls: "input", attrs: { type: "number", step: "any", min: "-180", max: "180", placeholder: "Longitude", "aria-label": "Longitude", name: st.key + "-lon" } });
        if (num(v.lat) !== null) lat.value = String(v.lat);
        if (num(v.lon) !== null) lon.value = String(v.lon);
        var exact = h(ctx, "details", { cls: "exact" }, h(ctx, "summary", { text: "Exact position (if you like)" }), h(ctx, "div", { cls: "row" }, lat, lon));
        if (num(v.lat) !== null) exact.setAttribute("open", "");
        var f = h(ctx, "form", { cls: "form", on: { submit: function (e) {
          e.preventDefault();
          var la = lat.value === "" ? null : Number(lat.value), lo = lon.value === "" ? null : Number(lon.value);
          if (!town.value.trim() && (la === null || lo === null)) { said.textContent = "Type a town, or both numbers."; return; }
          if ((la !== null && (la < -90 || la > 90)) || (lo !== null && (lo < -180 || lo > 180))) { said.textContent = "Latitude goes from -90 to 90 and longitude from -180 to 180."; return; }
          var out = { name: town.value.trim() };
          if (la !== null && lo !== null) { out.lat = la; out.lon = lo; }
          save(out).then(function () {
            var nv = ctx.settings[st.key] || {};
            if (num(nv.lat) !== null) lat.value = String(nv.lat);
            if (num(nv.lon) !== null) lon.value = String(nv.lon);
          });
        } } }, h(ctx, "div", { cls: "row" }, town, h(ctx, "button", { cls: "btn accent", text: "Save", attrs: { type: "submit" } })), exact);
        add(ctx, box, f);
      } else if (st.kind === "choice") {
        var sel = h(ctx, "select", { cls: "input", attrs: { "aria-label": settingLabel(st), name: st.key } });
        (st.choices || []).forEach(function (c) {
          var value = typeof c === "object" ? str(c.value) : str(c), label = typeof c === "object" ? str(c.label || c.value) : str(c);
          add(ctx, sel, h(ctx, "option", { text: label, attrs: { value: value } }));
        });
        if (val !== undefined) sel.value = str(val);
        sel.addEventListener("change", function () { save(sel.value); });
        add(ctx, box, sel);
      } else if (st.kind === "key") {
        // A key never goes into this app: it lives in the vault, set from the dashboard.
        var set = !!(val && val.set);
        var where = safeUrl(str(st.url)) || safeUrl(str(ctx.spec && ctx.spec.vault_url));
        add(ctx, box, h(ctx, "p", { cls: "small", text: set ? "Stored in your vault." : "Not set yet. It is stored in your vault, never in this app." }));
        if (where) add(ctx, box, h(ctx, "a", { cls: "btn", text: set ? "Change it in your vault" : "Put it in your vault", attrs: { href: where, target: "_blank", rel: "noreferrer noopener" } }));
        else add(ctx, box, h(ctx, "p", { cls: "muted small", text: "Open this app's page on your dashboard to put the key in your vault." }));
      } else {
        var txt = h(ctx, "input", { cls: "input", attrs: { type: "text", "aria-label": settingLabel(st), name: st.key } });
        txt.value = str(val);
        add(ctx, box, h(ctx, "form", { cls: "row", on: { submit: function (e) { e.preventDefault(); save(txt.value.trim()); } } },
          txt, h(ctx, "button", { cls: "btn accent", text: "Save", attrs: { type: "submit" } })));
      }
      add(ctx, box, said);
      add(ctx, wrap, box);
    });
    return wrap;
  }

  // ── links ─────────────────────────────────────────────────────────

  function links(view, ctx) {
    var wrap = shell(ctx, view, "links");
    var items = Array.isArray(view.items) ? view.items : (!view.source && ctx.spec && Array.isArray(ctx.spec.links) ? ctx.spec.links : null);
    function paint(target, rows) {
      var ul = h(ctx, "ul", { cls: "links" });
      rows.forEach(function (r) {
        var u = safeUrl(str(r.url || field(view, r, "link")));
        if (!u) return;
        add(ctx, ul, h(ctx, "li", {}, h(ctx, "a", { text: str(r.label || field(view, r, "title")) || u, attrs: { href: u, target: "_blank", rel: "noreferrer noopener" } }),
          r.note ? h(ctx, "span", { cls: "muted small", text: " " + str(r.note) }) : null));
      });
      add(ctx, target, ul);
    }
    if (items) { paint(wrap, items); return wrap; }
    var body = h(ctx, "div", { cls: "body" });
    add(ctx, wrap, body);
    var run = loader(ctx, body, function () { return ctx.call(actionFor(ctx, view), {}); }, function (rows) { paint(body, rows); });
    wrap._reload = run;
    run();
    return wrap;
  }

  // ── entry ─────────────────────────────────────────────────────────

  var VIEWS = { gallery: gallery, table: table, forecast: forecast, map: map, player: player, chart: chart, list: list, form: form, settings: settings, links: links };

  var TAB_WORDS = { gallery: "Pictures", table: "Table", forecast: "Forecast", map: "Map", player: "Watch and listen", chart: "Chart", list: "List", form: "Ask", settings: "Settings", links: "Links" };

  function renderView(view, ctx) {
    var fn = VIEWS[view && view.type];
    if (!fn) {
      var w = h(ctx, "section", { cls: "view" });
      add(ctx, w, problem(ctx, { error: "This app asks for a view it does not have: " + str(view && view.type) + "." }));
      return w;
    }
    return fn(view, ctx);
  }

  var api = {
    VIEWS: VIEWS,
    TAB_WORDS: TAB_WORDS,
    renderView: renderView,
    missingSettings: missingSettings,
    placeWords: placeWords,
    safeUrl: safeUrl,
    pageUrl: pageUrl,
    youTube: youTube,
    skyOf: skyOf,
    inputsOf: inputsOf,
    actionFor: actionFor,
    when: when,
    problem: problem,
  };
  root.PuffViews = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
