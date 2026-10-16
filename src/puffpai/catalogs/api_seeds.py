"""Our own hand-checked list of free websites that answer with live data.

``config/catalog_seeds/apis.json`` spans many topics (science, nature, fossils,
vehicles, books, music, food, weather, maps, museums, sports, games, words,
public health...). It is DATA: one row per website, each with a sample address
that needs no key and no owner information, the terms of use, a rate-limit
note, the words it matches and the date it was last checked live.

A row whose sample address is not on the row's own host, is not plain https,
or has an unknown ``auth`` value is dropped and logged - a bad row is never
guessed into a good one.

``puffpai catalogs refresh api_seeds`` re-checks every sample address (one small
GET each, through :func:`puffpai.catalogs.live.fetch_url`) and writes the result
as an ``api_seeds_check`` snapshot. A row whose newest check failed loses its
"checked" date, so the wizard stops pre-ticking it.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
from typing import Any, Callable, Optional

from puffpai.catalogs import live
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_ID = "api_seeds"
CHECK_ID = "api_seeds_check"
SEED_SCHEMA = "api_seeds/v1"
#: A sample answer bigger than this is not "one small GET".
SAMPLE_MAX_BYTES = 512 * 1024

_REQUIRED = ("id", "name", "what_it_is", "host", "sample_endpoint", "auth", "license",
             "match_terms", "topics")


def seed_path() -> str:
    return os.path.join(live.repo_root(), "config", "catalog_seeds", "apis.json")


def row_problem(row: Any) -> Optional[str]:
    """Why a seed row cannot be used, or None."""
    if not isinstance(row, dict):
        return "not an object"
    for key in _REQUIRED:
        if not row.get(key):
            return f"missing {key}"
    if row["auth"] not in live.AUTH_VALUES:
        return f"auth {row['auth']!r} is not one of {live.AUTH_VALUES}"
    sample = live.safe_https_url(row["sample_endpoint"])
    if not sample:
        return "sample address is not a plain https address"
    if live.host_of(sample) != str(row["host"]).lower():
        return "sample address is not on the row's own host"
    if not isinstance(row.get("match_terms"), list) or not isinstance(row.get("topics"), list):
        return "match_terms and topics must be lists"
    if "commercial_ok" in row and row["commercial_ok"] is not None \
            and not isinstance(row["commercial_ok"], bool):
        return "commercial_ok must be true, false or null"
    if row.get("card_url") is not None:
        problem = card_url_problem(row["card_url"], row["host"])
        if problem:
            return problem
        if "{topic}" in row["card_url"] and not row.get("card_topic"):
            return "card_url asks for a subject but card_topic gives none"
    if row.get("lookup_url") is not None:
        from puffpai import feeds
        problem = feeds.lookup_url_problem(row["lookup_url"], str(row["host"]).lower())
        if problem:
            return f"lookup_url {problem}"
    if row.get("card") is not None and not isinstance(row["card"], dict):
        return "card must be an object"
    means = row.get("field_meanings")
    if means is not None and not (isinstance(means, dict) and len(means) <= 20 and all(
            isinstance(k, str) and isinstance(v, str) and 0 < len(v) <= 160
            for k, v in means.items())):
        return "field_meanings must map field names to short words"
    if row.get("image_template") is not None:
        problem = image_template_problem(row["image_template"], row["host"])
        if problem:
            return f"image_template {problem}"
    if row.get("ua_header") is not None:
        from puffpai import feeds
        if row["ua_header"] not in feeds.UA_HEADERS:
            return f"ua_header must be one of {feeds.UA_HEADERS}"
    if row.get("json_label") is not None:
        from puffpai import feeds
        if row["json_label"] not in feeds.JSON_SENT_AS:
            return f"json_label must be one of {feeds.JSON_SENT_AS}"
    return None


def image_template_problem(template: Any, host: str) -> Optional[str]:
    """Why a row's picture address pattern cannot be used, or None.

    For services that send a picture NAME (and a date) rather than an address:
    ``https://<host>/archive/{date:year}/{date:month}/{date:day}/{image}.jpg``.
    Placeholders are paths into one row of the reply; the website is fixed
    text and may be the row's own host or its picture host.
    """
    from puffpai import feeds
    from puffpai.security import egress_doors
    text = str(template or "")
    try:
        from urllib.parse import urlsplit
        picture_host = (urlsplit(text.split("{", 1)[0]).hostname or "").lower()
    except ValueError:
        return "cannot be read"
    if not egress_doors.is_registrable_hostname(picture_host):
        return "does not name a public website"
    if picture_host != str(host).lower() and not picture_host.endswith(
            "." + ".".join(str(host).lower().split(".")[-2:])):
        return "is not on the row's own website or one of its sister sites"
    return feeds.image_template_problem(text, [picture_host])


#: The only placeholders a card address may carry.
CARD_URL_PLACEHOLDERS = ("topic", "lat_round", "lon_round", "date")


def card_url_problem(url: Any, host: str) -> Optional[str]:
    """Why a card address pattern cannot be used, or None."""
    import re as _re
    text = str(url or "")
    names = _re.findall(r"\{([^{}]*)\}", text)
    if any(n not in CARD_URL_PLACEHOLDERS for n in names):
        return ("card_url has a placeholder other than {topic}, {lat_round}, "
                "{lon_round}, {date}")
    filled = _re.sub(r"\{[^{}]*\}", "x", text)
    sample = live.safe_https_url(filled)
    if not sample or live.host_of(sample) != str(host).lower():
        return "card_url is not a plain https address on the row's own host"
    return None


def load_rows(path: Optional[str] = None) -> list[dict[str, Any]]:
    target = path or seed_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        logger.warning("api seed list unreadable: %s (%s)", target, exc)
        return []
    if not isinstance(data, dict) or data.get("schema") != SEED_SCHEMA:
        logger.warning("api seed list has the wrong schema: %s", target)
        return []
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in data.get("apis") or []:
        problem = row_problem(row)
        if problem:
            logger.warning("api seed row dropped (%s): %s",
                           problem, (row or {}).get("id") if isinstance(row, dict) else row)
            continue
        if row["id"] in seen:
            logger.warning("api seed row dropped (duplicate id): %s", row["id"])
            continue
        seen.add(row["id"])
        rows.append(row)
    return rows


def _latest_check() -> dict[str, dict[str, Any]]:
    snap = live.latest_snapshot(CHECK_ID)
    if not snap:
        return {}
    return {str(i.get("id")): i for i in snap.get("items") or [] if isinstance(i, dict)}


def to_candidate(row: dict[str, Any], check: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    verified_at = row.get("verified_at")
    if check is not None:
        # A newer check on this computer overrides the shipped date: a pass
        # moves it forward, a failure clears it (honesty never fails open).
        if check.get("ok") and (not verified_at or str(check.get("checked_at")) > str(verified_at)):
            verified_at = check.get("checked_at")
        elif not check.get("ok") and str(check.get("checked_at") or "") >= str(verified_at or ""):
            verified_at = None
    needs = list(row.get("needs") or [])
    if row["auth"] != "none" and "key" not in needs:
        needs.append("key")
    return live.make_candidate(
        id=f"seed:{row['id']}",
        kind="live_data",
        name=row["name"],
        what_it_is=row["what_it_is"],
        source_catalog=CATALOG_ID,
        url=row.get("url"),
        host=row["host"],
        sample_endpoint=row["sample_endpoint"],
        auth=row["auth"],
        license=row["license"],
        commercial_ok=row.get("commercial_ok"),
        size_bytes=None,
        local_or_cloud="cloud",
        needs=needs,
        match_terms=list(row.get("match_terms") or []) + list(row.get("topics") or []),
        verified_at=verified_at,
        card_url=row.get("card_url"),
        card_topic=row.get("card_topic"),
        card=row.get("card"),
        image_template=row.get("image_template"),
        ua_header=row.get("ua_header"),
        json_label=row.get("json_label"),
        lookup_url=row.get("lookup_url"),
    )


def candidates(path: Optional[str] = None) -> list[dict[str, Any]]:
    checks = _latest_check()
    return [to_candidate(row, checks.get(row["id"])) for row in load_rows(path)]


def check_row(row: dict[str, Any], fetch: Callable[..., Any] = live.fetch_url) -> dict[str, Any]:
    """One small GET of a row's sample address. Returns a check record."""
    import datetime
    stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    record: dict[str, Any] = {"id": row["id"], "host": row["host"], "checked_at": stamp}
    try:
        result = fetch(row["sample_endpoint"], hosts=[row["host"]], max_bytes=SAMPLE_MAX_BYTES,
                       accept=row.get("accept") or "application/json")
        json.loads(result.body.decode("utf-8"))
    except live.CatalogFetchError as exc:
        record.update(ok=False, reason=str(exc))
        return record
    except (UnicodeDecodeError, ValueError):
        record.update(ok=False, reason="The answer was not readable data.")
        return record
    record.update(ok=True, status=result.status, bytes=len(result.body),
                  sha256=result.sha256, final_url=result.final_url)
    return record


def refresh(*, out_dir: str, fetch: Callable[..., Any] = live.fetch_url,
            progress: Callable[[str], None] = lambda _m: None) -> dict[str, Any]:
    rows = load_rows()
    results = []
    for row in rows:
        record = check_row(row, fetch)
        progress(f"{row['name']}: {'works' if record['ok'] else 'did not work - ' + record['reason']}")
        results.append(record)
    passed = sum(1 for r in results if r["ok"])
    path = live.write_snapshot(
        CHECK_ID, results, out_dir=out_dir,
        provenance={"catalog": CATALOG_ID, "source_url": "config/catalog_seeds/apis.json",
                    "checked_by": live.USER_AGENT, "rows": len(rows)},
        extra={"passed": passed, "failed": len(results) - passed})
    return {"ok": True, "catalog": CATALOG_ID, "path": path, "count": len(results),
            "message": f"{passed} of {len(results)} websites answered."}
