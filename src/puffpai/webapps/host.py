"""The app host: each integrated browser app on its own address.

One server on ``127.0.0.1:<webapps.port>`` (default 4271), started with the
backend. Every app gets ``http://<id>.localhost:<port>/``; every throwaway
check copy gets ``http://chk-<16 random>.localhost:<port>/``. Loopback only;
there is no home-network mode.

What it enforces, in the order a request meets it:

1. **Host allowlist.** The ``Host`` header must be exactly
   ``<label>.localhost:<port>`` for an installed app or a live check copy.
   Anything else gets 421 (the DNS-rebinding defence).
2. **Frame only.** A top-level page load (``Sec-Fetch-Dest: document``) is
   refused with ``This app opens inside your bot's dashboard.`` unless it
   carries ``X-PuffPAI-Runner: <per-boot secret>``, which only the bot's own
   hidden browser sends (set through the DevTools pipe, never visible to page
   script). An HTML page is served only to a frame, to the runner, or to the
   app's own same-origin script; a request with no browser headers at all
   (a script, a probe) gets the same refusal. (Stricter than the pinned rule,
   which names only ``document``.)
3. **The app's files** from ``<apps root>/<id>/web/`` only: the raw path may
   not hold ``%5c``, ``%2f``, ``%00``, a backslash, a colon (``::$DATA``), an 8.3
   short name (``~1``), a component ending in a dot or a space, a device name,
   or ``..``; the resolved path must stay inside the app's folder.
4. **``/__puffpai/*``** (the shim, the storage copy, reports, the forwarding
   address): every request needs ``Sec-Fetch-Site: same-origin`` or the runner
   secret; every non-GET needs ``Origin`` equal to the app's own origin; every
   state-changing call (``/out`` any method, storage PUT, report POST, undo)
   needs ``X-PuffPAI-Nonce`` equal to a per-page nonce (128-bit, bound to the
   app, one hour) that the host puts into ``window.__PUFFPAI__`` when it serves
   the page. ``Access-Control-Allow-Origin`` is never sent.
5. **A removed app** (:func:`mark_removed`) answers exactly one thing for 30
   days: ``GET /__puffpai/bye`` loaded as a frame, with
   ``Clear-Site-Data: "cache", "cookies", "storage", "executionContexts"``, so
   the owner's dashboard can make her browser forget what the app kept there.
   Everything else at its address is 421, as for any unknown site. Only an
   app that is already removed can be cleared this way; an installed app's
   data is never cleared by a request from outside.

6. **No service worker, no SVG or XML page**: a
   service worker's script is never served (one could answer the app's own
   pages with no Content-Security-Policy at all, measured), and an SVG or XML
   file is never served as a page (it would run script without the guard).
7. **Every HTML page** has its resource hints and Speculation Rules made
   inert and every frame tag in it made inert
   (:func:`neutralise_hints`), and gets ``runner/guard.js`` as its very first
   script, before the page's settings and the shim (:func:`inject`). No
   answer carries ``Link`` or ``Speculation-Rules`` (:data:`NEVER_HEADERS`).

The Content-Security-Policy on every answer (:mod:`puffpai.webapps.headers`)
is the wall, with the guard for what no CSP covers; the shim only reroutes.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import base64
import collections
import hmac
import json
import os
import re
import secrets
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

from puffpai import webapps
from puffpai.webapps import headers as _headers
from puffpai.webapps import mirror, wording

NONCE_TTL_S = 3600
#: How long a removed app's address still answers ``/__puffpai/bye``.
REMOVED_KEEP_S = 30 * 24 * 3600
MAX_REMOVED = 500
CLEAR_SITE_DATA = '"cache", "cookies", "storage", "executionContexts"'
BYE_PAGE = (b"<!doctype html><html><head><meta charset=\"utf-8\"><title>Removed</title></head>"
            b"<body></body></html>")
MAX_NONCES_PER_LABEL = 500
REPORTS_PER_MINUTE = 30
MAX_STATIC_BYTES = 50 * 1024 * 1024
MAX_PATH_CHARS = 2048
#: A refused request's body up to this size is read and dropped before the
#: answer, so closing the connection never resets it before the page reads the
#: answer (Windows resets a socket closed with unread data).
DRAIN_MAX_BYTES = 64 * 1024
RUNNER_HEADER = "X-PuffPAI-Runner"
NONCE_HEADER = "X-PuffPAI-Nonce"

_SHIM_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner", "shim.js")
_GUARD_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "runner", "guard.js")
#: Files that may hold script of their own and are never served as a page
#: (an SVG or XML page would run without the guard).
_NEVER_PAGES = ("image/svg+xml", "application/xml")
_PAGE_DESTS = ("document", "iframe", "frame", "object", "embed", "fencedframe")

_TYPES = {
    ".html": "text/html", ".htm": "text/html", ".js": "text/javascript",
    ".mjs": "text/javascript", ".cjs": "text/javascript", ".css": "text/css",
    ".json": "application/json", ".map": "application/json",
    ".webmanifest": "application/manifest+json", ".svg": "image/svg+xml",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".avif": "image/avif",
    ".ico": "image/x-icon", ".bmp": "image/bmp", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".otf": "font/otf",
    ".wasm": "application/wasm", ".txt": "text/plain", ".md": "text/plain",
    ".csv": "text/csv", ".xml": "application/xml", ".mp3": "audio/mpeg",
    ".wav": "audio/wav", ".ogg": "audio/ogg", ".mp4": "video/mp4",
    ".webm": "video/webm", ".pdf": "application/pdf",
}
_TEXT_TYPES = ("text/html", "text/css", "text/javascript", "application/json",
               "text/plain", "text/csv", "application/xml",
               "application/manifest+json", "image/svg+xml")

_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9]|lpt[0-9]|conin\$|conout\$)(\..*)?$", re.I)
_BAD_RAW = re.compile(r"%(?:5c|2f|00|25)|\\", re.I)
_BAD_CHARS = re.compile(r'[\x00-\x1f\x7f\\:*?"<>|]')
_SHORT_NAME = re.compile(r"~\d")

_LOCK = threading.RLock()
_state: dict[str, Any] = {
    "server": None, "thread": None, "port": None, "dash_port": None,
    "secret": None, "apps": None,
}
_checks: dict[str, dict] = {}          # label -> {"app_id", "made"}
_issued_checks: set[str] = set()       # never handed out twice in this process
_nonces: dict[str, tuple[str, float]] = {}
_nonce_order: dict[str, collections.deque] = {}
_reports: dict[str, collections.deque] = {}


# ---------------------------------------------------------------------------
# labels, origins, secrets
# ---------------------------------------------------------------------------

def port() -> int:
    with _LOCK:
        running = _state["port"]
    return running if running else webapps.configured_port()


def origin_for(app_id: str) -> str:
    """``http://<app_id>.localhost:<webapps.port>`` (also for a check copy label)."""
    if not webapps.valid_label(app_id):
        raise ValueError("not an app id")
    return f"http://{app_id}.localhost:{port()}"


