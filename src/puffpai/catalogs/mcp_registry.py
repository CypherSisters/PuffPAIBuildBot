"""The official MCP Registry (registry.modelcontextprotocol.io), read as data.

The registry lists tool servers other programs can plug into. It is a preview,
it proves who published a name, and it does not review code. So in this build
an MCP server is only ever a *candidate you can read about*:

* nothing is downloaded, installed or run - a server that runs as code on your
  computer carries ``needs: ["code_sandbox"]`` and says in plain words that
  this bot cannot run outside programs safely yet;
* a remote server is labelled plainly: it "sends your data to <its host>".

Licenses are not in the registry, so every candidate shows "not stated".
Deprecated and deleted entries are skipped.

Paging: the registry answers 100 servers a page, one page a second. A refresh
stops at a byte budget and records ``complete: false`` when it did, so a
search can say it looked at part of the list rather than all of it.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable, Optional

from puffpai.catalogs import live

CATALOG_ID = "mcp_registry"
BASE_URL = "https://registry.modelcontextprotocol.io/v0/servers"
PAGE_SIZE = 100
PAGE_MAX_BYTES = 2 * 1024 * 1024
#: Total bytes a refresh may pull across all pages.
BUDGET_BYTES = 24 * 1024 * 1024
MAX_PAGES = 600
LICENSE = "not stated (the registry does not list licenses)"
#: Our own notes, put in front of the registry's description.
CODE_NOTE = ("It is a program that would run on your computer, and this bot "
             "cannot run outside programs safely yet.")
UNKNOWN_NOTE = "The list does not say how to reach it."
_NOTES = re.compile(r"^(?:It sends your data to \S+?\.\s+)?(?:%s\s+)?(?:%s\s+)?" % (
    re.escape(CODE_NOTE), re.escape(UNKNOWN_NOTE)))


def own_description(what_it_is: Any) -> str:
    """The registry's own words for a server, without the notes this code adds.

    Ranking reads only these words, so a goal word such as "running" or
    "data" cannot match every server through our own notes.
    """
    return _NOTES.sub("", str(what_it_is or ""), count=1)


def page_url(cursor: Optional[str]) -> str:
    query = {"version": "latest", "limit": str(PAGE_SIZE)}
    if cursor:
        query["cursor"] = cursor
    return BASE_URL + "?" + urllib.parse.urlencode(query)


def trim_server(entry: Any) -> Optional[dict[str, Any]]:
    """One registry record -> a short row, or None when it should be skipped."""
    if not isinstance(entry, dict):
        return None
    server = entry.get("server") if isinstance(entry.get("server"), dict) else entry
    meta = (entry.get("_meta") or {}).get("io.modelcontextprotocol.registry/official") or {}
    status = str(meta.get("status") or server.get("status") or "active").lower()
    if status in ("deprecated", "deleted"):
        return None
    name = str(server.get("name") or "").strip()
    if not name:
        return None
    remote_hosts: list[str] = []
    for remote in server.get("remotes") or []:
        if isinstance(remote, dict):
            host = live.host_of(live.safe_https_url(remote.get("url")))
            if host and host not in remote_hosts:
                remote_hosts.append(host)
    package_kinds: list[str] = []
    for package in server.get("packages") or []:
        if isinstance(package, dict):
            kind = str(package.get("registryType") or package.get("registry_type") or "").lower()[:20]
            if kind and kind not in package_kinds:
                package_kinds.append(kind)
    repo = server.get("repository") if isinstance(server.get("repository"), dict) else {}
    return {
        "name": name[:120],
        "title": live.clean_text(server.get("title"), 80) or None,
        "description": live.clean_text(server.get("description"), 200),
        "version": str(server.get("version") or "")[:40],
        "remote_hosts": remote_hosts[:3],
        "package_kinds": package_kinds[:4],
        "repository": live.safe_https_url(repo.get("url")),
        "website": live.safe_https_url(server.get("websiteUrl")),
    }


def to_candidate(item: dict[str, Any]) -> dict[str, Any]:
    remote = list(item.get("remote_hosts") or [])
    packages = list(item.get("package_kinds") or [])
    display = item.get("title") or item["name"].split("/")[-1]
    notes = []
    needs: list[str] = []
    if remote:
        notes.append(f"It sends your data to {remote[0]}.")
    if packages:
        needs.append("code_sandbox")
        notes.append(CODE_NOTE)
    if not remote and not packages:
        notes.append(UNKNOWN_NOTE)
    # The privacy and sandbox notes go FIRST, so no shortening can cut them off.
    what = " ".join(notes) + " " + (item.get("description") or "An outside tool server.")
    place = "cloud" if remote and not packages else ("local" if packages and not remote else "unknown")
    terms = live.words(item.get("name")) + live.words(item.get("title"))
    return live.make_candidate(
        id=f"mcp:{item['name']}",
        kind="integration",
        name=display,
        what_it_is=what,
        source_catalog=CATALOG_ID,
        url=item.get("website") or item.get("repository"),
        host=remote[0] if remote else None,
        sample_endpoint=None,
        auth="unknown",
        license=LICENSE,
        commercial_ok=None,
        size_bytes=None,
        local_or_cloud=place,
        needs=needs,
        match_terms=sorted(set(t for t in terms if len(t) > 2)),
        verified_at=None,
    )


def candidates() -> list[dict[str, Any]]:
    snap = live.latest_snapshot(CATALOG_ID)
    if not snap:
        return []
    return [to_candidate(i) for i in snap.get("items") or []
            if isinstance(i, dict) and i.get("name") and not _says_retired(i)]


def _says_retired(item: dict[str, Any]) -> bool:
    """A server whose own description opens with "deprecated" is not offered,
    even when the registry still marks it active."""
    return str(item.get("description") or "").lstrip().lower().startswith("deprecated")


def refresh(*, out_dir: str, fetch: Callable[..., Any] = live.fetch_url,
            progress: Callable[[str], None] = lambda _m: None,
            budget_bytes: int = BUDGET_BYTES, max_pages: int = MAX_PAGES) -> dict[str, Any]:
    hosts = live.catalog_hosts(CATALOG_ID) or {"registry.modelcontextprotocol.io"}
    items: dict[str, dict[str, Any]] = {}
    cursor: Optional[str] = None
    total = 0
    pages = 0
    complete = False
    first = None
    while pages < max_pages:
        result = fetch(page_url(cursor), hosts=hosts, max_bytes=PAGE_MAX_BYTES)
        first = first or result
        pages += 1
        total += len(result.body)
        try:
            data = json.loads(result.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise live.CatalogFetchError("The MCP Registry sent a page that was not readable data.") from None
        for entry in data.get("servers") or []:
            row = trim_server(entry)
            if row:
                items[row["name"]] = row
        cursor = ((data.get("metadata") or {}).get("nextCursor") or None)
        if pages % 25 == 0:
            progress(f"read {pages} pages, {len(items):,} tools so far")
        if not cursor:
            complete = True
            break
        if total >= budget_bytes:
            break
    if not items or first is None:
        raise live.CatalogFetchError("The MCP Registry returned no tools, so nothing was saved.")
    provenance = live.provenance_of(first, CATALOG_ID)
    provenance.update(pages=pages, bytes=total, sha256=None, first_page_sha256=first.sha256)
    rows = sorted(items.values(), key=lambda r: r["name"])
    path = live.write_snapshot(CATALOG_ID, rows, out_dir=out_dir, provenance=provenance,
                               compress=True, extra={"complete": complete})
    note = "" if complete else f"Stopped after {pages} pages to stay under the size limit; this is part of the list."
    return {"ok": True, "catalog": CATALOG_ID, "path": path, "count": len(rows), "message": note}
