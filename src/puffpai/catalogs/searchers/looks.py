"""The looks snapshots (tweakcn, shadcn). Read on this computer only.

Matches are by words first, then by meaning (shipped vectors). Each find
names its snapshot so the meaning check uses the shipped vector.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any

from puffpai.catalogs import searchers as S

STEMS = {"tweakcn": "looks_tweakcn_2026-09-24", "shadcn": "looks_shadcn_2026-09-24"}


def _colours(pkg: dict[str, Any]) -> list[str]:
    from puffpai.looks import find
    try:
        return sorted(find.colour_words(pkg))
    except (KeyError, TypeError, ValueError):
        return []


def _fields(pkg: dict[str, Any]) -> dict[str, str]:
    return {"name": str(pkg.get("name") or ""),
            "tag": " ".join(str(w) for w in pkg.get("words") or []) + " "
            + " ".join(_colours(pkg)),
            "text": ""}


def _rows(which: str) -> list[dict]:
    from puffpai.looks import catalog
    return [p for p in catalog.candidates()
            if str((p.get("from") or {}).get("list") or "") == which]


def _run(job: dict, kind: str, limit: int, which: str) -> list[dict]:
    stem = STEMS[which]
    rows = S.local_search(job, kind, limit, key="looks_" + which, loader=lambda: _rows(which),
                          fields=_fields, stem=stem, row_id=lambda p: str(p.get("id")))
    out = []
    for pkg in rows:
        rec = S.new_found(source="looks", part="look", id=str(pkg.get("id")),
                          name=str(pkg.get("name") or ""),
                          what_it_is=f"A look from the {which} list.", mode_guess="theme",
                          integrable=False,
                          trust={"floor_ok": True, "path": "", "pretick_ok": False,
                                 "reasons": []})
        rec["snapshot"] = stem
        out.append(rec)
    return out


def _vec_rows(which: str) -> list[tuple[str, dict]]:
    return [(str(p.get("id")), {"name": str(p.get("name") or ""),
                                "description": f"A look from the {which} list.",
                                "tags": [str(w) for w in p.get("words") or []] + _colours(p)})
            for p in _rows(which) if p.get("id")]


def tweakcn(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    return _run(job, kind, limit, "tweakcn")


def shadcn(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    return _run(job, kind, limit, "shadcn")


def rows_tweakcn() -> list[tuple[str, dict]]:
    return _vec_rows("tweakcn")


def rows_shadcn() -> list[tuple[str, dict]]:
    return _vec_rows("shadcn")


__all__ = ["rows_shadcn", "rows_tweakcn", "shadcn", "tweakcn"]
