"""Save, once, the parts a browser app loads from the internet (code, looks, lettering).

Runs while Toolsmith adds the app, after the owner's yes to adding it; never
while the app runs. Every download goes through the one audited exit
(``source_fetch.fetch_small`` with ``service="app_parts"``): https
only, port 443, public addresses only, only the host the part is on, the
owner's switch for "parts an app loads" honoured, and a line in the outside
log for each.

What it does, for the app's files under ``<apps root>/<id>/web/``:

* every script, style sheet, lettering file and picture the app's pages or
  style sheets load from another site (what :mod:`puffpai.webapps.scan` calls
  ``code``, ``look``, ``lettering``, ``picture``) is downloaded once and
  saved under ``web/__vendor__/`` by its SHA-256 (so a ``@latest`` address is
  pinned by what was actually saved);
* a saved style sheet's own ``url(...)`` parts (lettering, pictures) are
  saved the same way and the sheet is rewritten to point at the saved copies;
* the app's pages, scripts and style sheets are rewritten to load the saved
  copies (``/__vendor__/<file>``); "connect ahead" hints to other sites are
  removed (they load nothing);
* ``<state>/webapps/<id>/vendor.json`` lists every saved part with its address,
  size, SHA-256 and SRI (sha384) so Drawbridge and the owner can see exactly
  what was saved. The app's fingerprint (``app_sha``) covers the saved copies
  because they are inside the app's folder.

Anything that cannot be saved (http only, too big, refused, not found) is
reported; the app then stays held with a plain line (nothing half-saved is
served, because the caller re-scans and still finds the outside part).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from puffpai import webapps
from puffpai.webapps import scan as _scan

VENDOR_DIR = "__vendor__"
MAX_PART_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 40 * 1024 * 1024
MAX_PARTS = 80
SERVICE = "app_parts"
REWRITE_EXT = {".html", ".htm", ".js", ".mjs", ".cjs", ".css"}
_PART_USES = {"code", "look", "lettering", "picture"}
_EXT_RE = re.compile(r"\.(js|mjs|css|woff2?|ttf|otf|eot|png|jpe?g|gif|svg|webp|avif|ico)$", re.I)
_EARLY_LINK = re.compile(
    r"<link\b[^>]*\brel\s*=\s*[\"']?(?:preconnect|dns-prefetch|prefetch|preload|modulepreload)"
    r"[\"']?[^>]*>", re.I)
_CSS_URL = re.compile(r"url\(\s*([\"']?)([^\"')]+)\1\s*\)")
_CSS_IMPORT = re.compile(r"@import\s+(?:url\(\s*)?[\"']([^\"']+)[\"']\s*\)?")


def _fetch(url: str, host: str) -> dict:
    """One part through the audited exit (tests replace this)."""
    sf = webapps._optional("puffpai.source_fetch")
    if sf is None or not hasattr(sf, "fetch_small"):
        return {"body": None, "reason": "not_in_this_build"}
    return sf.fetch_small(url, allowed_hosts={host}, max_bytes=MAX_PART_BYTES,
                          service=SERVICE, honour_robots=False)


def _https(url: str) -> Optional[str]:
    u = url.strip()
    if u.startswith("//"):
        u = "https:" + u
    parts = urlsplit(u)
    if parts.scheme.lower() != "https" or not parts.hostname:
        return None
    if parts.username or parts.password or (parts.port not in (None, 443)):
        return None
    return u


def _ext(url: str, ctype: str) -> str:
    m = _EXT_RE.search(urlsplit(url).path)
    if m:
        return "." + m.group(1).lower()
    ctype = (ctype or "").lower()
    for word, ext in (("css", ".css"), ("javascript", ".js"), ("woff2", ".woff2"), ("woff", ".woff"),
                      ("font/ttf", ".ttf"), ("png", ".png"), ("jpeg", ".jpg"), ("svg", ".svg"),
                      ("webp", ".webp"), ("gif", ".gif")):
        if word in ctype:
            return ext
    return ".bin"


def part_urls(web: str) -> list[dict]:
    """Every outside part the app's own pages and style sheets load: ``{use, url, file}``."""
    out: list[dict] = []
    for dirpath, dirnames, files in webapps.walk(web):
        dirnames[:] = [d for d in dirnames if d != VENDOR_DIR and d not in _scan.SKIP_DIRS]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in (".html", ".htm", ".css", ".js", ".mjs", ".cjs"):
                continue
            path = os.path.join(dirpath, fn)
            rel = os.path.relpath(path, web).replace("\\", "/")
            text = _scan._read_text(path)
            refs: list[tuple[str, str]] = []
            if ext == ".css":
                refs = _scan._css_urls(text)
            elif ext in (".js", ".mjs", ".cjs"):
                # only pictures and lettering a script names, and style sheets a
                # bundled sheet imports (as the scan reads them); never what it
                # asks for data
                refs = [(k, u) for k, u in _scan._js_urls(text) if k in ("picture", "lettering")]
                refs += [(k, u) for k, u in _scan._css_urls(text.replace('\\"', '"'))
                         if u.startswith(("http", "//"))]
            else:
                p = _scan._Html()
                try:
                    p.feed(text)
                except Exception:  # noqa: BLE001 - a broken page is still read by its tags
                    pass
                refs = [(k, u) for k, u in p.refs if k in _PART_USES]
                for kind, body in p.inline:
                    if kind == "css":
                        refs += _scan._css_urls(body)
            for kind, url in refs:
                if kind in _PART_USES and _scan._host(url):
                    out.append({"use": kind, "url": url, "file": rel})
    return out


