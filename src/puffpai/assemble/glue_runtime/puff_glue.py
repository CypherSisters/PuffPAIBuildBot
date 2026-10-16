"""The one helper connecting code may import: ask BuildBot to fetch a page.

Glue never reaches the internet itself. ``fetch_source`` (one of the app's
declared sources), ``fetch_json`` and ``fetch_text`` (an address on a site the
app's sources or found parts name) ask BuildBot on 127.0.0.1 (the gateway in
:mod:`puffpai.assemble.server.gateway`) with this run's own token; BuildBot
fetches through the Bridge under that site's own switch and writes the line
on "What leaves your computer". A key from the vault is added by BuildBot on
the way out (``key="<setting name>"``); glue never sees it.

``csv_rows`` and ``rss_rows`` turn fetched text into rows.

Runs inside the glue process (:mod:`.runner`), which configures it.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import csv as _csv
import io as _io
import json as _json
import urllib.error as _uerr
import urllib.request as _ureq
from xml.etree import ElementTree as _ET

_SEND = {"post": None}
_MAX_FETCHES = 40
MAX_ROWS = 500


class GlueFetchError(RuntimeError):
    """BuildBot did not fetch it; the words say why."""


def _configure(url: str, token: str) -> None:
    """Set by the runner before the glue is loaded. The token is kept only in
    the closure below (never in a module value glue could name)."""
    if not str(url).startswith("http://127.0.0.1:"):
        raise GlueFetchError("the gateway is not on this computer")
    base = str(url).rstrip("/")
    secret = str(token)
    count = [0]

    def post(payload: dict) -> dict:
        count[0] += 1
        if count[0] > _MAX_FETCHES:
            raise GlueFetchError(f"more than {_MAX_FETCHES} fetches in one run")
        body = _json.dumps(payload).encode("utf-8")
        req = _ureq.Request(base + "/fetch", data=body, method="POST",
                            headers={"Content-Type": "application/json",
                                     "X-Puff-Token": secret})
        # No proxy, ever: the gateway is on this computer.
        opener = _ureq.build_opener(_ureq.ProxyHandler({}))  # lan-egress: BuildBot's gateway on 127.0.0.1 only
        try:
            with opener.open(req, timeout=25) as resp:  # lan-egress: BuildBot's gateway on 127.0.0.1 only
                return _json.loads(resp.read(12 * 1024 * 1024).decode("utf-8", "replace"))
        except _uerr.HTTPError as exc:
            try:
                return _json.loads(exc.read(64 * 1024).decode("utf-8", "replace"))
            except ValueError:
                return {"ok": False, "line": f"BuildBot answered {exc.code}"}
    _SEND["post"] = post


def _ask(url: str, key: str, accept: str) -> dict:
    return _post({"url": str(url), "key": str(key or ""), "accept": str(accept or "")})


def _post(payload: dict) -> dict:
    post = _SEND["post"]
    if post is None:
        raise GlueFetchError("BuildBot's fetch helper is not set up in this run")
    answer = post(payload)
    if not isinstance(answer, dict) or not answer.get("ok"):
        line = str((answer or {}).get("line") or (answer or {}).get("reason")
                   or "BuildBot did not fetch it")
        raise GlueFetchError(line)
    return answer


def fetch_source(source_id: str, **values) -> list:
    """Rows from one of the app's declared sources (its own address and
    switch), with *values* (like ``q``) filled in."""
    got = _post({"source": str(source_id), "input": {str(k): v for k, v in values.items()}})
    rows = got.get("rows")
    return list(rows) if isinstance(rows, list) else []


def fetch_text(url: str, *, key: str = "", accept: str = "") -> str:
    """The page at *url* as text, fetched by BuildBot through the Bridge."""
    return str(_ask(url, key, accept or "text/plain, */*").get("text") or "")


def fetch_json(url: str, *, key: str = "") -> object:
    """The page at *url* read as JSON, fetched by BuildBot through the Bridge."""
    text = str(_ask(url, key, "application/json").get("text") or "")
    try:
        return _json.loads(text)
    except ValueError as exc:
        raise GlueFetchError(f"the answer was not JSON ({exc.msg} at {exc.pos})") from None


def csv_rows(text: str, *, limit: int = MAX_ROWS) -> list:
    """Rows (dicts) from CSV text with a header line."""
    reader = _csv.DictReader(_io.StringIO(str(text or "")))
    out = []
    for row in reader:
        out.append({str(k or "").strip(): (v or "").strip() for k, v in row.items()})
        if len(out) >= limit:
            break
    return out


def rss_rows(text: str, *, limit: int = MAX_ROWS) -> list:
    """Rows from an RSS or Atom feed: title, link, when, summary."""
    data = str(text or "")
    if len(data) > 5 * 1024 * 1024 or "<!ENTITY" in data.upper():
        raise GlueFetchError("that feed is too big or declares its own entities")
    root = _ET.fromstring(data)
    out = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag not in ("item", "entry"):
            continue
        row = {"title": "", "link": "", "when": "", "summary": ""}
        for child in item:
            name = child.tag.rsplit("}", 1)[-1]
            text_ = (child.text or "").strip()
            if name == "title":
                row["title"] = text_
            elif name == "link":
                row["link"] = text_ or str(child.attrib.get("href") or "")
            elif name in ("pubDate", "updated", "published") and not row["when"]:
                row["when"] = text_
            elif name in ("description", "summary", "content") and not row["summary"]:
                row["summary"] = text_[:1000]
        out.append(row)
        if len(out) >= limit:
            break
    return out


__all__ = ["GlueFetchError", "csv_rows", "fetch_json", "fetch_source", "fetch_text",
           "rss_rows"]
