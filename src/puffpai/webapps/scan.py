"""Static scan of a downloaded browser app: what it would reach and what it uses.

Reads files, never runs them. Goal-agnostic: it knows no app. It reports

* every outside host, by HOW the app uses it (code it loads, a look,
  lettering, pictures, data it asks for, a form it sends, a link, a mention),
  whether it is needed while the app runs or can be saved once while adding
  it, and Toolsmith's own classification of what the host is for
  (``fonts``, ``cdn``, ``api``, ``auth``, ``maps``, unknown), which is where
  the consent words come from (never from the app's own text);
* browser features it uses (``RTCPeerConnection`` among them);
* the storage keys it names, whether it keeps data at all (``keeps_data``),
  and whether its data lives only in IndexedDB (then the bot cannot drive it);
* whether it is a tool at all (``interactive``: it has script or controls a
  person uses, such as buttons, fields or a drawing area). A page with neither
  is a document (an invitation template, an article, a link list), not a tool;
  a tool that stores nothing (a chess board, a timer, dice) is still a tool;
* network calls whose address is built while running (the static scan cannot
  name those hosts; the first-run watch does);
* every outside link found in its files (the only links the open-link hand-off
  will ever open).

Ported from the prototype ``appbridge/scan.py``.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import re
from html.parser import HTMLParser
from typing import Any

SCHEMA = "puffpai_webapp_scan/v1"
TEXT_EXT = {".html", ".htm", ".js", ".mjs", ".cjs", ".css", ".json",
            ".webmanifest", ".svg"}
MAX_FILE = 8 * 1024 * 1024
MAX_FILES = 20000
SKIP_DIRS = {"node_modules", ".git", "test", "tests", "__pycache__"}

NAMESPACE_URLS = re.compile(
    r"https?://www\.w3\.org/(?:2000/svg|1999/xhtml|1999/xlink|XML/1998/"
    r"namespace|2000/xmlns|1998/Math/MathML)"
    # the names of XML vocabularies an app
    # writes into files it makes (a spreadsheet, a picture), never loaded
    r"|https?://(?:www\.w3\.org/(?:19|20)\d\d/|www\.w3\.org/XML/|"
    r"schemas\.openxmlformats\.org/|schemas\.microsoft\.com/office/|"
    r"purl\.org/dc/|ns\.adobe\.com/|xmlns\.com/|"
    r"www\.inkscape\.org/namespaces/|sodipodi\.sourceforge\.net/DTD/)")
#: A URL handed straight to an XML name call or written as ``xmlns`` (the
#: text just before it): a name, not a place the app loads from. Never when
#: a call that loads (fetch, XMLHttpRequest and the like) stands there too.
NAMESPACE_BEFORE = re.compile(r"(?:createElementNS|createAttributeNS|setAttributeNS|"
                              r"getAttributeNS|createDocument|lookupNamespaceURI|"
                              r"xmlns(?::\w+)?\s*=)\s*\(?\s*[\\\"'`]*$", re.I)
PLACEHOLDER = re.compile(r"(?i)(your[-_]|example\.(?:com|org|net)|"
                         r"\.example$|\.test$|\.invalid$|localhost|"
                         r"xxx|<[^>]+>|\{[^}]+\})")
URL_RE = re.compile(r"""(?P<url>(?:https?:|wss?:)?//[A-Za-z0-9][A-Za-z0-9.-]*"""
                    r"""\.[A-Za-z]{2,}(?::\d+)?(?:/[^\s"'`)<>\\]*)?)""")
COMMENT_JS = re.compile(r"/\*.*?\*/|(?<![:\"'\\])//[^\n]*", re.S)

