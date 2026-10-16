"""Outside libraries BuildBot can look through for knowledge.

``config/catalog_index.json`` is the catalog of catalogs: one row per upstream
library. A row whose reader ships in this build says ``adapter_built: true``;
every other row is present so the product can say, in plain words, that the
library is *not searched yet* - never so that it can pretend to search it.

Results from any library are candidates. Nothing here downloads a file; the
only thing in this build that moves bytes is :mod:`puffpai.source_fetch`, and
it only moves them after the owner has seen the size, the license and what the
thing is, and said yes.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional

from puffpai import config_health

SCHEMA = "catalog_index/v1"

#: What a build with no index file (or a broken one) knows about: nothing.
#: An absent index means no library is searched, and the product says so.
_EMPTY_INDEX: dict[str, Any] = {"schema": SCHEMA, "catalogs": []}


def index_path() -> str:
    """Absolute path of the shipped catalog index."""
    from puffpai.config import get_config
    return os.path.join(get_config().REPO_ROOT, "config", "catalog_index.json")


def load_index(path: Optional[str] = None) -> dict[str, Any]:
    """The catalog index, or an empty one. Never raises.

    A missing or unreadable file is recorded in :mod:`puffpai.config_health`
    so the owner hears that no outside library is being searched, rather than
    being shown an empty result list that looks like "nothing matched".
    """
    target = path or index_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        config_health.note_load_failure("catalog_index", target, exc, reduced=True)
        return dict(_EMPTY_INDEX)
    if not isinstance(data, dict) or not isinstance(data.get("catalogs"), list):
        config_health.record("catalog_index", target, config_health.INVALID,
                             "The catalog index has no list of catalogs.",
                             reduced=True)
        return dict(_EMPTY_INDEX)
    return data


def catalog_rows(index: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """Every row in the index, as dicts. Rows without an id are dropped."""
    data = index if index is not None else load_index()
    return [dict(row) for row in (data.get("catalogs") or [])
            if isinstance(row, dict) and row.get("id")]


def catalog_row(catalog_id: str,
                index: Optional[dict[str, Any]] = None) -> Optional[dict[str, Any]]:
    for row in catalog_rows(index):
        if str(row.get("id")) == str(catalog_id):
            return row
    return None


def is_live(row: Optional[dict[str, Any]]) -> bool:
    """A library is searched only when its reader ships AND the row says so."""
    return bool(isinstance(row, dict) and row.get("adapter_built") is True
                and str(row.get("status") or "") == "live")


def live_catalogs(index: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    return [row for row in catalog_rows(index) if is_live(row)]


def not_built_catalogs(index: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    return [row for row in catalog_rows(index) if not is_live(row)]


def license_rules(catalog_id: str,
                  index: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """The per-family license table for a library with no per-item license."""
    row = catalog_row(catalog_id, index)
    rules = (row or {}).get("license_rules") or []
    out: list[dict[str, Any]] = []
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        if not rule.get("category") or not rule.get("license") \
                or not rule.get("license_url"):
            continue
        out.append(dict(rule))
    return out


def not_searched_line(index: Optional[dict[str, Any]] = None) -> str:
    """One plain sentence naming the libraries this build cannot search yet."""
    labels = [str(row.get("label") or row.get("id"))
              for row in not_built_catalogs(index)]
    if not labels:
        return ""
    return ("Not searched yet, because this build has no reader for them: "
            + "; ".join(labels) + ".")


__all__ = ["SCHEMA", "index_path", "load_index", "catalog_rows", "catalog_row",
           "is_live", "live_catalogs", "not_built_catalogs", "license_rules",
           "not_searched_line"]