def host_for(label: str) -> str:
    return f"{label}.localhost:{port()}"


def runner_secret() -> Optional[str]:
    """The per-boot secret only the runner sends. None while the host is down."""
    with _LOCK:
        return _state["secret"]


def _record_path(app_id: str) -> str:
    return os.path.join(webapps.app_state_dir(app_id), "web.json")


def read_record(app_id: str) -> Optional[dict]:
    if not webapps.valid_app_id(app_id):
        return None
    try:
        with open(_record_path(app_id), encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("app_id") != app_id:
        return None
    return rec


def installed_ids() -> list[str]:
    """Apps whose record says installed. Cached; :func:`refresh` re-reads."""
    with _LOCK:
        cached = _state["apps"]
        if cached is not None:
            return list(cached)
    found: list[str] = []
    root = webapps.state_root()
    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    gone = removed_ids()
    for name in names:
        if not webapps.valid_app_id(name) or name in gone:
            continue
        rec = read_record(name)
        if rec and rec.get("status") == "installed":
            found.append(name)
    with _LOCK:
        _state["apps"] = list(found)
    return found


def refresh() -> None:
    with _LOCK:
        _state["apps"] = None


def _removed_path() -> str:
    return os.path.join(webapps.state_root(), "_removed.json")


def removed_ids() -> dict[str, float]:
    """Apps removed in the last 30 days: ``{app_id: removed_at}``."""
    try:
        with open(_removed_path(), encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return {}
    now = time.time()
    out: dict[str, float] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            if webapps.valid_app_id(k) and isinstance(v, (int, float)) \
                    and 0 <= now - float(v) <= REMOVED_KEEP_S:
                out[k] = float(v)
    return out


def mark_removed(app_id: str) -> None:
    """Remember a removed app so its address answers only ``/__puffpai/bye``."""
    if not webapps.valid_app_id(app_id):
        raise ValueError("not an app id")
    with _LOCK:
        rec = removed_ids()
        rec[app_id] = time.time()
        if len(rec) > MAX_REMOVED:
            rec = dict(sorted(rec.items(), key=lambda kv: kv[1])[-MAX_REMOVED:])
        path = _removed_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        os.replace(tmp, path)
        _state["apps"] = None


def new_check_copy(app_id: str) -> str:
    """A throwaway copy of ``app_id`` at a never-reused address, empty storage."""
    if not webapps.valid_app_id(app_id):
        raise ValueError("not an app id")
    with _LOCK:
        while True:
            label = "chk-" + base64.b32encode(secrets.token_bytes(10)).decode("ascii").lower()
            if label not in _issued_checks and not webapps.ID_RE.fullmatch(label):
                break
        _issued_checks.add(label)
        _checks[label] = {"app_id": app_id, "made": time.time()}
    return label


def drop_check_copy(label: str) -> None:
    with _LOCK:
        _checks.pop(label, None)
        for nonce in list(_nonce_order.pop(label, ())):
            _nonces.pop(nonce, None)
        _reports.pop(label, None)
    try:
        mirror.clear(label)
    except (ValueError, OSError):
        pass


def check_copies() -> dict[str, str]:
    with _LOCK:
        return {label: rec["app_id"] for label, rec in _checks.items()}


def app_of(label: str) -> Optional[str]:
    """The app a label serves, or None when the label is not served now."""
    if webapps.CHECK_RE.fullmatch(label or ""):
        with _LOCK:
            rec = _checks.get(label)
        return rec["app_id"] if rec else None
    if webapps.valid_app_id(label) and label in installed_ids():
        return label
    return None


def _issue_nonce(label: str) -> str:
    nonce = secrets.token_hex(16)             # 128 bits
    now = time.time()
    with _LOCK:
        order = _nonce_order.setdefault(label, collections.deque())
        while order and (len(order) >= MAX_NONCES_PER_LABEL
                         or _nonces.get(order[0], ("", 0.0))[1] < now):
            _nonces.pop(order.popleft(), None)
        _nonces[nonce] = (label, now + NONCE_TTL_S)
        order.append(nonce)
    return nonce


def nonce_ok(label: str, value: Optional[str]) -> bool:
    if not value or not re.fullmatch(r"[0-9a-f]{32}", value):
        return False
    with _LOCK:
        entry = _nonces.get(value)
    if not entry:
        return False
    bound, expires = entry
    return hmac.compare_digest(bound, label) and expires >= time.time()


def _report_allowed(label: str) -> bool:
    now = time.time()
    with _LOCK:
        q = _reports.setdefault(label, collections.deque())
        while q and q[0] < now - 60:
            q.popleft()
        if len(q) >= REPORTS_PER_MINUTE:
            return False
        q.append(now)
    return True


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def safe_rel_path(raw_path: str) -> Optional[list[str]]:
    """The path's components, or None when the raw request path is refused.

    Refused: ``%5c`` ``%2f`` ``%00`` ``%25`` or a backslash in the raw path;
    after decoding, a colon (``::$DATA``), a control or wildcard character,
    ``.`` or ``..``, an empty component, an 8.3 short name (``PROGRA~1``), a
    component ending in a dot or a space, a device name, or a component over
    255 characters. An empty path means the entry page.
    """
    if not isinstance(raw_path, str) or not raw_path.startswith("/"):
        return None
    if len(raw_path) > MAX_PATH_CHARS or _BAD_RAW.search(raw_path):
        return None
    try:
        decoded = unquote(raw_path, errors="strict")
    except UnicodeDecodeError:
        return None
    if _BAD_CHARS.search(decoded):
        return None
    body = decoded[1:]
    if body == "":
        return []
    trailing_slash = body.endswith("/")
    if trailing_slash:
        body = body[:-1]
    parts = body.split("/")
    for part in parts:
        if part in ("", ".", "..") or len(part) > 255:
            return None
        if part.endswith(".") or part.endswith(" ") or part.startswith(" "):
            return None
        if _SHORT_NAME.search(part) or _RESERVED.match(part):
            return None
    return parts


def _within(root: str, candidate: str) -> bool:
    r = os.path.normcase(root)
    c = os.path.normcase(candidate)
    return c == r or c.startswith(r.rstrip("\\/") + os.sep)


def resolve_file(label: str, raw_path: str) -> Optional[str]:
    """The real file to serve for ``raw_path``, confined to the app's folder."""
    parts = safe_rel_path(raw_path)
    app_id = app_of(label)
    if parts is None or app_id is None:
        return None
    root = os.path.realpath(webapps.web_dir(app_id))
    full = os.path.realpath(os.path.join(root, *parts)) if parts else root
    if not _within(root, full):
        return None
    if os.path.isdir(full):
        full = os.path.join(full, "index.html")
    if os.path.islink(full) or not os.path.isfile(full):
        return None
    if not _within(root, os.path.realpath(full)):
        return None
    return full


def _content_type(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return _TYPES.get(ext, "application/octet-stream")


#: label -> the version of the bot's copy last served to (or written by) the
#: runner's own page of it: the base its own writes are checked against.
_runner_base: dict[str, int] = {}


def runner_base(label: str) -> Optional[int]:
    with _LOCK:
        return _runner_base.get(label)


def _set_runner_base(label: str, v: int) -> None:
    with _LOCK:
        _runner_base[label] = int(v)
        if len(_runner_base) > 2000:
            for k in list(_runner_base)[:500]:
                _runner_base.pop(k, None)


def _inline_conf(label: str, nonce: str, runner: bool = False) -> str:
    store = mirror.get(label)
    # The runner's own page never saves through the page: the
    # runner reads the page's storage through DevTools (no script in the
    # page) and saves it itself, so nothing the app's script sends counts as
    # the bot's.
    conf = json.dumps({"app": label, "store": store, "mirror": True, "nonce": nonce,
                       "runner": bool(runner)}, ensure_ascii=False)
    if runner:
        _set_runner_base(label, store["v"])
    return (conf.replace("<", "\\u003c").replace(">", "\\u003e")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


#: Words in a ``<link>`` tag that make the owner's browser reach another
#: address on its own, where the page's Content-Security-Policy does not
#: look (measured: a parsed ``<link rel=prerender>``
#: sent ``GET /prerender?d=...`` to any host from Edge 154 and Chrome 153;
#: ``preconnect`` opened a connection). ``prefetch`` covers ``dns-prefetch``.
_HINT_WORDS = ("prerender", "prefetch", "preconnect", "speculation")
#: What a neutralised tag starts with: an element no browser gives any meaning to.
NEUTRAL_LINK = "<x-puffpai-removed"
NEUTRAL_SCRIPT = '<script type="text/x-puffpai-removed"'
#: Headers an answer never carries beside the fixed ones in
#: :func:`puffpai.webapps.headers.app_headers`: ``Link`` and
#: ``Speculation-Rules`` would give the browser addresses to reach on its own.
NEVER_HEADERS = frozenset({"link", "speculation-rules", "access-control-allow-origin",
                           "x-dns-prefetch-control", "content-security-policy"})


def _tag_end(text: str, start: int) -> int:
    """Where the tag that starts at ``start`` ends (after its ``>``), reading
    quoted values as the HTML parser does; the end of the text when none."""
    i, n, quote = start, len(text), ""
    while i < n:
        ch = text[i]
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'" and i > 0 and text[i - 1] in "= \t\n\r\f":
            quote = ch
        elif ch == ">":
            return i + 1
        i += 1
    return n


def _tag_says(tag: str) -> str:
    """The tag's text as the browser reads it: character references decoded,
    lower case, with no spaces."""
    import html as _html
    return re.sub(r"\s+", "", _html.unescape(tag).lower())


def _outside_href(said: str) -> bool:
    """True when a decoded tag's ``href`` names an address with a scheme or
    starting ``//`` (anything that could be another origin), or is missing."""
    m = re.search(r"href=([\"']?)([^\"'>]*)", said)
    if not m:
        return True
    ref = m.group(2)
    return ref.startswith("//") or ref.startswith("\\") or re.match(r"[a-z][a-z0-9+.\-]*:", ref) is not None


def neutralise_hints(page: str) -> str:
    """The page with every resource hint and Speculation Rules block that the
    browser would act on by itself made inert (fail closed).

    * A ``<link>`` whose tag holds ``prerender``, ``prefetch`` (and so
      ``dns-prefetch``), ``preconnect`` or ``speculation`` anywhere, read the
      way the browser reads it (character references decoded, any case),
      becomes ``<x-puffpai-removed ...>``, which nothing acts on.
    * A ``<link>`` with ``preload`` or ``modulepreload`` pointing at an
      address with a scheme or ``//`` (possibly another site) is made inert the
      same way; one pointing into the app's own folder stays (built apps
      preload their own parts, and the CSP covers those).
    * A ``<script>`` whose tag says ``speculationrules`` gets
      ``type="text/x-puffpai-removed"`` as its first attribute (the HTML
      parser keeps the first of two), so it never runs.
    * ``<iframe>``, ``<frame>`` and ``<frameset>`` are made inert: a frame
      in the page, moved to another address, opened a connection
      there before any policy stopped it (measured, Edge 154), and the app's
      script could move one through ``frames[i].location``, which nothing in
      the page can intercept. The same holds for these tags written with
      character references (the text of a ``srcdoc``).

    Only the tag's name is rewritten, so everything else in the page stays
    as the app wrote it. Every tag start is judged on its own; a tag read
    too widely is only ever made inert, never let through."""
    out: list[str] = []
    pos = 0
    page = _ESCAPED_TAG.sub(_escaped_tag, page)
    for m in re.finditer(r"<(link|script|iframe|frameset|frame)(?=[\s/>])", page, re.I):
        name = m.group(1).lower()
        if name in ("iframe", "frame", "frameset"):
            out.append(page[pos:m.start()])
            out.append(NEUTRAL_LINK)
            pos = m.end()
            continue
        said = _tag_says(page[m.start():_tag_end(page, m.start())])
        if name == "link":
            if any(w in said for w in _HINT_WORDS) or ("preload" in said and _outside_href(said)):
                out.append(page[pos:m.start()])
                out.append(NEUTRAL_LINK)
                pos = m.end()
        elif "speculationrules" in said:
            out.append(page[pos:m.start()])
            out.append(NEUTRAL_SCRIPT)
            pos = m.end()
    out.append(page[pos:])
    return "".join(out)


#: A frame, a link or a frameset written with character references inside an
#: attribute (the text of a ``srcdoc``): made inert or sealed the same way.
_ESCAPED_TAG = re.compile(r"(&lt;|&#0*60;?|&#x0*3c;?)(\s*)(link|iframe|frameset|frame)(?=[\s/>&])", re.I)


def _escaped_tag(m: "re.Match") -> str:
    return m.group(1) + m.group(2) + NEUTRAL_LINK[1:]


def has_hints(text: str) -> bool:
    """True when :func:`neutralise_hints` would change ``text``."""
    return neutralise_hints(text) != text


_DOCTYPE_FIRST = re.compile(r"\A\ufeff?(?:\s|<!--.*?-->)*<!doctype[^>]*>", re.I | re.S)


def inject(label: str, html: str, runner: bool = False) -> str:
    """Put the guard, the page's settings and the shim first in the page:
    right after a leading ``<!doctype>`` (so the page keeps its mode), else at
    the very start, so none of the app's own markup or script comes before
    the guard (a ``<script>`` written before ``<head>`` would)."""
    nonce = _issue_nonce(label)
    tag = ('<script src="/__puffpai/guard.js"></script>'
           f"<script>window.__PUFFPAI__={_inline_conf(label, nonce, runner)};</script>"
           f'<script src="/__puffpai/shim.js"></script>')
    m = _DOCTYPE_FIRST.match(html)
    pos = m.end() if m else 0
    return html[:pos] + tag + html[pos:]


# ---------------------------------------------------------------------------
# the handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    server_version = "puffpai-apps"
    sys_version = ""
    timeout = 20

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: D401 - silence
        return

    # -- replies ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8",
              extra: Optional[dict] = None) -> None:
        self._drain()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in _headers.app_headers(self.server.dash_port).items():
            self.send_header(k, v)
        for k, v in (extra or {}).items():
            if str(k).strip().lower() in NEVER_HEADERS:
                continue
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _text(self, code: int, words: str) -> None:
        self._send(code, (words + "\n").encode("utf-8"), extra={"Cache-Control": "no-store"})

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj).encode("utf-8"), "application/json",
                   {"Cache-Control": "no-store"})

    def _body(self, cap: int) -> Optional[bytes]:
        if self.headers.get("Transfer-Encoding"):
            return None
        raw = self.headers.get("Content-Length")
        if raw is None:
            return b""
        try:
            n = int(raw)
        except ValueError:
            return None
        if n < 0 or n > cap:
            return None
        self._drained = True
        return self.rfile.read(n) if n else b""

    def _drain(self) -> None:
        """Read and drop a body nobody read (small ones only)."""
        if getattr(self, "_drained", True):
            return
        self._drained = True
        if self.headers.get("Transfer-Encoding"):
            return
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return
        if 0 < n <= DRAIN_MAX_BYTES:
            try:
                self.rfile.read(n)
            except (OSError, ValueError):
                pass

    # -- which address is this? -----------------------------------------
    def _named_label(self) -> Optional[str]:
        """The label the exact Host header names on this port, served or not."""
        values = self.headers.get_all("Host") or []
        if len(values) != 1:
            return None
        host = values[0].strip().lower()
        m = re.fullmatch(r"([a-z0-9][a-z0-9-]{0,60})\.localhost:(\d{1,5})", host)
        if not m or int(m.group(2)) != self.server.server_address[1]:
            return None
        label = m.group(1)
        return label if webapps.valid_label(label) else None

    def _label(self) -> Optional[str]:
        label = self._named_label()
        if label is None or app_of(label) is None:
            return None
        return label

    def _from_browser(self) -> bool:
        """True for any request a browser made (it always says what it
        fetches, for a page on a ``localhost`` address); the runner process's
        own requests carry none of these."""
        return any(self.headers.get(h) for h in ("Sec-Fetch-Dest", "Sec-Fetch-Mode",
                                                   "Sec-Fetch-Site", "Sec-Fetch-User"))

    def _is_runner_process(self) -> bool:
        """The runner secret on a request the runner process made itself:
        runner rights on ``/__puffpai/*`` are never given to a
        request from a browser, so no app script in the runner's tab can
        obtain them, whatever header rode on it."""
        return self._is_runner() and not self._from_browser()

    def _is_runner(self) -> bool:
        secret = self.server.secret
        given = self.headers.get(RUNNER_HEADER)
        if not secret or not given:
            return False
        return hmac.compare_digest(given.encode("utf-8", "replace"), secret.encode("ascii"))

    # -- methods ---------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        self._route()

    do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_GET  # noqa: N815

    def _route(self) -> None:
        self._drained = False
        try:
            self._route_inner()
        except (ConnectionError, TimeoutError, socket.timeout):
            return
        except Exception:  # noqa: BLE001 - never show a trace to a page
            try:
                self._text(500, wording.REFUSED)
            except Exception:  # noqa: BLE001
                pass

    def _route_inner(self) -> None:
        label = self._label()
        if label is None:
            return self._bye_or_unknown()
        target = self.path or ""
        if not target.startswith("/") or len(target) > MAX_PATH_CHARS:
            return self._text(400, wording.REFUSED)
        path = target.split("?", 1)[0].split("#", 1)[0]
        if path.startswith("/__puffpai/"):
            return self._puffpai(label, path)
        return self._static(label, path)

    def _bye_or_unknown(self) -> None:
        """A removed app's address answers only its goodbye page (as a frame)."""
        label = self._named_label()
        path = (self.path or "").split("?", 1)[0]
        dest = (self.headers.get("Sec-Fetch-Dest") or "").lower()
        if (label is not None and webapps.valid_app_id(label) and path == "/__puffpai/bye"
                and self.command in ("GET", "HEAD") and dest in ("iframe", "frame")
                and label in removed_ids()):
            return self._send(200, BYE_PAGE, "text/html; charset=utf-8",
                              {"Cache-Control": "no-store", "Clear-Site-Data": CLEAR_SITE_DATA})
        return self._text(421, wording.UNKNOWN_SITE)

    # -- the app's own files ---------------------------------------------
    def _worker_script(self) -> bool:
        """A service worker's script: never served (a service worker could
        answer the app's own pages with no Content-Security-Policy at all)."""
        dest = (self.headers.get("Sec-Fetch-Dest") or "").lower()
        return dest in ("serviceworker", "sharedworker") or bool(self.headers.get("Service-Worker"))

    def _static(self, label: str, raw_path: str) -> None:
        runner = self._is_runner()
        dest = (self.headers.get("Sec-Fetch-Dest") or "").lower()
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if self._worker_script():
            return self._text(403, wording.REFUSED)
        if dest == "document" and not runner:
            return self._text(403, wording.FRAME_ONLY)
        if self.command not in ("GET", "HEAD"):
            return self._text(405, wording.READ_ONLY)
        full = resolve_file(label, raw_path)
        if full is None:
            return self._text(404, wording.NOT_FOUND)
        ctype = _content_type(full)
        if ctype in _NEVER_PAGES and dest in _PAGE_DESTS:
            return self._text(403, wording.REFUSED)
        if ctype == "text/html" and not (
                runner or dest in ("iframe", "frame")
                or (dest == "empty" and site == "same-origin")):
            return self._text(403, wording.FRAME_ONLY)
        try:
            size = os.path.getsize(full)
            if size > MAX_STATIC_BYTES:
                return self._text(404, wording.NOT_FOUND)
            with open(full, "rb") as fh:
                data = fh.read(MAX_STATIC_BYTES + 1)
        except OSError:
            return self._text(404, wording.NOT_FOUND)
        if ctype == "text/html":
            data = inject(label, neutralise_hints(data.decode("utf-8", "replace")),
                          runner=runner).encode("utf-8")
        elif ctype in ("application/xml", "image/svg+xml") and has_hints(
                data.decode("utf-8", "replace")):
            # an XML page with a hint in it is never served (renaming a tag
            # there would break the page anyway)
            return self._text(404, wording.NOT_FOUND)
        if ctype in _TEXT_TYPES:
            ctype += "; charset=utf-8"
        return self._send(200, data, ctype, {"Cache-Control": "no-store"})

    # -- /__puffpai/* ------------------------------------------------------
    def _puffpai(self, label: str, path: str) -> None:
        runner = self._is_runner_process()
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if self._worker_script():
            return self._json(403, {"error": "refused"})
        if not runner and site != "same-origin":
            return self._json(403, {"error": "not_same_origin"})
        method = self.command
        if method not in ("GET", "HEAD"):
            if (self.headers.get("Origin") or "") != origin_for(label):
                return self._json(403, {"error": "origin"})
        if method == "OPTIONS":
            return self._json(405, {"error": "method"})
        if path in ("/__puffpai/shim.js", "/__puffpai/guard.js"):
            if method not in ("GET", "HEAD"):
                return self._json(405, {"error": "method"})
            try:
                with open(_SHIM_PATH if path.endswith("shim.js") else _GUARD_PATH, "rb") as fh:
                    data = fh.read()
            except OSError:
                return self._json(404, {"error": "missing"})
            return self._send(200, data, "text/javascript; charset=utf-8",
                              {"Cache-Control": "no-store"})
        if path == "/__puffpai/storage":
            return self._storage(label, method)
        if path == "/__puffpai/report":
            return self._report(label, method)
        if path == "/__puffpai/out":
            return self._out(label, method)
        if path == "/__puffpai/files" or path.startswith("/__puffpai/files/"):
            return self._files(label, runner)
        return self._json(404, {"error": "not_found"})

    def _needs_nonce(self, label: str) -> bool:
        return nonce_ok(label, self.headers.get(NONCE_HEADER))

    def _storage(self, label: str, method: str) -> None:
        if method in ("GET", "HEAD"):
            cur = mirror.get(label)
            if "v=1" in (urlsplit(self.path).query or "").split("&"):
                return self._json(200, {"v": cur["v"]})
            return self._json(200, cur)
        if method != "PUT":
            return self._json(405, {"error": "method"})
        runner = self._is_runner_process()
        if not runner and not self._needs_nonce(label):
            return self._json(403, {"error": "nonce"})
        raw = self._body(mirror.MAX_BYTES + 64 * 1024)
        if raw is None:
            return self._json(413, {"error": "too_big"})
        try:
            req = json.loads(raw or b"{}")
        except ValueError:
            return self._json(400, {"error": "bad_json"})
        if not isinstance(req, dict):
            return self._json(400, {"error": "bad_json"})
        # Only the runner process's own write is the bot's (never a request
        # from a browser, so never one the app's script made); while
        # a bot action holds the app, every other write waits (423).
        base = req.get("base")
        if runner and base == "served":
            base = runner_base(label)
            if base is None:
                return self._json(409, {"v": mirror.version(label)})
        code, body = mirror.put(label, base, req.get("data"), by="runner" if runner else "page")
        if runner and code == 200:
            _set_runner_base(label, body["v"])
        if code == 200 and body.get("v") == 1 and webapps.valid_app_id(label):
            # the day her copy was first written (never what was written):
            # the app's own dates are told apart by it (webapps.examples)
            from puffpai.webapps import examples
            examples.note_first_write(label)
        return self._json(code, body)

    def _report(self, label: str, method: str) -> None:
        if method != "POST":
            return self._json(405, {"error": "method"})
        if not self._needs_nonce(label):
            return self._json(403, {"error": "nonce"})
        if not _report_allowed(label):
            return self._json(429, {"error": "slow_down"})
        raw = self._body(64 * 1024)
        if raw is None:
            return self._json(413, {"error": "too_big"})
        try:
            rep = json.loads(raw or b"{}")
        except ValueError:
            rep = {}
        if not isinstance(rep, dict):
            rep = {}
        inner = rep.get("csp-report") if isinstance(rep.get("csp-report"), dict) else rep
        uri = str(inner.get("blocked-uri") or inner.get("uri") or "")[:200]
        host = (urlsplit(uri).hostname or "")[:120]
        directive = str(inner.get("effective-directive") or inner.get("violated-directive")
                        or inner.get("directive") or "")[:60]
        mirror.watch(label, kind="blocked", host=host, directive=directive)
        # a report that passed every check above may raise the owner's
        # "wants to reach" question (at most once a day per site, never after No)
        fwd = webapps._optional("puffpai.webapps.forward")
        if fwd is not None and webapps.valid_app_id(label) and host:
            fwd.note_wanted(label, host.rstrip(".").lower())
        return self._send(204, b"", extra={"Cache-Control": "no-store"})

    def _out(self, label: str, method: str) -> None:
        if not self._needs_nonce(label):
            return self._json(403, {"error": "nonce"})
        target = ""
        for part in (urlsplit(self.path).query or "").split("&"):
            if part.startswith("u="):
                target = unquote(part[2:])
                break
        host = (urlsplit(target).hostname or "")[:120]
        fwd = webapps._optional("puffpai.webapps.forward")
        if fwd is None or webapps.CHECK_RE.fullmatch(label) or not hasattr(fwd, "handle"):
            mirror.watch(label, kind="asked", host=host, method=method, reason="switched_off")
            return self._json(403, {"refused": "switched_off", "host": host,
                                    "message": wording.OUTSIDE_NOT_ON})
        return fwd.handle(self, label, method, target)

    def _files(self, label: str, runner: bool) -> None:
        if not runner:
            return self._json(403, {"error": "runner_only"})
        if self.command not in ("GET", "HEAD"):
            return self._json(405, {"error": "read_only"})
        files = webapps._optional("puffpai.webapps.files")
        if files is None or not hasattr(files, "handle"):
            return self._json(404, {"error": "not_in_this_build", "message": wording.NOT_IN_BUILD})
        return files.handle(self, label)


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR on Windows lets another program bind the same port and take
    # the traffic; never set it there, and ask for the port exclusively
    # instead. On Linux and macOS it means something else: it only lets a
    # restarted host take its own port back while old connections are still
    # closing (TIME_WAIT); a port another program is listening on is still
    # refused. Without it, stopping and starting the bot within a minute
    # failed with "address already in use".
    allow_reuse_address = sys.platform != "win32"

    def handle_error(self, request, client_address) -> None:  # noqa: D401 - quiet
        return

    def server_bind(self) -> None:
        if sys.platform == "win32" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


BIND_HOST = "127.0.0.1"


def start(cfg: Any = None) -> None:
    """Start the host on loopback. Idempotent. Raises OSError if the port is taken."""
    with _LOCK:
        if _state["server"] is not None:
            return
        want = webapps.configured_port(cfg)
        srv = _Server((BIND_HOST, want), _Handler)
        srv.dash_port = webapps.dashboard_port(cfg)
        srv.secret = secrets.token_urlsafe(32)
        _state.update(server=srv, port=srv.server_address[1], dash_port=srv.dash_port,
                      secret=srv.secret, apps=None)
        t = threading.Thread(target=srv.serve_forever, name="webapps-host", daemon=True)
        _state["thread"] = t
        t.start()


def stop() -> None:
    with _LOCK:
        srv = _state["server"]
        _state.update(server=None, thread=None, port=None, secret=None, apps=None)
        _nonces.clear()
        _nonce_order.clear()
        _reports.clear()
    if srv is not None:
        srv.shutdown()
        srv.server_close()


def bound_address() -> Optional[tuple]:
    with _LOCK:
        srv = _state["server"]
        return srv.server_address if srv is not None else None


__all__ = ["origin_for", "start", "stop", "runner_secret", "new_check_copy",
           "mark_removed", "removed_ids", "CLEAR_SITE_DATA",
           "drop_check_copy", "installed_ids", "refresh", "safe_rel_path",
           "resolve_file", "nonce_ok", "read_record", "host_for", "port",
           "bound_address", "RUNNER_HEADER", "NONCE_HEADER"]