def _sri(body: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(body).digest()).decode("ascii")


class _Saver:
    def __init__(self, web: str) -> None:
        self.web = web
        self.dir = os.path.join(web, VENDOR_DIR)
        self.items: list[dict] = []
        self.failed: list[dict] = []
        self.total = 0
        self.by_url: dict[str, str] = {}

    def save(self, url: str, *, depth: int = 0) -> Optional[str]:
        """The saved copy's served path for ``url``, or None (reported)."""
        full = _https(url)
        if full is None:
            self.failed.append({"url": url[:200], "why": "not_encrypted"})
            return None
        if full in self.by_url:
            return self.by_url[full]
        if len(self.items) >= MAX_PARTS:
            self.failed.append({"url": full[:200], "why": "too_many"})
            return None
        host = (urlsplit(full).hostname or "").lower()
        got = _fetch(full, host)
        body = got.get("body")
        status = got.get("status")
        if got.get("reason") != "ok" or not isinstance(body, (bytes, bytearray)) \
                or (isinstance(status, int) and not 200 <= status < 300) or got.get("truncated"):
            self.failed.append({"url": full[:200], "why": str(got.get("reason") or "no_answer")[:40]
                                if got.get("reason") != "ok" else f"status {status}"})
            return None
        body = bytes(body)
        ctype = str((got.get("headers") or {}).get("content-type") or "")
        ext = _ext(full, ctype)
        if ext == ".css" and depth < 2:
            body = self._css_parts(body, full, depth).encode("utf-8")
        if self.total + len(body) > MAX_TOTAL_BYTES:
            self.failed.append({"url": full[:200], "why": "too_big"})
            return None
        sha = hashlib.sha256(body).hexdigest()
        name = sha[:16] + ext
        os.makedirs(self.dir, exist_ok=True)
        dest = os.path.join(self.dir, name)
        if not os.path.exists(dest):
            with open(dest, "xb") as fh:
                fh.write(body)
        self.total += len(body)
        served = f"/{VENDOR_DIR}/{name}"
        self.by_url[full] = served
        self.items.append({"asked": full[:300], "host": host, "file": name, "bytes": len(body),
                           "sha256": sha, "sri": _sri(body)})
        return served

    def _css_parts(self, body: bytes, base: str, depth: int) -> str:
        css = body.decode("utf-8", "replace")

        def sub_url(m: re.Match) -> str:
            raw = m.group(2).strip()
            if raw.startswith(("data:", "#")):
                return m.group(0)
            local = self.save(urljoin(base, raw), depth=depth + 1)
            return f"url({local})" if local else m.group(0)

        def sub_import(m: re.Match) -> str:
            local = self.save(urljoin(base, m.group(1).strip()), depth=depth + 1)
            return f'@import "{local}"' if local else m.group(0)

        css = _CSS_IMPORT.sub(sub_import, css)
        return _CSS_URL.sub(sub_url, css)


