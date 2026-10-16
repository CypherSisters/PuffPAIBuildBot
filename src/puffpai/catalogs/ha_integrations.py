"""Home Assistant's own list of connections, read as data.

Home Assistant publishes ``homeassistant/generated/integrations.json``: every
integration it ships, with a name, a type and an ``iot_class`` that says
whether it stays on the home network (``local_*``) or goes through the maker's
online service (``cloud_*``). BuildBot uses that to answer "can my <thing>
connect?" in plain words. Nothing is installed: the owner adds the connection
in Home Assistant, and this build talks to Home Assistant on the home network.

Brand groups (one brand, several connections) are flattened. "Virtual" rows
are brand names that Home Assistant supports through another integration or a
standard such as Zigbee; they keep the brand name and point at the connection
that actually does the work.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

from puffpai.catalogs import live

CATALOG_ID = "ha_integrations"
LIST_URL = ("https://raw.githubusercontent.com/home-assistant/core/master/"
            "homeassistant/generated/integrations.json")
MAX_BYTES = 5 * 1024 * 1024
#: Types that are part of Home Assistant itself, not a connection to a thing.
SKIP_TYPES = frozenset(("system", "helper", "entity"))
LICENSE = "Apache-2.0 (Home Assistant)"


def place_of(iot_class: Any, iot_standards: Any = None) -> str:
    text = str(iot_class or "")
    if text.startswith("local") or text in ("calculated", "assumed_state"):
        return "local"
    if text.startswith("cloud"):
        return "cloud"
    if isinstance(iot_standards, list) and iot_standards:
        return "local"
    return "unknown"


def trim(raw: Any) -> list[dict[str, Any]]:
    """integrations.json -> one flat row per connection. Never raises."""
    table = raw.get("integration") if isinstance(raw, dict) else None
    if not isinstance(table, dict):
        return []
    flat: dict[str, dict[str, Any]] = {}

    def add(domain: str, entry: dict[str, Any], brand: Optional[str]) -> None:
        if not isinstance(entry, dict) or not domain:
            return
        kind = entry.get("integration_type")
        if kind in SKIP_TYPES:
            return
        flat[domain] = {
            "domain": str(domain)[:80],
            "name": live.clean_text(entry.get("name") or domain, 80),
            "brand": live.clean_text(brand, 60) if brand else None,
            "integration_type": kind,
            "iot_class": entry.get("iot_class"),
            "iot_standards": [str(s) for s in entry.get("iot_standards") or []][:4],
            "supported_by": entry.get("supported_by"),
            "config_flow": bool(entry.get("config_flow")),
        }

    for domain, entry in table.items():
        if not isinstance(entry, dict):
            continue
        subs = entry.get("integrations")
        if isinstance(subs, dict):
            for sub_domain, sub in subs.items():
                add(str(sub_domain), sub, entry.get("name"))
        else:
            add(str(domain), entry, None)
    # A virtual brand row inherits where the supporting connection runs.
    for row in flat.values():
        target = flat.get(str(row.get("supported_by") or ""))
        if target and not row.get("iot_class"):
            row["iot_class"] = target.get("iot_class")
        if target:
            row["supported_by_name"] = target.get("name")
    return sorted(flat.values(), key=lambda r: r["domain"])


def to_candidate(item: dict[str, Any]) -> dict[str, Any]:
    domain = item["domain"]
    target = item.get("supported_by") or domain
    place = place_of(item.get("iot_class"), item.get("iot_standards"))
    where = {"local": "works on your home network",
             "cloud": "goes through the maker's online service",
             "unknown": "Home Assistant does not say where it runs"}[place]
    via = ""
    if item.get("supported_by"):
        helper = item.get("supported_by_name") or "another"
        via = f" (through Home Assistant's {helper} connection)"
    elif item.get("iot_standards"):
        via = f" (through {', '.join(s.title() for s in item['iot_standards'])})"
    terms = live.words(item.get("name")) + live.words(item.get("brand")) + live.words(domain)
    return live.make_candidate(
        id=f"ha:{domain}",
        kind="integration",
        name=item.get("name") or domain,
        what_it_is=f"A Home Assistant connection for {item.get('name') or domain}{via}; it {where}.",
        source_catalog=CATALOG_ID,
        url=f"https://www.home-assistant.io/integrations/{target}/",
        host=None,
        sample_endpoint=None,
        auth="unknown" if place == "cloud" else "none",
        license=LICENSE,
        commercial_ok=True,
        size_bytes=None,
        local_or_cloud=place,
        needs=["home_assistant"],
        match_terms=sorted(set(t for t in terms if len(t) > 1)),
        verified_at=None,
    )


def setup_domain(candidate: dict[str, Any]) -> str:
    """The Home Assistant integration the owner actually adds for this row."""
    url = str(candidate.get("url") or "")
    marker = "/integrations/"
    if marker in url:
        return url.split(marker, 1)[1].strip("/") or candidate["id"].split(":")[-1]
    return candidate["id"].split(":")[-1]


def candidates() -> list[dict[str, Any]]:
    snap = live.latest_snapshot(CATALOG_ID)
    if not snap:
        return []
    out = []
    for item in snap.get("items") or []:
        if isinstance(item, dict) and item.get("domain"):
            out.append(to_candidate(item))
    return out


def refresh(*, out_dir: str, fetch: Callable[..., Any] = live.fetch_url,
            progress: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    progress("asking raw.githubusercontent.com for Home Assistant's connection list")
    result = fetch(LIST_URL, hosts=live.catalog_hosts(CATALOG_ID) or {"raw.githubusercontent.com"},
                   max_bytes=MAX_BYTES)
    import json
    try:
        raw = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise live.CatalogFetchError("Home Assistant's list was not readable data.") from None
    items = trim(raw)
    if not items:
        raise live.CatalogFetchError("Home Assistant's list had no connections in it, so nothing was saved.")
    path = live.write_snapshot(CATALOG_ID, items, out_dir=out_dir,
                               provenance=live.provenance_of(result, CATALOG_ID))
    return {"ok": True, "catalog": CATALOG_ID, "path": path, "count": len(items), "message": ""}
