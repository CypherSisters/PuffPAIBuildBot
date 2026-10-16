/*
 * The page of an assembled app: reads /spec.json, shows one view at a time
 * with a tab per view (and Settings when it has any), and talks only to its
 * own server on this computer:
 *
 *   GET  /spec.json              the app's plan (assembled_app/v1)
 *   GET  /settings.json          { values: { key: value } } (keys: { set: true })
 *   POST /settings               { key, value } -> { ok, value?, message? } | { error, fix? }
 *   POST /api/<action>           input as JSON  -> { rows: [...] } | { error, fix? }
 *   POST <fix.url>               { kind, ...fix.body } -> { ok, message? } | { error }
 *   parent.postMessage           { puffpai: "fix", kind, service, name } (a switch or a key:
 *                                the dashboard page shows the card and does it)
 *
 * The page itself makes no outside calls except what a view plays or shows:
 * pictures and streams (through spec.media_proxy when the server sets it)
 * and YouTube frames, which say so in plain words.
 *
 * ?theme=day|night picks the look (the dashboard passes its own); otherwise
 * it follows the computer's light or dark setting.
 *
 * puff puff pass
 */
(function () {
  "use strict";
  var V = window.PuffViews;
  var doc = document;

  function json(url, init) {
    return fetch(url, Object.assign({ cache: "no-store", credentials: "same-origin" }, init || {})).then(function (res) {
      return res.text().then(function (t) {
        var data = {};
        try { data = t ? JSON.parse(t) : {}; } catch (e) { data = {}; }
        if (!res.ok && !data.error) data = { error: "This app's server said no (" + res.status + ")." };
        return data;
      });
    });
  }

  function post(url, body) {
    return json(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) })
      .catch(function () { return { error: "This app's server is not answering. Start it again from its page on your dashboard." }; });
  }

  var hlsPromise = null;
  function loadHls() {
    if (window.Hls) return Promise.resolve(window.Hls);
    if (!hlsPromise) {
      hlsPromise = new Promise(function (resolve) {
        var sc = doc.createElement("script");
        sc.src = "vendor/hls-1.6.15.min.js";
        sc.onload = function () { resolve(window.Hls || null); };
        sc.onerror = function () { resolve(null); };
        doc.head.appendChild(sc);
      });
    }
    return hlsPromise;
  }

  function theme() {
    var t = new URLSearchParams(location.search).get("theme");
    if (t !== "day" && t !== "night") t = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches ? "day" : "night";
    doc.documentElement.setAttribute("data-theme", t);
  }

  function el(tag, cls, text) {
    var e = doc.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function boot() {
    theme();
    var main = doc.getElementById("app");
    Promise.all([json("/spec.json"), json("/settings.json").catch(function () { return {}; })]).then(function (got) {
      var spec = got[0] || {};
      if (spec.error || !Array.isArray(spec.views)) {
        main.textContent = "";
        main.appendChild(el("p", "problem", "This app could not read its plan. Use \"Put it together again\" on its page on your dashboard."));
        return;
      }
      var values = (got[1] && (got[1].values || got[1])) || {};
      if (values.error) values = {};
      start(main, spec, values);
    }, function () {
      main.textContent = "";
      main.appendChild(el("p", "problem", "This app's server is not answering. Start it again from its page on your dashboard."));
    });
  }

  function start(main, spec, values) {
    doc.title = spec.title || "Your app";
    var views = spec.views.slice();
    if ((spec.settings || []).length && !views.some(function (v) { return v.type === "settings"; })) views.push({ type: "settings" });

    var current = null;
    var tabs = [];
    var ctx = {
      doc: doc,
      spec: spec,
      settings: values,
      call: function (id, input) { return post("/api/" + encodeURIComponent(String(id || "")), input); },
      saveSetting: function (key, value) { return post("/settings", { key: key, value: value }); },
      postFix: function (fix) { return post(fix.url, Object.assign({ kind: fix.kind }, fix.body || {})); },
      // Inside its dashboard page: ask that page to show the card (it checks
      // this came from this frame and asks BuildBot for the card's words).
      // Only a kind and an id are sent.
      askDashboard: function (fix) {
        if (!window.parent || window.parent === window) return false;
        window.parent.postMessage({ puffpai: "fix", kind: String(fix.kind || ""), service: String(fix.service || "").slice(0, 64), name: String(fix.name || "").slice(0, 40) }, "*");
        return true;
      },
      loadHls: loadHls,
      openSettings: function (key) {
        var i = views.findIndex(function (v) { return v.type === "settings"; });
        if (i >= 0) show(i);
        var at = key && doc.getElementById("set-" + key);
        if (at && at.scrollIntoView) at.scrollIntoView({ block: "center" });
      },
      onSettings: function () { banner(); },
    };

    main.textContent = "";
    var head = el("header", "top");
    head.appendChild(el("h1", "title", spec.title || "Your app"));
    if (spec.intro) head.appendChild(el("p", "intro", spec.intro));
    var nav = el("nav", "tabs");
    nav.setAttribute("aria-label", "Parts of this app");
    var note = el("div", "banner");
    var stage = el("main", "stage-main");
    main.appendChild(head);
    if (views.length > 1) main.appendChild(nav);
    main.appendChild(note);
    main.appendChild(stage);

    views.forEach(function (v, i) {
      var b = el("button", "tab", v.title || V.TAB_WORDS[v.type] || v.type);
      b.type = "button";
      b.addEventListener("click", function () { show(i); });
      nav.appendChild(b);
      tabs.push(b);
    });

    function banner() {
      note.textContent = "";
      var missing = V.missingSettings(ctx);
      if (!missing.length || (current !== null && views[current].type === "settings")) return;
      var words = missing.map(function (m) { return m.label || (m.kind === "place" ? "your town" : m.key.replace(/_/g, " ")); }).join(", ");
      var p = el("p", "", "Tell this app " + words + " first, so it can fill in the rest.");
      var b = el("button", "btn accent", "Open settings");
      b.type = "button";
      b.addEventListener("click", function () { ctx.openSettings(missing[0].key); });
      note.appendChild(p);
      note.appendChild(b);
    }

    function show(i) {
      if (current === i) return;
      var old = stage.firstChild;
      if (old && old._stop) old._stop();
      current = i;
      tabs.forEach(function (t, j) { t.setAttribute("aria-current", j === i ? "page" : "false"); });
      stage.textContent = "";
      stage.appendChild(V.renderView(views[i], ctx));
      if (history.replaceState) history.replaceState(null, "", "#" + i);
      banner();
    }

    var first = parseInt((location.hash || "").slice(1), 10);
    show(first >= 0 && first < views.length ? first : 0);
  }

  if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
