"""The MCP Registry snapshot: connectors an AI could drive. Look-only.

Read on this computer only; nothing is sent. Every row is ``integrable: false``.
Matches are by words first, then by meaning (shipped vectors); each find names
its snapshot so the meaning check uses the shipped vector.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

from typing import Any

from puffpai.catalogs import searchers as S

STEM = "mcp_registry_2026-09-23"
#: The MCP registry keeps at most this many characters of a description.
DESCRIPTION_CUT = 100


def _fields(cand: dict[str, Any]) -> dict[str, str]:
    from puffpai.catalogs import mcp_registry
    return {"name": str(cand.get("name") or ""),
            "tag": " ".join(str(t) for t in cand.get("match_terms") or [] if t != "mcp"),
            "text": mcp_registry.own_description(cand.get("what_it_is"))}


def local(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    from puffpai.catalogs import mcp_registry
    rows = S.local_search(job, kind, limit, key="mcp_registry", loader=mcp_registry.candidates,
                          fields=_fields, stem=STEM, row_id=lambda c: str(c.get("id")))
    out = []
    for cand in rows:
        rec = S.new_found(
            source="mcp", part="code-app", id=str(cand.get("id")),
            name=str(cand.get("name") or ""),
            # The registry keeps at most 100 characters of a description.
            what_it_is=S.whole_words(mcp_registry.own_description(cand.get("what_it_is")),
                                     DESCRIPTION_CUT),
            url=str(cand.get("url") or ""), mode_guess="mcp_server", integrable=False,
            trust={"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []})
        rec["snapshot"] = STEM
        out.append(rec)
    return out


def rows() -> list[tuple[str, dict]]:
    from puffpai.catalogs import mcp_registry
    return [(str(c.get("id")), {"name": str(c.get("name") or ""),
                                "description": mcp_registry.own_description(c.get("what_it_is")),
                                "tags": [str(t) for t in c.get("match_terms") or [] if t != "mcp"]})
            for c in mcp_registry.candidates() if c.get("id")]


__all__ = ["local", "rows"]