FEATURES = {
    "localStorage": r"\blocalStorage\b",
    "sessionStorage": r"\bsessionStorage\b",
    "IndexedDB": r"\bindexedDB\b",
    "cookies": r"\bdocument\.cookie\b",
    "fetch": r"\bfetch\s*\(",
    "XMLHttpRequest": r"\bXMLHttpRequest\b",
    "WebSocket": r"\bnew\s+WebSocket\b",
    "EventSource": r"\bnew\s+EventSource\b",
    "sendBeacon": r"\bsendBeacon\b",
    "RTCPeerConnection": r"\b(?:webkit)?RTCPeerConnection\b|\bRTCDataChannel\b",
    "service worker": r"serviceWorker\.register",
    "eval": r"\beval\s*\(|\bnew\s+Function\s*\(",
    "window.open": r"\bwindow\.open\s*\(",
    "file picker": r"show(?:Open|Save|Directory)FilePicker|showDirectoryPicker",
    "file button": r"type\s*=\s*[\"']?file",
    "geolocation": r"navigator\.geolocation",
    "camera or microphone": r"getUserMedia",
    "notifications": r"\bNotification\b\s*\.\s*requestPermission",
    "postMessage": r"\bpostMessage\s*\(",
    "dialogs": r"\b(?:confirm|alert|prompt)\s*\(",
    "canvas": r"getContext\s*\(\s*[\"'](?:2d|webgl2?|bitmaprenderer)[\"']",
    "drag and drop": r"\bdraggable\s*=|\bondragstart\b|addEventListener\(\s*[\"']drag(?:start|over)[\"']|\bdataTransfer\b",
}

DYN_RE = re.compile(r"""\b(?:fetch|new\s+WebSocket|new\s+EventSource|sendBeacon)\s*\(\s*(?![\s"'`])""")
LS_KEY_RE = re.compile(r"""localStorage\.(?:getItem|setItem|removeItem)\(\s*["'`]([^"'`]{1,80})["'`]""")
LS_CONST_RE = re.compile(r"""localStorage\.(?:getItem|setItem|removeItem)\(\s*([A-Za-z_$][A-Za-z0-9_$]{1,40})\s*[,)]""")
LS_INDEX_RE = re.compile(r"""localStorage\s*\[\s*["'`]([^"'`]{1,80})["'`]\s*\]""")


