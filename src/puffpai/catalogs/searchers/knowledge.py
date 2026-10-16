"""Knowledge snapshots: the Kiwix library, the Gutenberg catalog and OpenStax textbooks.

The snapshots are read on this computer only; nothing is sent. Matches are by
words first, then by meaning (shipped vectors). Each find names its snapshot
so the meaning check uses the shipped vector.

Wikipedia (live, only for a "learn about" job, only after "Start looking"):
one search on Wikipedia's own search with the job's ``Sendable`` phrase (at
most 2 a build). When Wikipedia has at least :data:`WIKI_MIN_HITS` articles
on those words, the find is the existing Wikipedia topic book for them
(``wikipedia_topics``: the articles are fetched as one book only after the
owner's yes). Wikibooks has no reader in this build that could add a book,
so its registry row is off with that reason: a place that could not give a
usable find is never asked.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Optional

from puffpai.catalogs import searchers as S

KIWIX_STEM = "kiwix_opds_eng_2026-09-23"
GUTENBERG_STEM = "gutenberg_catalog_en_2026-09-24"
#: The Gutenberg snapshot keeps at most this many characters of a title.
GUTENBERG_TITLE_CUT = 100
OPENSTAX_STEM = "openstax_books_2026-09-24"


def _found(source: str, id: str, name: str, what: str, url: str, stem: str) -> dict[str, Any]:
    rec = S.new_found(source=source, part="knowledge", id=id, name=name, what_it_is=what,
                      url=url, mode_guess="other", integrable=False,
                      trust={"floor_ok": True, "path": "", "pretick_ok": False, "reasons": []})
    rec["snapshot"] = stem
    return rec


# -- Kiwix -------------------------------------------------------------------

def _kiwix_rows() -> list[dict]:
    from puffpai.catalogs import kiwix_opds
    best: dict[str, dict] = {}
    for e in kiwix_opds.load_snapshot().get("entries") or []:
        if not isinstance(e, dict) or not e.get("name"):
            continue
        key = str(e.get("title") or e["name"])
        if key not in best or (e.get("size_bytes") or 0) < (best[key].get("size_bytes") or 0):
            best[key] = e
    return list(best.values())


def _kiwix_id(e: dict) -> str:
    from puffpai.catalogs import kiwix_opds
    return kiwix_opds.book_id(str(e["name"]), str(e.get("flavour") or ""))


def _kiwix_fields(e: dict) -> dict[str, str]:
    tags = [str(t) for t in e.get("tags") or []] + [str(e.get("category") or "")]
    return {"name": str(e.get("title") or ""), "tag": " ".join(tags),
            "text": str(e.get("summary") or "")}


def kiwix(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    rows = S.local_search(job, kind, limit, key="kiwix", loader=_kiwix_rows,
                          fields=_kiwix_fields, stem=KIWIX_STEM, row_id=_kiwix_id)
    return [_found("kiwix", _kiwix_id(e), str(e.get("title") or e["name"]),
                   str(e.get("summary") or ""), "https://library.kiwix.org/", KIWIX_STEM)
            for e in rows]


def rows_kiwix() -> list[tuple[str, dict]]:
    return [(_kiwix_id(e), {"name": str(e.get("title") or ""),
                            "description": str(e.get("summary") or ""),
                            "tags": [str(t) for t in e.get("tags") or []]
                            + [str(e.get("category") or "")]})
            for e in _kiwix_rows()]


# -- Gutenberg ---------------------------------------------------------------

def _gutenberg_rows() -> list[dict]:
    from puffpai.catalogs import gutendex
    return [b for b in gutendex.load_snapshot().get("books") or []
            if isinstance(b, dict) and isinstance(b.get("id"), int)
            and not isinstance(b.get("id"), bool)]


def _gutenberg_fields(b: dict) -> dict[str, str]:
    return {"name": str(b.get("title") or ""),
            "tag": " ".join(str(s) for s in b.get("subjects") or []), "text": ""}


def _gutenberg_what(b: dict) -> str:
    return f"A free book by {str(b.get('author') or 'an unknown author')}."


def gutenberg(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    rows = S.local_search(job, kind, limit, key="gutenberg", loader=_gutenberg_rows,
                          fields=_gutenberg_fields, stem=GUTENBERG_STEM,
                          row_id=lambda b: f"gutenberg:{b.get('id')}")
    # The catalog stores titles cut at 100 characters, often mid-word.
    return [_found("gutenberg", f"gutenberg:{b['id']}",
                   S.whole_words(b.get("title"), GUTENBERG_TITLE_CUT),
                   _gutenberg_what(b), f"https://www.gutenberg.org/ebooks/{b['id']}",
                   GUTENBERG_STEM) for b in rows]


def rows_gutenberg() -> list[tuple[str, dict]]:
    return [(f"gutenberg:{b['id']}", {"name": str(b.get("title") or ""),
                                      "description": _gutenberg_what(b),
                                      "tags": [str(s) for s in b.get("subjects") or []]})
            for b in _gutenberg_rows()]


# -- OpenStax ----------------------------------------------------------------

def _openstax_rows() -> list[dict]:
    from puffpai.catalogs import openstax as mod
    return [b for b in mod.load_snapshot().get("books") or []
            if isinstance(b, dict) and b.get("state") == "live"]


def _openstax_fields(b: dict) -> dict[str, str]:
    return {"name": str(b.get("title") or ""),
            "tag": " ".join(str(s) for s in b.get("subjects") or []),
            "text": str(b.get("summary") or "")}


def _openstax_id(b: dict) -> str:
    from puffpai.catalogs import openstax as mod
    return str(mod.candidate(b)["id"])


def openstax(job: dict, *, kind: str, row: dict, limit: int = 25) -> list[dict]:
    from puffpai.catalogs import openstax as mod
    rows = S.local_search(job, kind, limit, key="openstax", loader=_openstax_rows,
                          fields=_openstax_fields, stem=OPENSTAX_STEM, row_id=_openstax_id)
    out = []
    for b in rows:
        cand = mod.candidate(b)
        out.append(_found("openstax", str(cand["id"]), str(cand.get("name") or ""),
                          str(cand.get("what_it_is") or ""), str(cand.get("url") or ""),
                          OPENSTAX_STEM))
    return out


def rows_openstax() -> list[tuple[str, dict]]:
    from puffpai.catalogs import openstax as mod
    out = []
    for b in _openstax_rows():
        cand = mod.candidate(b)
        out.append((str(cand["id"]), {"name": str(cand.get("name") or ""),
                                      "description": str(cand.get("what_it_is") or ""),
                                      "tags": [str(s) for s in b.get("subjects") or []]}))
    return out


# -- Wikipedia (live) --------------------------------------------------------

WIKI_POOL = "wikipedia"
WIKI_PER_PAGE = 10
WIKI_MIN_HITS = 3
WIKI_MAX_BYTES = 256 * 1024


def _wiki_titles(body: Any) -> list[str]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError, RecursionError):
        return []
    pages = data.get("pages") if isinstance(data, dict) else None
    out: list[str] = []
    for pg in pages if isinstance(pages, list) else []:
        title = S.plain_text(pg.get("title"), 80).rstrip(".") if isinstance(pg, dict) else ""
        if title and title not in out:
            out.append(title)
    return out[:WIKI_PER_PAGE]


def _wiki_asks(job: dict, phrases: list, kind: str) -> list[tuple[Any, dict]]:
    """``[(phrase, book)]`` *wikipedia* asks about: a learn job's best phrase,
    when it names a Wikipedia book BuildBot can make."""
    if kind != "knowledge" or not isinstance(job, dict) or not job.get("learn"):
        return []
    from puffpai.catalogs import wikipedia_topics as wt
    from puffpai.catalogs.searchers import github as G
    out = []
    for p in G.phrases_for_kind(job, list(phrases or []), kind)[:1]:
        # The book's id is made from the phrase's own words, the way a typed
        # goal is, so the book added later asks for exactly these words.
        ident = wt.book_id("en", wt.topic_words(str(p.phrase)))
        cand = wt.candidate_by_id(ident) if ident else None
        if cand is not None:
            out.append((p, cand))
    return out


def would_send(job: dict, phrases: list, *, kind: str, row: dict) -> bool:
    """Would the Wikipedia or Wikibooks search ask anything for these phrases? Sends nothing."""
    oq = S._optional("puffpai.security.outbound_query")
    if oq is None or not all(oq.is_sendable(p) for p in phrases or []):
        return False
    return bool(_wiki_asks(job, phrases, kind))


def wikipedia(job: dict, phrases: list, *, kind: str, budget, row: dict,
              progress: Optional[Callable[[str], None]] = None) -> list[dict]:
    """Registry adapter for the ``wikipedia`` row: a topic book for a learn job."""
    oq = S._optional("puffpai.security.outbound_query")
    if oq is None or kind != "knowledge" or not isinstance(job, dict) or not job.get("learn"):
        return []
    for p in phrases or []:
        if not oq.is_sendable(p):
            raise TypeError("Wikipedia search takes only Sendable phrases")
    hosts = list(row.get("hosts") or [])
    out: list[dict] = []
    for p, cand in _wiki_asks(job, phrases, kind):
        if budget.later(WIKI_POOL) or budget.remaining(WIKI_POOL) == 0:
            continue
        url = str(row.get("query_template") or "").format(
            words=oq.url_quote(p), per_page=WIKI_PER_PAGE, min_stars=0, since="")
        got = S.fetch(url, hosts=hosts, max_bytes=WIKI_MAX_BYTES,
                      service=str(row.get("service") or ""), pool=WIKI_POOL, budget=budget,
                      progress=progress, robots=row.get("robots") != "on_request_only",
                      accept="application/json", cache_key=("wikipedia_search", url))
        titles = _wiki_titles(got.get("body")) if got.get("status") == 200 else []
        if len(titles) < WIKI_MIN_HITS:
            continue                          # too little on Wikipedia to make a book of
        rec = S.new_found(source="wikipedia", part="knowledge", id=str(cand["id"]),
                          name=str(cand.get("name") or ""),
                          what_it_is="Articles such as " + ", ".join(titles[:2])
                          + " and " + titles[2] + ".",
                          url=str(cand.get("url") or ""), mode_guess="other",
                          integrable=False,
                          trust={"floor_ok": True, "path": "", "pretick_ok": False,
                                 "reasons": []})
        out.append(rec)
    return out


__all__ = ["gutenberg", "kiwix", "openstax", "rows_gutenberg", "rows_kiwix", "rows_openstax",
           "wikipedia", "would_send"]
