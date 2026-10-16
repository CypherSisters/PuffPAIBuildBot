/* PuffPAI app guard. Copyright (c) Cypher Sisters. Apache-2.0 License.
 *
 * Put FIRST in every page of an integrated browser app by the app host
 * (puffpai/webapps/host.py), before any of the app's own code. It closes the
 * ways a page can make the owner's browser reach another address by itself,
 * past the Content-Security-Policy (measured in Edge 154 and Chrome 153):
 *
 *  1. Resource hints. A <link> whose rel is prerender, prefetch, dns-prefetch
 *     or preconnect is acted on by the browser whatever the CSP says
 *     (prerender sent a request carrying page data). Every way script can give
 *     a link such a rel (the rel property, relList, setAttribute and friends,
 *     Attr nodes) leaves the rel as "x-puffpai-removed" instead.
 *  2. Child frames. A frame the app makes could run script the guard is not
 *     in (srcdoc, about:blank, a javascript: address) or lend the app clean
 *     copies of the functions patched here, and moving a frame to another
 *     address opened a connection there before any policy stopped it
 *     (measured: src, setAttribute, markup, frames[i].location,
 *     window.open or a form aimed at a named frame). So the app makes no
 *     frame at all: <iframe>, <frame> and <frameset> are refused on every
 *     route (createElement, insertion, HTML text) and made inert in the
 *     page's own markup by the host. Any frame that still appeared would
 *     get sandbox="" from the start, never loosened.
 *  3. HTML text. The CSP requires Trusted Types, and this guard owns the only
 *     policy ("default"): every string that becomes HTML (innerHTML,
 *     outerHTML, insertAdjacentHTML, document.write, srcdoc, DOMParser and the
 *     rest) passes through clean(), which makes hints inert and gives every
 *     frame tag sandbox="" as its first attribute. A javascript: address is
 *     never navigated to. Scripts the app runs by itself (eval, script text)
 *     pass unchanged: the CSP already lets the app run its own code.
 *
 *  5. Moves. Moving the page itself to another address
 *     (location, navigation.navigate, window.open, a form, a link, a meta
 *     refresh) opened a connection there before the dashboard's policy
 *     stopped it. The guard listens for the browser's own "navigate" event
 *     first, before any of the app's code, and cancels every move to an
 *     address that is not the app's own, so the browser never starts it;
 *     window.open is refused for any other address too.
 *
 *  4. WebRTC (final fix). The CSP's "webrtc 'block'" is not honoured by
 *     Edge or Chrome: STUN and TURN packets reached any address. The guard
 *     takes RTCPeerConnection (and its old prefixed names) off the page
 *     before any of the app's code runs, so no app script can make a direct
 *     connection. Every frame the app makes is sealed with no script (2), so
 *     no fresh copy of it can be fetched from a child frame. An app made for
 *     calls cannot call in this round (none is integrated; the static scan
 *     holds apps that make direct connections).
 *
 * What it does NOT close (said plainly in headers.py): a move the browser
 * makes without a cancelable "navigate" event (going back or forward in
 * history) is not seen here; the app's frame has no history of its own
 * outside its address. A browser without the Navigation API (none of the
 * ones BuildBot supports) would leave moves to the policies alone.
 *
 * If this file does not run, Trusted Types has no policy, so the app cannot
 * put any HTML text into the page at all: it fails closed.
 */
