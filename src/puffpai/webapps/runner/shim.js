/* PuffPAI app shim. Copyright (c) Cypher Sisters. Apache-2.0 License.
 *
 * Put first in every page of an integrated browser app by the app host
 * (puffpai/webapps/host.py). It is a convenience, not the wall: the wall is the
 * Content-Security-Policy the host sends (connect-src 'self', img-src 'self',
 * ...), which the app cannot remove. If an app strips or outruns this shim, its
 * outside calls are simply blocked, never let through.
 *
 *  1. Outside calls go to the app's own forwarding address
 *     (/__puffpai/out?u=<address>), with the page's nonce. The Bridge decides
 *     there; nothing leaves before the owner's yes for that host.
 *  2. What the CSP blocked is reported (the first-run watch), with the nonce.
 *  3. localStorage is mirrored to the bot's copy (/__puffpai/storage): the page
 *     starts from the copy the host put in __PUFFPAI__.store and writes every
 *     change back with the version it started from. A stale page gets 409 and
 *     reloads, so it never overwrites newer data. While the bot is using the
 *     app a write gets 423 and is sent again a moment later; if it then turns
 *     out stale, the dashboard is told the change was not saved.
 *     In the bot's own hidden browser (CFG.runner, set by the host only for
 *     the runner's own page loads) the page never saves: the runner reads
 *     the page's storage through DevTools (no script in the page) and saves
 *     it itself, so
 *     nothing the app's script sends ever counts as the bot's.
 *  4. Links to other sites are handed to the dashboard, which asks first.
 */