_HREF = re.compile(r"href\s*=\s*[\"']?([^\"'\s>]+)", re.I)


def _drop_outside_hint(m: re.Match) -> str:
    """A connect-ahead or load-ahead hint to another site is dropped; one to
    the app's own files (no host) stays."""
    href = _HREF.search(m.group(0))
    return "" if href and _scan._host(href.group(1)) else m.group(0)


def _rewrite(web: str, mapping: dict[str, str]) -> int:
    """Point the app's own files at the saved copies; drop connect-ahead hints."""
    pairs = sorted(mapping.items(), key=lambda kv: -len(kv[0]))
    changed = 0
    for dirpath, dirnames, files in webapps.walk(web):
        dirnames[:] = [d for d in dirnames if d != VENDOR_DIR]
        for fn in files:
            ext = os.path.splitext(fn)[1].lower()
            if ext not in REWRITE_EXT:
                continue
            path = os.path.join(dirpath, fn)
            with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh:
                text = fh.read()
            new = text
            for asked, local in pairs:
                new = new.replace(asked, local)
                if asked.startswith("https:"):
                    new = new.replace(asked[len("https:"):], local)     # protocol-relative form
            if ext in (".html", ".htm"):
                new = _EARLY_LINK.sub(_drop_outside_hint, new)
            if new != text:
                with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as fh:
                    fh.write(new)
                changed += 1
    return changed


def save_parts(app_id: str, web: str, *, progress: Callable[[str], None] = lambda _l: None) -> dict:
    """Save every outside part the app loads and point the app at the copies.

    ``{"ok": bool, "saved": n, "failed": [{"url", "why"}], "items": [...]}``;
    ``ok`` only when every part was saved. The list is kept in
    ``<state>/webapps/<id>/vendor.json``.
    """
    if not webapps.valid_app_id(app_id):
        raise ValueError("not an app id")
    web = os.path.realpath(web)
    if os.path.normcase(web) != os.path.normcase(os.path.realpath(webapps.web_dir(app_id))):
        raise ValueError("only the app's own folder is changed")
    if os.path.lexists(os.path.join(web, VENDOR_DIR)) and not os.path.isdir(
            os.path.join(web, VENDOR_DIR)) or webapps.is_link(os.path.join(web, VENDOR_DIR)):
        return {"ok": False, "saved": 0, "failed": [{"url": "", "why": "name_taken"}], "items": []}
    saver = _Saver(web)
    wanted = part_urls(web)
    if wanted:
        progress("Toolsmith is saving a copy of the parts this app loads from the internet.")
    mapping: dict[str, str] = {}
    for ref in wanted:
        local = saver.save(ref["url"])
        if local:
            mapping[ref["url"]] = local
            full = _https(ref["url"])
            if full:
                mapping[full] = local
    _rewrite(web, mapping)
    record = {"schema": "puffpai_webapp_vendor/v1", "app_id": app_id, "items": saver.items,
              "failed": saver.failed[:50]}
    path = os.path.join(webapps.app_state_dir(app_id), "vendor.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=1)
    os.replace(tmp, path)
    return {"ok": not saver.failed, "saved": len(saver.items), "failed": saver.failed[:50],
            "items": saver.items}


__all__ = ["save_parts", "part_urls", "VENDOR_DIR", "SERVICE"]
