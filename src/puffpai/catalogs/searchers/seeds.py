"""Live-info snapshots: our checked list, the public-apis list and APIs.guru.

Read on this computer only; nothing is sent. Rows keep their existing ids
(``seed:...``, ``public_apis:...``, ``apis_guru:...``) so the existing live
card path can rebuild them. Matches are by words first, then by meaning
(shipped vectors, :func:`searchers.local_search`). Each find names its
snapshot (``found["snapshot"]``) so the meaning check uses the shipped
vector instead of embedding it again.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any

from puffpai.catalogs import searchers as S

STEMS = {"seeds": "api_seeds", "public_apis": "public_apis_2026-09-23",
         "apis_guru": "apis_guru_2026-09-23", "ha": "ha_integrations_2026-09-23"}


def _trust() -> dict[str, Any]:
    return {"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []}


def _fields(cand: dict[str, Any]) -> dict[str, str]:
    return {"name": str(cand.get("name") or ""),
            "tag": " ".join(str(t) for t in cand.get("match_terms") or []),
            "text": str(cand.get("what_it_is") or "")}


def _found(cand: dict[str, Any], source: str, stem: str) -> dict[str, Any]:
    rec = S.new_found(source=source, part="live-info", id=str(cand.get("id")),
                      name=str(cand.get("name") or ""),
                      what_it_is=str(cand.get("what_it_is") or ""),
                      url=str(cand.get("url") or ""), mode_guess="other", integrable=False,
                      trust=_trust())
    rec["snapshot"] = stem
    return rec


def _run(job: dict, kind: str, limit: int, key: str, loader, source: str) -> list[dict]:
    stem = STEMS[source if source in STEMS else key]
    rows = S.local_search(job, kind, limit, key=key, loader=loader, fields=_fields, stem=stem,
                          row_id=lambda c: str(c.get("id")))
    return [_found(c, source, stem) for c in rows]


def _vec_rows(cands: list[dict]) -> list[tuple[str, dict]]:
    return [(str(c.get("id")), {"name": str(c.get("name") or ""),
                                "description": str(c.get("what_it_is") or ""),
                                "tags": [str(t) for t in c.get("match_terms") or []]})
            for c in cands if c.get("id")]


def api_seeds(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    from puffpai.catalogs import api_seeds as mod
    return _run(job, kind, limit, "api_seeds", mod.candidates, "seeds")


def public_apis(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    from puffpai.catalogs import public_apis as mod
    return _run(job, kind, limit, "public_apis", mod.candidates, "public_apis")


def apis_guru(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    from puffpai.catalogs import apis_guru as mod
    return _run(job, kind, limit, "apis_guru", mod.candidates, "apis_guru")


def rows_api_seeds() -> list[tuple[str, dict]]:
    from puffpai.catalogs import api_seeds as mod
    return _vec_rows(mod.candidates())


def rows_public_apis() -> list[tuple[str, dict]]:
    from puffpai.catalogs import public_apis as mod
    return _vec_rows(mod.candidates())


def rows_apis_guru() -> list[tuple[str, dict]]:
    from puffpai.catalogs import apis_guru as mod
    return _vec_rows(mod.candidates())


def rows_ha_integrations() -> list[tuple[str, dict]]:
    """Home Assistant integrations, by the ids ``ha_integrations.candidates()`` gives
    (no adapter here: the meaning check scores them with these vectors)."""
    from puffpai.catalogs import ha_integrations as mod
    return _vec_rows(mod.candidates())


__all__ = ["api_seeds", "apis_guru", "public_apis", "rows_api_seeds", "rows_apis_guru",
           "rows_ha_integrations", "rows_public_apis"]