(function () {
  "use strict";
  var CFG = window.__PUFFPAI__ || {};
  var NONCE = String(CFG.nonce || "");
  var SELF = location.origin;
  var OUT = "/__puffpai/out?u=";
  var HDR = "X-PuffPAI-Nonce";

  function outside(u) {
    try {
      var x = new URL(String(u), location.href);
      if (x.origin === SELF) return null;
      if (/^(https?|wss?):$/.test(x.protocol)) return x;
    } catch (e) {}
    return null;
  }
  function routed(u) {
    var x = outside(u);
    if (!x) return u;
    return OUT + encodeURIComponent(x.href.replace(/^ws(s?):/, "http$1:"));
  }

  // 1. network calls
  var _fetch = window.fetch;
  window.fetch = function (input, init) {
    var rerouted = false;
    try {
      if (input && typeof input === "object" && "url" in input) {
        var r = routed(input.url);
        if (r !== input.url) { input = new Request(r, input); rerouted = true; }
      } else {
        var s = routed(input);
        if (s !== input) { input = s; rerouted = true; }
      }
      if (rerouted) {
        init = Object.assign({}, init || {});
        var h = new Headers(init.headers || (input && input.headers) || {});
        h.set(HDR, NONCE);
        init.headers = h;
        init.credentials = "same-origin";
      }
    } catch (e) {}
    return _fetch.call(this, input, init);
  };
  var _open = XMLHttpRequest.prototype.open;
  var _send = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function (m, u) {
    var args = Array.prototype.slice.call(arguments);
    var r = routed(u);
    this.__ppRouted = r !== u;
    args[1] = r;
    return _open.apply(this, args);
  };
  XMLHttpRequest.prototype.send = function (body) {
    if (this.__ppRouted) { try { this.setRequestHeader(HDR, NONCE); } catch (e) {} }
    return _send.call(this, body);
  };
  if (navigator.sendBeacon) {
    var _beacon = navigator.sendBeacon.bind(navigator);
    // a beacon cannot carry the nonce, so an outside beacon is refused there
    navigator.sendBeacon = function (u, d) { return _beacon(routed(u), d); };
  }
  if (window.EventSource) {
    var _ES = window.EventSource;
    window.EventSource = function (u, o) { return new _ES(routed(u), o); };
    window.EventSource.prototype = _ES.prototype;
  }
  if (window.WebSocket) {
    var _WS = window.WebSocket;
    window.WebSocket = function (u, p) {
      var x = outside(u);
      if (x) {
        _fetch.call(window, OUT + encodeURIComponent(x.href.replace(/^ws(s?):/, "http$1:")),
                    {method: "HEAD", headers: {"X-PuffPAI-Nonce": NONCE}}).catch(function () {});
        throw new DOMException("This app's live connection to " + x.host +
                               " is not switched on", "SecurityError");
      }
      return p === undefined ? new _WS(u) : new _WS(u, p);
    };
    window.WebSocket.prototype = _WS.prototype;
  }

  // 2. report what the CSP blocked (the first-run watch)
  document.addEventListener("securitypolicyviolation", function (e) {
    try {
      _fetch.call(window, "/__puffpai/report", {method: "POST",
        headers: {"Content-Type": "application/json", "X-PuffPAI-Nonce": NONCE},
        body: JSON.stringify({kind: "blocked", uri: e.blockedURI,
                              directive: e.effectiveDirective})});
    } catch (x) {}
  });

  // 3. storage mirror
  if (CFG.mirror !== false) (function () {
    var store = CFG.store || {v: 0, data: {}};
    var version = store.v || 0;
    var LS = window.localStorage;
    var proto = Storage.prototype;
    var _set = proto.setItem, _rem = proto.removeItem, _clr = proto.clear;
    function snapshot() {
      var o = {};
      for (var i = 0; i < LS.length; i++) { var k = LS.key(i); o[k] = LS.getItem(k); }
      return o;
    }
    if (version > 0) {
      _clr.call(LS);
      Object.keys(store.data || {}).forEach(function (k) { _set.call(LS, k, store.data[k]); });
    }
    if (CFG.runner === true) return;     // the runner saves through its own channel
    var last = JSON.stringify(snapshot());
    var timer = null, stale = false, held = false;
    function push() {
      timer = null;
      var now = snapshot(), text = JSON.stringify(now);
      if (text === last || stale) return;
      var body = JSON.stringify({base: version, data: now});
      var x = new XMLHttpRequest();
      _open.call(x, "PUT", "/__puffpai/storage", false);    // sync: a closing
      x.setRequestHeader("Content-Type", "application/json");  // page still saves
      x.setRequestHeader(HDR, NONCE);
      try { _send.call(x, body); } catch (e) { return; }
      if (x.status === 200) { version = JSON.parse(x.responseText).v; last = text; held = false; }
      else if (x.status === 423) {            // the bot is using the app: send it again soon
        held = true;
        if (!timer) timer = setTimeout(push, 1500);
      } else if (x.status === 409) {
        stale = true;
        // a change held while the bot used the app could not be saved: say so
        if (held) { try { parent.postMessage({puffpai: "not-saved"}, "*"); } catch (e) {} }
        location.reload();
      }
    }
    // Saved at the end of the same task (a microtask), never later: an app
    // that saves and then reloads at once ("location.reload()" right after
    // setItem) loses a save made on a timer, and browsers refuse the
    // synchronous save on "pagehide" (final fix, a to-do app's add was lost).
    // Every write in one task is one save.
    var queued = false;
    var later = typeof queueMicrotask === "function" ? queueMicrotask
      : function (f) { Promise.resolve().then(f); };
    function soon() {
      if (queued || timer) return;
      queued = true;
      later(function () { queued = false; push(); });
    }
    proto.setItem = function (k, v) { var r = _set.call(this, k, v); if (this === LS) soon(); return r; };
    proto.removeItem = function (k) { var r = _rem.call(this, k); if (this === LS) soon(); return r; };
    proto.clear = function () { var r = _clr.call(this); if (this === LS) soon(); return r; };
    setInterval(function () {             // catches localStorage.x = y writes
      if (JSON.stringify(snapshot()) !== last) soon();
    }, 1000);
    addEventListener("pagehide", push);
    window.__PUFFPAI_FLUSH__ = push;       // the bot's hidden browser saves at once
    // the bot (or another screen) changed the data: reload unless typing
    setInterval(function () {
      if (stale) return;
      _fetch.call(window, "/__puffpai/storage?v=1").then(function (r) { return r.json(); })
        .then(function (j) {
          var typing = document.activeElement && /^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName);
          if (j.v > version && !typing && JSON.stringify(snapshot()) === last) { stale = true; location.reload(); }
        }).catch(function () {});
    }, 2000);
  })();

  // 4. links to other sites: ask the dashboard
  document.addEventListener("click", function (e) {
    var a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
    if (!a) return;
    var x = outside(a.href);
    if (!x) return;
    e.preventDefault();
    try { parent.postMessage({puffpai: "open-link", url: x.origin + x.pathname}, "*"); } catch (err) {}
  }, true);

  window.__PUFFPAI_SHIM__ = {loaded: true};
})();