class _Html(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.refs: list[tuple[str, str]] = []
        self.inline: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.title = ""
        self._in: list[str] = []
        self.controls = 0
        self.scripts = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        rel = a.get("rel", "").lower()
        if tag in ("script", "style", "title"):
            self._in.append(tag)
        editable = "contenteditable" in a and a["contenteditable"].lower() in ("", "true")
        if tag in ("input", "button", "select", "textarea", "canvas") or editable \
                or a.get("onclick") or a.get("role", "").lower() in ("button", "slider", "switch"):
            self.controls += 1
        if tag == "script":
            self.scripts += 1
        if tag == "script" and a.get("src"):
            self.refs.append(("code", a["src"]))
        elif tag == "link" and a.get("href"):
            if "stylesheet" in rel:
                self.refs.append(("look", a["href"]))
            elif rel in ("preconnect", "dns-prefetch", "prefetch", "preload", "modulepreload"):
                self.refs.append(("early", a["href"]))
            elif "icon" in rel or "manifest" in rel:
                self.refs.append(("picture", a["href"]))
        elif tag in ("img", "source", "video", "audio", "track"):
            for k in ("src", "poster"):
                if a.get(k):
                    self.refs.append(("picture", a[k]))
            if a.get("srcset"):
                for part in a["srcset"].split(","):
                    self.refs.append(("picture", part.strip().split(" ")[0]))
        elif tag == "iframe" and a.get("src"):
            self.refs.append(("frame", a["src"]))
        elif tag == "a" and a.get("href"):
            self.refs.append(("link", a["href"]))
        elif tag == "form" and a.get("action"):
            self.refs.append(("form", a["action"]))
        if a.get("style"):
            self.inline.append(("css", a["style"]))
        for k in ("placeholder", "title", "aria-label", "alt"):
            if a.get(k):
                self.text.append(a[k])

    def handle_endtag(self, tag: str) -> None:
        if self._in and self._in[-1] == tag:
            self._in.pop()

    def handle_data(self, data: str) -> None:
        where = self._in[-1] if self._in else ""
        if where == "script":
            self.inline.append(("js", data))
        elif where == "style":
            self.inline.append(("css", data))
        elif where == "title":
            self.title += data
        elif data.strip():
            self.text.append(data.strip())


def _host(url: str) -> str:
    m = re.match(r"^(?:[a-z]+:)?//([^/:?#]+)", url.strip(), re.I)
    return m.group(1).lower() if m else ""


def _pinned(url: str) -> bool:
    if "@latest" in url or re.search(r"@\^|@~", url):
        return False
    return bool(re.search(r"@\d+\.\d+\.\d+", url) or re.search(r"/\d+\.\d+\.\d+/", url))


def _css_urls(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r"@import\s+[\"']([^\"']+)[\"']", text):
        out.append(("look", m.group(1)))
    for m in re.finditer(r"url\(\s*[\"']?([^\"')]+)[\"']?\s*\)", text):
        u = m.group(1)
        kind = "lettering" if re.search(r"\.(woff2?|ttf|otf|eot)\b|fonts\.", u) else "picture"
        out.append((kind, u))
    return out


def _js_urls(text: str) -> list[tuple[str, str]]:
    out = []
    stripped = COMMENT_JS.sub(" ", text)
    for m in URL_RE.finditer(stripped):
        u = m.group("url")
        if NAMESPACE_URLS.match(u if u.startswith("http") else "https:" + u):
            continue
        before = stripped[max(0, m.start() - 160):m.start()]
        if NAMESPACE_BEFORE.search(before[-60:]) and not re.search(
                r"(fetch|XMLHttpRequest|\.open|WebSocket|EventSource|sendBeacon|axios|import)\s*\(?"
                r"\s*[\"'`]*$", before[-60:]):
            continue
        if re.search(r"(fetch|XMLHttpRequest|\.open|WebSocket|EventSource|"
                     r"sendBeacon|axios|\burl\s*[:=])", before):
            kind = "data"
        elif re.search(r"@import|fonts\.googleapis|fonts\.gstatic", u + before):
            kind = "lettering"
        elif re.search(r"\.(png|jpe?g|gif|webp|svg|avif)\b", u):
            kind = "picture"
        elif re.search(r"(href|window\.open|location)\s*[:=(]", before[-60:]):
            kind = "link"
        else:
            kind = "maybe"
        out.append((kind, u))
    return out


#: Toolsmith's classification of what a host is for. The consent words come
#: from here (``puffpai.webapps.forward``), never from the app's own text.
FOR_WORDS = {
    "fonts": "lettering",
    "cdn": "parts it loads",
    "api": "fetching or saving data online",
    "auth": "signing in",
    "maps": "maps",
    "": "something the app does online",
}
_AUTH_HOST = re.compile(r"(?:^|[.-])(?:auth|login|signin|accounts?|oauth|sso|identity)(?:[.-]|$)")
_MAPS_HOST = re.compile(r"(?:^|[.-])(?:maps?|tile|tiles|openstreetmap|mapbox|leaflet)(?:[.-]|$)")
_CDN_HOST = re.compile(r"(?:^|[.-])(?:cdn|cdnjs|jsdelivr|unpkg|esm|skypack|static|assets)(?:[.-]|$)")
_FONT_HOST = re.compile(r"(?:^|[.-])(?:fonts?|typekit)(?:[.-]|$)")


def classify(host: str, uses: set[str]) -> str:
    h = host.lower()
    if _AUTH_HOST.search(h):
        return "auth"
    if _MAPS_HOST.search(h):
        return "maps"
    if "lettering" in uses or _FONT_HOST.search(h):
        return "fonts"
    if uses & {"code", "look", "early"} or _CDN_HOST.search(h):
        return "cdn"
    if uses & {"data", "form"}:
        return "api"
    return ""


PLAIN = {
    "code": "loads program code from {h}",
    "look": "loads its look (styles) from {h}",
    "lettering": "loads lettering from {h}",
    "picture": "shows pictures from {h}",
    "early": "connects ahead of time to {h}",
    "data": "asks {h} for information while you use it",
    "form": "sends a form you fill in to {h}",
    "frame": "shows a page from {h} inside itself",
    "link": "has a link to {h} that you may click",
    "maybe": "mentions {h} in its code (may never contact it)",
    "yours": "can connect to an account you would set up yourself at {h}; off unless you set it up",
}
VENDORABLE = {"code", "look", "lettering", "picture", "early"}
_ORDER = ["data", "form", "yours", "frame", "code", "look", "lettering", "picture",
          "early", "link", "maybe"]


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read(MAX_FILE)


def scan(root: str) -> dict[str, Any]:
    """Scan the app's files under ``root``. Never follows links."""
    refs: list[dict] = []
    features: dict[str, list[str]] = {}
    ls_keys: set[str] = set()
    dynamic: dict[str, int] = {}
    titles: list[str] = []
    texts: list[str] = []
    files = 0
    total = 0
    html_files: list[str] = []
    controls = 0
    script_files = 0
    inline_scripts = 0
    from puffpai import webapps
    for dirpath, dirnames, filenames in webapps.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS
                             and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            if files >= MAX_FILES:
                break
            path = os.path.join(dirpath, fn)
            if os.path.islink(path) or not os.path.isfile(path):
                continue
            ext = os.path.splitext(fn)[1].lower()
            rel = os.path.relpath(path, root).replace("\\", "/")
            size = os.path.getsize(path)
            files += 1
            total += size
            if ext not in TEXT_EXT or size > MAX_FILE:
                continue
            text = _read_text(path)
            found: list[tuple[str, str]] = []
            code_parts: list[str] = []
            if ext in (".html", ".htm"):
                html_files.append(rel)
                p = _Html()
                try:
                    p.feed(text)
                except Exception:  # noqa: BLE001 - a broken page is still scanned by words
                    pass
                found += p.refs
                controls += p.controls
                inline_scripts += sum(1 for kind, body in p.inline if kind == "js" and body.strip())
                if p.title.strip():
                    entry = rel.lower() in ("index.html", "index.htm")
                    titles.insert(0 if entry else len(titles), p.title.strip()[:120])
                texts.extend(t[:200] for t in p.text[:400])
                for kind, body in p.inline:
                    if kind == "js":
                        found += _js_urls(body)
                        code_parts.append(body)
                    else:
                        found += _css_urls(body)
                if re.search(FEATURES["file button"], text):
                    features.setdefault("file button", []).append(rel)
                if re.search(FEATURES["drag and drop"], text):
                    features.setdefault("drag and drop", []).append(rel)
            elif ext == ".css":
                found += _css_urls(text)
            elif ext in (".js", ".mjs", ".cjs"):
                script_files += 1
                found += _js_urls(text)
                found += [(k, u) for k, u in _css_urls(text.replace('\\"', '"'))
                          if u.startswith(("http", "//"))]
                code_parts.append(text)
            for code in code_parts:
                for name, rx in FEATURES.items():
                    if re.search(rx, code):
                        features.setdefault(name, []).append(rel)
                ls_keys.update(LS_KEY_RE.findall(code))
                ls_keys.update(LS_INDEX_RE.findall(code))
                for const in LS_CONST_RE.findall(code):
                    m = re.search(r"\b%s\s*[:=]\s*[\"'`]([^\"'`]{1,80})[\"'`]" % re.escape(const), text)
                    if m:
                        ls_keys.add(m.group(1))
                n = len(DYN_RE.findall(COMMENT_JS.sub(" ", code)))
                if n:
                    dynamic[rel] = dynamic.get(rel, 0) + n
            for kind, url in found:
                h = _host(url)
                if not h:
                    continue
                refs.append({"use": kind, "host": h, "url": url[:200], "file": rel,
                             "pinned": _pinned(url)})

    hosts: dict[str, dict] = {}
    links: set[str] = set()
    for r in refs:
        h = r["host"]
        e = hosts.setdefault(h, {"uses": set(), "urls": set(), "files": set(), "unpinned": False})
        use = "yours" if PLACEHOLDER.search(h) else r["use"]
        e["uses"].add(use)
        e["urls"].add(r["url"])
        e["files"].add(r["file"])
        if use == "code" and not r["pinned"]:
            e["unpinned"] = True
        if r["use"] == "link":
            u = r["url"]
            if u.startswith("//"):
                u = "https:" + u
            if re.match(r"^https?://", u, re.I):
                links.add(re.split(r"[?#]", u, maxsplit=1)[0])
    out_hosts = []
    for h, e in sorted(hosts.items()):
        uses = sorted(e["uses"])
        main = next(u for u in _ORDER if u in e["uses"])
        runtime = main in ("data", "form", "frame")
        out_hosts.append({
            "host": h, "uses": uses, "main_use": main,
            "for": classify(h, e["uses"]),
            "needed_while_running": runtime,
            "save_copy_when_adding": main in VENDORABLE,
            "unpinned_code": e["unpinned"],
            "urls": sorted(e["urls"])[:5], "files": sorted(e["files"])[:5],
            "owner_line": PLAIN[main].format(h=h),
        })
    feats = {k: sorted(set(v))[:5] for k, v in sorted(features.items())}
    uses_ls = "localStorage" in feats
    uses_idb = "IndexedDB" in feats
    return {
        "schema": SCHEMA,
        "files": files, "bytes": total, "html_files": html_files[:50],
        "title": titles[0] if titles else "",
        "text_sample": texts[:400],
        "hosts": out_hosts,
        "needs_internet_while_running": sorted(
            h["host"] for h in out_hosts if h["needed_while_running"]),
        "parts_to_save": sorted(h["host"] for h in out_hosts if h["save_copy_when_adding"]),
        "features": feats,
        "direct_connections": "RTCPeerConnection" in feats,
        "localStorage_keys": sorted(ls_keys),
        "keeps_data": bool(uses_ls or uses_idb or ls_keys),
        "controls": controls,
        "interactive": bool(controls or script_files or inline_scripts),
        "indexeddb_only": bool(uses_idb and not uses_ls and not ls_keys),
        "network_calls_with_runtime_address": dynamic,
        "maybe_runtime_hosts": maybe_runtime_hosts(out_hosts, dynamic),
        "links": sorted(links)[:200],
    }


def maybe_runtime_hosts(hosts: list[dict], dynamic: dict[str, int]) -> list[str]:
    """Hosts the scan saw only as a mention ("maybe") in a file whose network
    calls build their address while running (``var u = "https://h/..." + q;
    fetch(u)``). The static scan cannot prove the app reaches them, so they
    are never registered on their own; they become setup rows (asked once,
    with the build's one yes) instead of being dropped."""
    files = {f for f, n in (dynamic or {}).items() if n}
    out = []
    for h in hosts or []:
        if h.get("main_use") != "maybe" or h.get("needed_while_running"):
            continue
        if files & set(h.get("files") or []):
            out.append(str(h["host"]))
    return sorted(set(out))


#: The one owner line for an outside address an app may need (plain words).
ALLOW_WORDS = "Allow {app} to reach {host}"
RUNTIME_ONLY_WORDS = ("{app} builds some web addresses while it runs. BuildBot asks you "
                      "once, by name, the first time it reaches one.")


def outside_rows(result: dict[str, Any], app: str) -> list[dict[str, Any]]:
    """Setup rows for an app's outside addresses, from one :func:`scan` result.

    One row per host the app needs while running and per "maybe" host in a
    file whose calls build their address while running, each ``{"kind":
    "outside_address", "app", "host", "words", "for", "learned", "sure"}``;
    plus one ``runtime_ask`` row when the app builds addresses but no host
    could be named. Words come from Toolsmith's classification, never from
    the app's own text. Nothing here opens anything."""
    rows: list[dict[str, Any]] = []
    name = str(app or "This app")[:80]
    by_host = {h.get("host"): h for h in result.get("hosts") or [] if isinstance(h, dict)}
    seen: set[str] = set()
    wanted = list(result.get("needs_internet_while_running") or [])
    wanted += list(result.get("maybe_runtime_hosts") or [])
    for host in wanted:
        if not isinstance(host, str) or host in seen or PLACEHOLDER.search(host):
            continue
        seen.add(host)
        h = by_host.get(host) or {}
        sure = bool(h.get("needed_while_running"))
        rows.append({"kind": "outside_address", "app": name, "host": host,
                     "words": ALLOW_WORDS.format(app=name, host=host),
                     "for": FOR_WORDS.get(str(h.get("for") or ""), FOR_WORDS[""]),
                     "learned": "scan" if sure else "scan_maybe", "sure": sure})
    if not rows and any((result.get("network_calls_with_runtime_address") or {}).values()):
        rows.append({"kind": "runtime_ask", "app": name, "host": "",
                     "words": RUNTIME_ONLY_WORDS.format(app=name),
                     "for": FOR_WORDS[""], "learned": "scan_runtime", "sure": False})
    return rows


__all__ = ["ALLOW_WORDS", "FOR_WORDS", "SCHEMA", "classify", "maybe_runtime_hosts",
           "outside_rows", "scan"]
