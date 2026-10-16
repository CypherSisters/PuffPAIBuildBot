"""The APIs.guru directory (api.apis.guru), read as data.

APIs.guru lists about 2,500 web services with a machine-readable description
(an OpenAPI spec) for each. The directory itself is CC0. Only about a third of
the services state a license; the rest show "not stated".

Two steps, both deliberate:

1. **The list** (``/v2/list.json``, about 9 MB) is trimmed to a snapshot: id,
   title, a short cleaned description, its categories, the address of its
   spec, and its stated license. From the list alone we cannot tell whether a
   service needs a key, so every candidate starts as ``auth: "unknown"``.
2. **The spec** of a shortlisted service is read lazily, only when the owner
   asks (``puffpai catalogs search ... --details``), from api.apis.guru only.
   :func:`read_spec` looks at its security schemes, its servers and its GET
   operations. A ``sample_endpoint`` is built only when an operation needs no
   parameters at all, the service declares no security schemes, and the server
   is a plain https address - so no owner data and no key can end up in it.
   A sample built this way has not been called; ``verified_at`` stays empty.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from puffpai.catalogs import live

CATALOG_ID = "apis_guru"
LIST_URL = "https://api.apis.guru/v2/list.json"
LIST_MAX_BYTES = 25 * 1024 * 1024
SPEC_MAX_BYTES = 5 * 1024 * 1024
SPEC_HOST = "api.apis.guru"

#: Plain names for common license strings. Anything else is shown as given.
_SPDX = {
    "cc0": "CC0-1.0", "cc0 1.0": "CC0-1.0", "mit": "MIT", "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0", "apache license 2.0": "Apache-2.0",
    "apache license, version 2.0": "Apache-2.0", "cc by 4.0": "CC-BY-4.0",
    "cc-by-4.0": "CC-BY-4.0", "creative commons attribution 4.0 international": "CC-BY-4.0",
    "bsd": "BSD", "gpl-3.0": "GPL-3.0",
}


def license_of(info: Any) -> tuple[str, Optional[str]]:
    lic = (info or {}).get("license") if isinstance(info, dict) else None
    if not isinstance(lic, dict) or not lic.get("name"):
        return "not stated", None
    name = live.clean_text(lic.get("name"), 60)
    return _SPDX.get(name.lower(), name), live.safe_https_url(lic.get("url"))


def trim(raw: Any) -> list[dict[str, Any]]:
    """list.json -> one short row per service (preferred version). Never raises."""
    if not isinstance(raw, dict):
        return []
    out: list[dict[str, Any]] = []
    for api_id, api in raw.items():
        if not isinstance(api, dict):
            continue
        versions = api.get("versions") or {}
        version = versions.get(api.get("preferred")) if isinstance(versions, dict) else None
        if not isinstance(version, dict):
            continue
        info = version.get("info") or {}
        spec_url = live.safe_https_url(version.get("swaggerUrl"))
        if spec_url and live.host_of(spec_url) != SPEC_HOST:
            spec_url = None
        license_name, license_url = license_of(info)
        contact = info.get("contact") if isinstance(info.get("contact"), dict) else {}
        docs = version.get("externalDocs") if isinstance(version.get("externalDocs"), dict) else {}
        home = live.safe_https_url(contact.get("url")) or live.safe_https_url(
            (info.get("externalDocs") or {}).get("url") if isinstance(info.get("externalDocs"), dict) else None
        ) or live.safe_https_url(docs.get("url"))
        cats = info.get("x-apisguru-categories") or []
        out.append({
            "id": str(api_id)[:120],
            "title": live.clean_text(info.get("title") or api_id, 80),
            "description": live.clean_text(info.get("description"), 240),
            "categories": [str(c)[:30] for c in cats if isinstance(c, str)][:6],
            "spec_url": spec_url,
            "license": license_name,
            "license_url": license_url,
            "home": home,
        })
    out.sort(key=lambda r: r["id"])
    return out


def to_candidate(item: dict[str, Any]) -> dict[str, Any]:
    provider = item["id"].split(":")[0]
    terms = (live.words(" ".join(item.get("categories") or []).replace("_", " "))
             + live.words(item.get("title")) + live.words(provider.replace(".", " ")))
    return live.make_candidate(
        id=f"apis_guru:{item['id']}",
        kind="live_data",
        name=item.get("title") or item["id"],
        what_it_is=item.get("description") or item.get("title"),
        source_catalog=CATALOG_ID,
        url=item.get("home"),
        host=None,
        sample_endpoint=None,
        auth="unknown",
        license=item.get("license") or "not stated",
        commercial_ok=None,
        size_bytes=None,
        local_or_cloud="cloud",
        needs=["key"],
        match_terms=sorted(set(t for t in terms if len(t) > 2 and t not in live.STOPWORDS)),
        verified_at=None,
    )


def candidates() -> list[dict[str, Any]]:
    snap = live.latest_snapshot(CATALOG_ID)
    if not snap:
        return []
    return [to_candidate(i) for i in snap.get("items") or [] if isinstance(i, dict) and i.get("id")]


def item_for(candidate_id: str) -> Optional[dict[str, Any]]:
    snap = live.latest_snapshot(CATALOG_ID) or {}
    want = candidate_id.split("apis_guru:", 1)[-1]
    for item in snap.get("items") or []:
        if isinstance(item, dict) and item.get("id") == want:
            return item
    return None


# ---------------------------------------------------------------------------
# reading one spec (lazy, online, owner-asked)
# ---------------------------------------------------------------------------

def _servers(spec: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for server in spec.get("servers") or []:
        if isinstance(server, dict):
            url = str(server.get("url") or "")
            if "{" in url:
                continue
            safe = live.safe_https_url(url)
            if safe:
                urls.append(safe.rstrip("/"))
    if not urls and spec.get("host"):
        schemes = spec.get("schemes") or ["https"]
        if "https" in schemes:
            safe = live.safe_https_url(f"https://{spec['host']}{spec.get('basePath') or ''}")
            if safe:
                urls.append(safe.rstrip("/"))
    return urls


def _schemes(spec: dict[str, Any]) -> dict[str, Any]:
    comps = spec.get("components") if isinstance(spec.get("components"), dict) else {}
    found = comps.get("securitySchemes") or spec.get("securityDefinitions") or {}
    return found if isinstance(found, dict) else {}


def _required_params(params: Any) -> bool:
    for param in params or []:
        if isinstance(param, dict) and (param.get("required") or param.get("in") == "path"
                                        or "$ref" in param):
            return True
    return False


def read_spec(spec: Any) -> dict[str, Any]:
    """What one OpenAPI/Swagger document says. Pure; never raises.

    Returns ``{"auth", "host", "sample_endpoint", "get_count", "scheme_kinds"}``.
    ``auth`` is ``"none"`` only when the spec declares no security schemes at
    all; any declared scheme means ``"unknown"`` (we cannot tell free from
    paid). ``$ref`` parameters are treated as required - unread means unsafe.
    """
    out: dict[str, Any] = {"auth": "unknown", "host": None, "sample_endpoint": None,
                           "get_count": 0, "scheme_kinds": []}
    if not isinstance(spec, dict):
        return out
    schemes = _schemes(spec)
    out["scheme_kinds"] = sorted({str((s or {}).get("type") or "?") for s in schemes.values()
                                  if isinstance(s, dict)})
    keyless = not schemes and not spec.get("security")
    servers = _servers(spec)
    if servers:
        out["host"] = live.host_of(servers[0])
    paths = spec.get("paths") if isinstance(spec.get("paths"), dict) else {}
    sample = None
    for path, ops in paths.items():
        if not isinstance(ops, dict) or not isinstance(path, str):
            continue
        get = ops.get("get")
        if not isinstance(get, dict):
            continue
        out["get_count"] += 1
        if sample or "{" in path or not path.startswith("/"):
            continue
        if _required_params(ops.get("parameters")) or _required_params(get.get("parameters")):
            continue
        if get.get("security"):
            continue
        if keyless and servers:
            candidate = live.safe_https_url(servers[0] + path)
            if candidate and live.host_of(candidate) == out["host"]:
                sample = candidate
    if keyless:
        out["auth"] = "none"
    out["sample_endpoint"] = sample
    return out


def enrich(candidate: dict[str, Any], *, fetch: Callable[..., Any] = live.fetch_url) -> dict[str, Any]:
    """Read a shortlisted service's spec and return an updated candidate. Online.

    Raises :class:`live.CatalogFetchError` with plain words when the spec is
    unreachable or unreadable. The candidate passed in is not modified.
    """
    item = item_for(candidate["id"])
    if not item or not item.get("spec_url"):
        raise live.CatalogFetchError("APIs.guru has no description on file for that service.")
    result = fetch(item["spec_url"], hosts={SPEC_HOST}, max_bytes=SPEC_MAX_BYTES)
    try:
        spec = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise live.CatalogFetchError("That service's description was not readable data.") from None
    facts = read_spec(spec)
    updated = dict(candidate)
    updated["host"] = facts["host"]
    updated["auth"] = facts["auth"]
    updated["sample_endpoint"] = facts["sample_endpoint"]
    updated["needs"] = [] if facts["auth"] == "none" else ["key"]
    updated["verified_at"] = None
    return updated


def refresh(*, out_dir: str, fetch: Callable[..., Any] = live.fetch_url,
            progress: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    progress("asking api.apis.guru for its directory (about 9 MB)")
    result = fetch(LIST_URL, hosts=live.catalog_hosts(CATALOG_ID) or {SPEC_HOST},
                   max_bytes=LIST_MAX_BYTES)
    try:
        raw = json.loads(result.body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise live.CatalogFetchError("The APIs.guru directory was not readable data.") from None
    items = trim(raw)
    if not items:
        raise live.CatalogFetchError("The APIs.guru directory had no services in it, so nothing was saved.")
    with_license = sum(1 for i in items if i["license"] != "not stated")
    path = live.write_snapshot(CATALOG_ID, items, out_dir=out_dir, compress=True,
                               provenance=live.provenance_of(result, CATALOG_ID),
                               extra={"with_license": with_license})
    return {"ok": True, "catalog": CATALOG_ID, "path": path, "count": len(items),
            "message": f"{with_license} of them state a license."}
