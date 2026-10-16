"""The public-apis list (github.com/public-apis/public-apis), read as data.

The list is one long README of Markdown tables: a section heading, then rows of
``| [Name](link) | Description | Auth | HTTPS | CORS |``. Its old JSON service
is gone, so the README is the only machine-readable form. The list itself is
MIT; it says nothing about each service's own license, so every candidate
shows "not stated".

Only rows that need no key (``Auth: No``) and use https become candidates.
Everything before the "Index" heading (the sponsor block) is skipped, and so is
any row whose link carries tracking parameters. No sample address is made up:
the link in the list is a documentation page, not something to call.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from puffpai.catalogs import live

CATALOG_ID = "public_apis"
README_URL = "https://raw.githubusercontent.com/public-apis/public-apis/master/README.md"
MAX_BYTES = 2 * 1024 * 1024
LICENSE = "not stated (the list is MIT; each service has its own terms)"

_ROW_LINK = re.compile(r"^\[(?P<name>[^\]]{1,120})\]\((?P<url>[^)\s]{1,500})\)$")


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [c.strip() for c in body.split("|")]


def parse(markdown: str) -> list[dict[str, Any]]:
    """README text -> rows. Never raises; odd lines are skipped."""
    rows: list[dict[str, Any]] = []
    section = None
    started = False
    for line in str(markdown or "").splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("## index"):
            started = True
            continue
        if not started:
            continue
        if stripped.startswith("### "):
            section = live.clean_text(stripped[4:], 60)
            continue
        if not section or not stripped.startswith("|"):
            continue
        cells = _cells(stripped)
        if len(cells) < 5:
            continue
        match = _ROW_LINK.match(cells[0])
        if not match:
            continue
        url = match.group("url")
        if "utm_" in url.lower():
            continue
        auth = cells[2].strip("` ").lower()
        rows.append({
            "section": section,
            "name": live.clean_text(match.group("name"), 80),
            "url": url if live.safe_https_url(url) else None,
            "description": live.clean_text(cells[1], 200),
            "auth": auth if auth else "unknown",
            "https": cells[3].strip().lower() == "yes",
            "cors": cells[4].strip().lower(),
        })
    return rows


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:50] or "row"


def candidates() -> list[dict[str, Any]]:
    snap = live.latest_snapshot(CATALOG_ID)
    if not snap:
        return []
    out = []
    seen: set[str] = set()
    for item in snap.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("auth") != "no" or not item.get("https") or not item.get("url"):
            continue
        base = f"public_apis:{_slug(item.get('name'))}"
        cid = base
        n = 2
        while cid in seen:
            cid = f"{base}_{n}"
            n += 1
        seen.add(cid)
        out.append(live.make_candidate(
            id=cid,
            kind="live_data",
            name=item.get("name"),
            what_it_is=item.get("description"),
            source_catalog=CATALOG_ID,
            url=item.get("url"),
            host=None,
            sample_endpoint=None,
            auth="none",
            license=LICENSE,
            commercial_ok=None,
            size_bytes=None,
            local_or_cloud="cloud",
            needs=[],
            match_terms=live.words(item.get("section")) + live.words(item.get("name")),
            verified_at=None,
        ))
    return out


def refresh(*, out_dir: str, fetch: Callable[..., Any] = live.fetch_url,
            progress: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    progress("asking raw.githubusercontent.com for the public-apis list")
    result = fetch(README_URL, hosts=live.catalog_hosts(CATALOG_ID) or {"raw.githubusercontent.com"},
                   max_bytes=MAX_BYTES, accept="text/plain")
    try:
        text = result.body.decode("utf-8")
    except UnicodeDecodeError:
        raise live.CatalogFetchError("The public-apis list was not readable text.") from None
    items = parse(text)
    if not items:
        raise live.CatalogFetchError("The public-apis list had no rows we could read, so nothing was saved.")
    keyless = sum(1 for i in items if i["auth"] == "no" and i["https"])
    path = live.write_snapshot(CATALOG_ID, items, out_dir=out_dir,
                               provenance=live.provenance_of(result, CATALOG_ID),
                               extra={"keyless_https": keyless})
    return {"ok": True, "catalog": CATALOG_ID, "path": path, "count": len(items),
            "message": f"{keyless} need no key and use https."}