(function () {
  "use strict";
  if (window.__PUFFPAI_GUARD__) return;
  var def = Object.defineProperty;
  def(window, "__PUFFPAI_GUARD__", { value: true, configurable: false, writable: false });

  // ---- 4. no WebRTC: the constructors are gone before the app runs ----------
  ["RTCPeerConnection", "webkitRTCPeerConnection", "mozRTCPeerConnection"].forEach(function (name) {
    try {
      def(window, name, { value: undefined, configurable: false, writable: false, enumerable: false });
    } catch (e) {
      try { delete window[name]; } catch (e2) { /* nothing more to take away */ }
    }
  });

  var HINT = /prerender|prefetch|preconnect|speculation/i;
  var PRELOAD = /preload/i;
  var NEUTRAL = "x-puffpai-removed";
  var gd = Object.getOwnPropertyDescriptor;
  var EP = Element.prototype;
  var rawSetAttr = EP.setAttribute;
  var rawGetAttr = EP.getAttribute;
  var lower = function (s) { return String(s == null ? "" : s).toLowerCase(); };

  function isLink(el) { return !!el && el.nodeType === 1 && lower(el.localName) === "link"; }
  function isFrame(el) { return !!el && el.nodeType === 1 && lower(el.localName) === "iframe"; }
  // no frame is ever made: a frame moved to another address
  // opened a connection there before any policy stopped it
  var NO_MAKE = { frame: 1, frameset: 1, iframe: 1 };
  function outsideRef(href) {
    var h = String(href == null ? "" : href).trim();
    return h === "" || /^\/\//.test(h) || /^\\/.test(h) || /^[a-z][a-z0-9+.\-]*:/i.test(h);
  }
  function relOk(el, value) {
    var v = String(value == null ? "" : value);
    if (HINT.test(v)) return NEUTRAL;
    if (PRELOAD.test(v) && outsideRef(rawGetAttr.call(el, "href"))) return NEUTRAL;
    return v;
  }

  // ---- 3. every HTML string: hints inert, frames sandboxed --------------------
  function decode(s) {
    return String(s).replace(/&#x([0-9a-f]+);?/gi, function (_m, h) { return String.fromCharCode(parseInt(h, 16)); })
      .replace(/&#([0-9]+);?/g, function (_m, d) { return String.fromCharCode(parseInt(d, 10)); })
      .replace(/&tab;/gi, "\t").replace(/&newline;/gi, "\n").replace(/&colon;/gi, ":")
      .replace(/&lt;/gi, "<").replace(/&gt;/gi, ">").replace(/&quot;/gi, "\"").replace(/&apos;/gi, "'")
      .replace(/&amp;/gi, "&");
  }
  function tagEnd(s, i) {
    var q = "";
    for (var j = i; j < s.length; j++) {
      var c = s.charAt(j);
      if (q) { if (c === q) q = ""; }
      else if ((c === "\"" || c === "'") && /[=\s]/.test(s.charAt(j - 1))) q = c;
      else if (c === ">") return j + 1;
    }
    return s.length;
  }
  function cleanOnce(s) {
    var out = "", pos = 0, re = /<(link|script|iframe|frameset|frame)(?=[\s\/>])/gi, m;
    while ((m = re.exec(s))) {
      var name = m[1].toLowerCase();
      var said = decode(s.slice(m.index, tagEnd(s, m.index))).toLowerCase().replace(/\s+/g, "");
      var put = null;
      if (name === "link") {
        var hm = /href=(["']?)([^"'>]*)/.exec(said);
        if (HINT.test(said) || (PRELOAD.test(said) && (!hm || outsideRef(hm[2])))) put = "<" + NEUTRAL;
      } else if (name === "script") {
        if (said.indexOf("speculationrules") >= 0) put = "<script type=\"text/x-puffpai-removed\"";
      } else {
        put = "<" + NEUTRAL;
      }
      if (put !== null) { out += s.slice(pos, m.index) + put; pos = m.index + m[0].length; }
    }
    return out + s.slice(pos);
  }
  function clean(html) {
    var s = String(html);
    // tags written with character references inside an attribute (a srcdoc
    // in innerHTML text): made inert too, so a frame's own text is clean
    s = s.replace(/(&lt;|&#0*60;?|&#x0*3c;?)(\s*)(link|iframe|frameset|frame)(?=[\s\/>&])/gi, function (_m, lt, sp) {
      return lt + sp + NEUTRAL;
    });
    return cleanOnce(s);
  }
  def(window, "__PUFFPAI_CLEAN__", { value: clean, configurable: false, writable: false });

  var tt = window.trustedTypes;
  if (tt && tt.createPolicy) {
    try {
      tt.createPolicy("default", {
        createHTML: function (s) { return clean(s); },
        createScript: function (s, _type, sink) {
          // a javascript: address never runs (a new page the guard is not in)
          if (/location|href|src|navigat|javascript/i.test(String(sink || ""))) return null;
          return s;
        },
        createScriptURL: function (s) {
          return /^\s*javascript:/i.test(String(s)) ? null : s;
        },
      });
    } catch (e) { /* already made: the app cannot make another (CSP) */ }
  }

  // ---- 2. frames: sandbox="" from birth, never loosened -------------------------
  var boxLists = new WeakSet();
  var relLists = new WeakSet();
  function seal(el) {
    if (isFrame(el) && rawGetAttr.call(el, "sandbox") !== "") rawSetAttr.call(el, "sandbox", "");
  }
  function fixLink(el) {
    if (isLink(el)) {
      var r = rawGetAttr.call(el, "rel");
      if (r !== null && relOk(el, r) !== r) rawSetAttr.call(el, "rel", NEUTRAL);
    }
  }
  function sweep(node) {
    if (!node || typeof node !== "object" || !node.nodeType) return;
    if (node.nodeType === 1) {
      if (NO_MAKE[lower(node.localName)]) throw new DOMException("Not allowed in this app.", "NotAllowedError");
      seal(node); fixLink(node);
    }
    if ((node.nodeType === 1 || node.nodeType === 11 || node.nodeType === 9) && node.querySelectorAll) {
      var found = node.querySelectorAll("link,iframe,frame,frameset");
      for (var i = 0; i < found.length; i++) {
        if (NO_MAKE[lower(found[i].localName)]) throw new DOMException("Not allowed in this app.", "NotAllowedError");
        seal(found[i]); fixLink(found[i]);
      }
    }
  }
  function lock(proto, name, desc) {
    if (!proto) return;
    try { def(proto, name, Object.assign({ configurable: false, enumerable: true }, desc)); } catch (e) {}
  }
  function wrapCreate(proto, name) {
    var d = proto && gd(proto, name);
    if (!d || typeof d.value !== "function") return;
    var raw = d.value;
    lock(proto, name, { writable: false, value: function () {
      var el = raw.apply(this, arguments);
      if (el && NO_MAKE[lower(el.localName)]) throw new DOMException("Not allowed in this app.", "NotAllowedError");
      seal(el);
      return el;
    } });
  }
  wrapCreate(Document.prototype, "createElement");
  wrapCreate(Document.prototype, "createElementNS");

  function frameProp(name, check) {
    var P = window.HTMLIFrameElement && HTMLIFrameElement.prototype;
    var d = P && gd(P, name);
    if (!d || !d.set) return;
    lock(P, name, { get: d.get, set: function (v) { var w = check(this, v); if (w !== undefined) d.set.call(this, w); } });
  }
  frameProp("src", function (_el, v) { return /^\s*javascript:/i.test(String(v)) ? "about:blank" : v; });
  (function () {
    var P = window.HTMLIFrameElement && HTMLIFrameElement.prototype;
    var d = P && gd(P, "sandbox");
    if (d && d.get) {
      lock(P, "sandbox", { get: function () { var l = d.get.call(this); boxLists.add(l); return l; },
                           set: function () { rawSetAttr.call(this, "sandbox", ""); } });
    }
  })();

  // ---- 1. links: no hint rel, whatever the route --------------------------------
  (function () {
    var P = window.HTMLLinkElement && HTMLLinkElement.prototype;
    if (!P) return;
    var d = gd(P, "rel");
    if (d && d.set) lock(P, "rel", { get: d.get, set: function (v) { d.set.call(this, relOk(this, v)); } });
    var dl = gd(P, "relList");
    if (dl && dl.get) {
      lock(P, "relList", { get: function () { var l = dl.get.call(this); relLists.add(l); return l; },
                           set: function (v) { if (d && d.set) d.set.call(this, relOk(this, v)); } });
    }
    var dh = gd(P, "href");
    if (dh && dh.set) {
      lock(P, "href", { get: dh.get, set: function (v) {
        dh.set.call(this, v);
        fixLink(this);
      } });
    }
  })();

  var TL = DOMTokenList.prototype;
  function tokens(args) { return Array.prototype.join.call(args, " "); }
  (function () {
    ["add", "replace", "toggle"].forEach(function (name) {
      var d = gd(TL, name);
      if (!d || typeof d.value !== "function") return;
      var raw = d.value;
      lock(TL, name, { writable: false, value: function () {
        if (boxLists.has(this)) {
          if (name === "toggle" && arguments[1] === false) return raw.apply(this, arguments);
          return name === "toggle" ? false : undefined;
        }
        if (relLists.has(this) && HINT.test(tokens(arguments))) return name === "toggle" ? false : undefined;
        return raw.apply(this, arguments);
      } });
    });
    var dv = gd(TL, "value");
    if (dv && dv.set) {
      lock(TL, "value", { get: dv.get, set: function (v) {
        if (boxLists.has(this)) return;
        if (relLists.has(this) && HINT.test(String(v))) v = NEUTRAL;
        dv.set.call(this, v);
      } });
    }
  })();

  // attributes, by name, on links and frames
  function attrOk(el, name, value) {
    var n = lower(name);
    if (isLink(el) && (n === "rel" || n === "href")) {
      if (n === "rel") return relOk(el, value);
      return value;
    }
    if (isFrame(el)) {
      if (n === "sandbox") return "";
      if (n === "src" && /^\s*javascript:/i.test(String(value))) return "about:blank";
    }
    return value;
  }
  (function () {
    var d = gd(EP, "setAttribute");
    lock(EP, "setAttribute", { writable: false, value: function (name, value) {
      var r = d.value.call(this, name, attrOk(this, name, value));
      if (isLink(this)) fixLink(this);
      return r;
    } });
    var dn = gd(EP, "setAttributeNS");
    if (dn) {
      lock(EP, "setAttributeNS", { writable: false, value: function (ns, name, value) {
        var local = String(name).split(":").pop();
        var r = dn.value.call(this, ns, name, ns == null ? attrOk(this, local, value) : value);
        if (isLink(this)) fixLink(this);
        return r;
      } });
    }
    ["removeAttribute", "removeAttributeNS", "toggleAttribute"].forEach(function (fn) {
      var dr = gd(EP, fn);
      if (!dr) return;
      lock(EP, fn, { writable: false, value: function () {
        var name = lower(arguments[fn === "removeAttributeNS" ? 1 : 0]);
        if (isFrame(this) && name === "sandbox") return fn === "toggleAttribute" ? true : undefined;
        return dr.value.apply(this, arguments);
      } });
    });
    ["setAttributeNode", "setAttributeNodeNS", "removeAttributeNode"].forEach(function (fn) {
      var da = gd(EP, fn);
      if (!da) return;
      lock(EP, fn, { writable: false, value: function (attr) {
        if (isFrame(this) && attr && lower(attr.name) === "sandbox") {
          if (fn === "removeAttributeNode") return attr;
          rawSetAttr.call(this, "sandbox", "");
          return null;
        }
        if (isLink(this) && attr && lower(attr.name) === "rel" && fn !== "removeAttributeNode") {
          rawSetAttr.call(this, "rel", relOk(this, attr.value));
          return null;
        }
        var r = da.value.apply(this, arguments);
        fixLink(this);
        return r;
      } });
    });
    var NP = window.NamedNodeMap && NamedNodeMap.prototype;
    ["setNamedItem", "setNamedItemNS", "removeNamedItem", "removeNamedItemNS"].forEach(function (fn) {
      var dm = NP && gd(NP, fn);
      if (!dm) return;
      lock(NP, fn, { writable: false, value: function () {
        // an element's attribute list is changed only through the checked calls
        throw new DOMException("Not allowed in this app.", "NotAllowedError");
      } });
    });
    // an Attr already on a link or frame: its value goes through the same check
    function attrSetter(proto, name) {
      var da = proto && gd(proto, name);
      if (!da || !da.set) return;
      lock(proto, name, { get: da.get, set: function (v) {
        var el = this && this.nodeType === 2 ? this.ownerElement : null;
        if (el) v = attrOk(el, this.name, v);
        da.set.call(this, v);
        if (el) fixLink(el);
      } });
    }
    attrSetter(window.Attr && Attr.prototype, "value");
    attrSetter(Node.prototype, "nodeValue");
    attrSetter(Node.prototype, "textContent");
  })();

  // ---- insertion: whatever arrives is swept first (a second wall) ---------------
  function wrapInsert(proto, name) {
    var d = proto && gd(proto, name);
    if (!d || typeof d.value !== "function") return;
    var raw = d.value;
    lock(proto, name, { writable: false, value: function () {
      for (var i = 0; i < arguments.length; i++) sweep(arguments[i]);
      return raw.apply(this, arguments);
    } });
  }
  ["appendChild", "insertBefore", "replaceChild"].forEach(function (n) { wrapInsert(Node.prototype, n); });
  [EP, window.CharacterData && CharacterData.prototype, window.DocumentType && DocumentType.prototype].forEach(function (p) {
    ["before", "after", "replaceWith"].forEach(function (n) { wrapInsert(p, n); });
  });
  [EP, Document.prototype, window.DocumentFragment && DocumentFragment.prototype].forEach(function (p) {
    ["append", "prepend", "replaceChildren", "moveBefore"].forEach(function (n) { wrapInsert(p, n); });
  });
  wrapInsert(EP, "insertAdjacentElement");
  wrapInsert(window.Range && Range.prototype, "insertNode");
  wrapInsert(window.Range && Range.prototype, "surroundContents");
  wrapInsert(Document.prototype, "adoptNode");
  wrapInsert(Document.prototype, "importNode");
  (function () {
    var d = gd(Node.prototype, "cloneNode");
    if (!d) return;
    lock(Node.prototype, "cloneNode", { writable: false, value: function () {
      var c = d.value.apply(this, arguments);
      sweep(c);
      return c;
    } });
  })();
  // document.write can split a tag across calls, past any check: never used
  ["write", "writeln"].forEach(function (n) {
    lock(Document.prototype, n, { writable: false, value: function () {
      throw new DOMException("Not allowed in this app.", "NotAllowedError");
    } });
  });
  (function () {
    var d = gd(Document.prototype, "execCommand");
    if (!d) return;
    lock(Document.prototype, "execCommand", { writable: false, value: function (cmd, ui, value) {
      if (/^inserthtml$/i.test(String(cmd))) value = clean(value);
      return d.value.call(this, cmd, ui, value);
    } });
  })();
  // the Sanitizer API's setHTML is not a Trusted Types sink: swept after
  ["setHTML"].forEach(function (n) {
    [EP, window.ShadowRoot && ShadowRoot.prototype].forEach(function (p) {
      var d = p && gd(p, n);
      if (!d || typeof d.value !== "function") return;
      lock(p, n, { writable: false, value: function () {
        var r = d.value.apply(this, arguments);
        sweep(this);
        return r;
      } });
    });
  });

  // ---- 5. moves: the page never moves itself to another address ---------------
  // Taken before any of the app's code runs: the app can replace URL, the
  // event's getters or preventDefault later, never these copies.
  var RawURL = window.URL;
  var urlProto = RawURL && RawURL.prototype;
  var protoGet = urlProto && gd(urlProto, "protocol") && gd(urlProto, "protocol").get;
  var originGet = urlProto && gd(urlProto, "origin") && gd(urlProto, "origin").get;
  var baseGet = gd(Node.prototype, "baseURI") && gd(Node.prototype, "baseURI").get;
  var here = location.origin;
  var rawDoc = document;
  function ours(u) {
    try {
      var x = new RawURL(String(u), baseGet ? baseGet.call(rawDoc) : undefined);
      return /^https?:$/.test(protoGet.call(x)) && originGet.call(x) === here;
    } catch (e) { return false; }
  }
  (function () {
    var nav = window.navigation;
    var NE = window.NavigateEvent && NavigateEvent.prototype;
    var ND = window.NavigationDestination && NavigationDestination.prototype;
    var dd = NE && gd(NE, "destination"), du = ND && gd(ND, "url");
    if (!nav || !dd || !dd.get || !du || !du.get || !protoGet || !originGet) return;
    var prevent = Event.prototype.preventDefault;
    EventTarget.prototype.addEventListener.call(nav, "navigate", function (e) {
      var to = "";
      try { to = du.get.call(dd.get.call(e)); } catch (x) { to = ""; }
      if (!ours(to)) prevent.call(e);
    }, true);
  })();
  (function () {
    var rawOpen = window.open;
    if (typeof rawOpen !== "function") return;
    try {
      def(window, "open", { configurable: false, writable: false, value: function (url) {
        if (url !== undefined && url !== "" && !ours(url)) return null;
        return rawOpen.apply(this, arguments);
      } });
    } catch (e) { /* left as it is: the frame's sandbox still refuses new windows */ }
  })();
})();
