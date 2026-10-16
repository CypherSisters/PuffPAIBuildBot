"""Finding knowledge for any goal: our own list, then the trusted libraries.

A goal can be anything - space, dinosaurs, a 2014 Honda Civic, knitting. This
module does not know any of those subjects and must not. It turns the goal (and
any search words the local model suggested) into words, asks each place in
turn, and returns candidates that each cite the catalog words that matched:

1. the curated list in ``config/source_registry.json`` (plus this build's owner
   overlay), matched by :func:`puffpai.source_registry.search`;
2. the Kiwix library: the shipped dated snapshot by default (works offline and
   never touches the network while a wizard is being drawn), or the live
   listing when the caller asks for it because the owner typed a search;
3. a Wikipedia topic book made from the goal's own words
   (:mod:`puffpai.catalogs.wikipedia_topics`; planned offline, its articles
   are found only after the owner picks it);
4. Project Gutenberg (:mod:`puffpai.catalogs.gutendex`; saved list offline,
   Gutendex when the owner searches);
5. OpenStax textbooks (:mod:`puffpai.catalogs.openstax`; saved list offline,
   its own book list when the owner searches);
6. Wikibooks how-to books (:mod:`puffpai.catalogs.wikibooks`) and
7. the Internet Archive's public-domain library scans
   (:mod:`puffpai.catalogs.internet_archive`): both only when the owner
   searches online (they have no saved list).

When the owner searches online, the libraries that are asked online (Kiwix,
OpenStax, Wikibooks, the Internet Archive) are asked AT ONCE, each on its own
thread, and the search waits at most :data:`LIBRARY_WAIT_S` for all of them.
A library that has not answered by then is given up on: its saved list is
used and one plain line says so ("Project Gutenberg didn't answer this time").
Asked one after another, a hung Gutendex would hold the Add page for 30 s on
every search.

Across the places, candidates are ordered by the same code score (a match in a
title counts more than one in a blurb), with the order above breaking ties.
The local model may reorder the candidates code found (``ask=``), by id only.

Two catalog-wide general encyclopedias (named in ``config/catalog_index.json``,
not in code) are added after the matches, because "knows everything about X"
is exactly what an encyclopedia is for. The builder's age changes the ORDER
only: a young builder sees books written for children first. Nothing is ever
hidden because of age.

The model never picks anything here. It may suggest search words; those words
go through the same filter as the owner's and are only ever used as search
terms. Every id returned came from a catalog this module read.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from typing import Any, Callable, Iterable, Optional

from puffpai import catalogs
from puffpai.catalogs import goal_words, kiwix_opds
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

MB = 1024 * 1024
DEFAULT_LIMIT = 12
LIVE_TERM_LIMIT = 4
#: The online search waits this long, at most, for all the libraries it asks
#: at once; one request to a library may take :data:`LIBRARY_REQUEST_S`.
LIBRARY_WAIT_S = 8.0
LIBRARY_REQUEST_S = 6.0
#: The libraries asked online, by the names an owner reads.
LIBRARY_NAMES = {"kiwix": "The Kiwix library", "gutenberg": "Project Gutenberg",
                 "openstax": "OpenStax", "wikibooks": "Wikibooks",
                 "archive": "The Internet Archive"}
#: Libraries asked only online: they have no saved list to fall back on.
LIVE_ONLY = ("wikibooks", "archive")
#: A match on the book's title or name is worth more than one in its blurb.
_WEIGHTS = {"title": 3, "name": 3, "tag": 2, "category": 2, "summary": 1}
#: One cited word is enough to be listed (a blurb that says "owners of cars" is
#: about cars); weaker matches sort below stronger ones rather than vanishing.
_SCORE_FLOOR = 1
#: Name parts that say nothing about the subject.
_NAME_NOISE = frozenset({"en", "all", "com", "org", "net", "stackexchange", "maxi",
                         "nopic", "mini", "mul", "www", "wiki"})
_SPLIT_RE = re.compile(r"[a-z0-9]+")

#: Proposal defaults: at most this many items start ticked, each no bigger than
#: this. Anything larger is listed with its size and starts unticked.
DEFAULT_ON_MAX_ITEMS = 2
DEFAULT_ON_MAX_BYTES = 500 * MB


def age_band(age: Any) -> str:
    """``child`` (under 13), ``teen`` (13-17), ``adult``, or ``""`` when unknown."""
    if isinstance(age, str):
        text = age.strip().lower()
        if text in ("child", "kid", "kids", "young"):
            return "child"
        if text in ("teen", "teenager"):
            return "teen"
        if text in ("adult", "grown-up", "grownup"):
            return "adult"
        try:
            age = int(text)
        except ValueError:
            return ""
    if isinstance(age, bool) or not isinstance(age, (int, float)):
        return ""
    if age <= 0:
        return ""
    if age < 13:
        return "child"
    if age < 18:
        return "teen"
    return "adult"


def enough_words(terms: list[str], matched: list[str]) -> bool:
    """Two of the words must match when there are two or more (one shared
    word, "civic" in "Social Civics", is not evidence about a Honda Civic)."""
    need = 1 if len(terms) < 2 else 2
    hit = {t for t in terms for w in matched if goal_words.term_hits_word(t, w)}
    return len(hit) >= need


def _entry_words(entry: dict[str, Any]) -> dict[str, list[str]]:
    name_parts = [w for w in _SPLIT_RE.findall(str(entry.get("name") or "").lower())
                  if w not in _NAME_NOISE]
    return {
        "title": goal_words.raw_words(str(entry.get("title") or "")),
        "name": name_parts,
        "tag": [w for t in entry.get("tags") or [] for w in goal_words.raw_words(str(t))],
        "category": goal_words.raw_words(str(entry.get("category") or "")),
        "summary": goal_words.raw_words(str(entry.get("summary") or "")),
    }


def score_entry(entry: dict[str, Any], terms: Iterable[str]) -> tuple[int, list[str]]:
    """(score, the catalog words that matched). Evidence or nothing."""
    fields = _entry_words(entry)
    score, matched = 0, []
    for term in terms:
        best, hit_word = 0, ""
        for field, words in fields.items():
            for word in words:
                if goal_words.term_hits_word(term, word) and _WEIGHTS[field] > best:
                    best, hit_word = _WEIGHTS[field], word
        if best:
            score += best
            if hit_word not in matched:
                matched.append(hit_word)
    return (score, matched) if score >= _SCORE_FLOOR else (0, [])


def _shelves() -> dict[str, list[str]]:
    """``config/subject_shelves.json``: shelf word -> subject words. {} if unreadable."""
    import json
    import os
    from puffpai.catalogs import live
    path = os.path.join(live.repo_root(), "config", "subject_shelves.json")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    shelves = data.get("shelves") if isinstance(data, dict) else None
    if not isinstance(shelves, dict):
        return {}
    return {str(k).lower(): [str(w).lower() for w in v if isinstance(w, str)]
            for k, v in shelves.items() if isinstance(v, list)}


def shelf_words(goal: str, words: Iterable[str]) -> list[str]:
    """The library shelves the goal's subjects sit on: knitting -> craft.

    Data, not code (``config/subject_shelves.json``). Only widens the search;
    a book found this way still cites the catalog word that matched.
    """
    have = [str(w).lower() for w in words]
    text = " " + " ".join(str(goal or "").lower().split()) + " "
    out: list[str] = []
    for shelf, members in _shelves().items():
        if shelf in have or shelf in out:
            continue
        for member in members:
            if " " in member or "-" in member:
                hit = f" {member} " in text
            else:
                hit = any(goal_words.term_hits_word(member, w)
                          or goal_words.term_hits_word(w, member) for w in have)
            if hit:
                out.append(shelf)
                break
    return out


def _kiwix_row() -> dict[str, Any]:
    return catalogs.catalog_row(kiwix_opds.CATALOG_ID) or {}


def _lang_ok(entry: dict[str, Any], lang: str) -> bool:
    langs = [x for x in str(entry.get("language") or "").split(",") if x]
    return not lang or not langs or lang in langs


def _smallest_per_book(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One candidate per book: the smallest downloadable issue, else the smallest."""
    best: dict[str, dict[str, Any]] = {}
    for cand in candidates:
        key = str(cand.get("book") or cand["id"])
        size = cand.get("size_bytes") or 0
        current = best.get(key)
        if current is None:
            best[key] = cand
            continue
        better = (bool(cand.get("fetchable")), -size) > \
                 (bool(current.get("fetchable")), -(current.get("size_bytes") or 0))
        if better:
            merged = dict(cand)
            merged["_score"] = max(cand.get("_score", 0), current.get("_score", 0))
            best[key] = merged
    return list(best.values())


def registry_candidate(entry: dict[str, Any], match_terms: Iterable[str] = ()) -> dict[str, Any]:
    """A curated or owner-added registry row in the shared Candidate shape."""
    size_mb = entry.get("size_mb")
    try:
        size = int(float(size_mb) * MB) if size_mb else None
    except (TypeError, ValueError):
        size = None
    commercial = entry.get("commercial_use")
    book = str(entry.get("kiwix_book") or "")
    return {
        "id": str(entry.get("id")),
        "kind": "knowledge",
        "name": str(entry.get("label") or entry.get("id"))[:80],
        "what_it_is": str(entry.get("what_it_is") or "")[:240],
        "source_catalog": ("Added by you" if entry.get("provenance") == "owner_added"
                           else "BuildBot's own list"),
        "url": "https://library.kiwix.org/" if book else (entry.get("source_url") or None),
        "host": kiwix_opds.DOWNLOAD_HOST if book else None,
        "sample_endpoint": None,
        "auth": "none",
        "license": str(entry.get("license") or "unknown"),
        "commercial_ok": commercial if isinstance(commercial, bool) else None,
        "size_bytes": size,
        "local_or_cloud": "local",
        "needs": [],
        "match_terms": [str(t) for t in match_terms],
        "verified_at": entry.get("license_checked"),
        "book": book,
        "flavour": str(entry.get("kiwix_flavour") or ""),
        "meta4_url": str(entry.get("meta4_url") or ""),
        "license_url": str(entry.get("license_url") or ""),
        "license_plain": kiwix_opds.plain_license(
            str(entry.get("license") or ""),
            commercial if isinstance(commercial, bool) else None),
        "fetchable": True,
        "why_not": "",
        "audience": "general",
        "category": "",
        "updated": "",
    }


def _kiwix_candidates(entries: list[dict[str, Any]], terms: list[str], lang: str,
                      verified_at: Optional[str]) -> list[dict[str, Any]]:
    row = _kiwix_row()
    rules = catalogs.license_rules(kiwix_opds.CATALOG_ID)
    audience = row.get("audience") if isinstance(row.get("audience"), dict) else {}
    out = []
    for entry in entries:
        if not _lang_ok(entry, lang):
            continue
        score, matched = score_entry(entry, terms)
        if not score:
            continue
        cand = kiwix_opds.to_candidate(entry, rules, match_terms=matched,
                                       verified_at=verified_at, audience=audience)
        cand["_score"] = score
        cand["reason"] = "Matched on: " + ", ".join(matched) + "."
        out.append(cand)
    return out


def _general_references(entries: list[dict[str, Any]], lang: str,
                        verified_at: Optional[str]) -> list[dict[str, Any]]:
    row = _kiwix_row()
    rules = catalogs.license_rules(kiwix_opds.CATALOG_ID)
    audience = row.get("audience") if isinstance(row.get("audience"), dict) else {}
    out = []
    for ref in row.get("general_reference") or []:
        if not isinstance(ref, dict):
            continue
        book = str(ref.get("book") or "")
        for entry in entries:
            if entry.get("name") != book or not _lang_ok(entry, lang):
                continue
            cand = kiwix_opds.to_candidate(entry, rules, verified_at=verified_at,
                                           audience=audience)
            cand["_score"] = 0
            cand["general"] = True
            cand["reason"] = str(ref.get("why") or "A general encyclopedia.")[:160]
            out.append(cand)
    return out


#: Which place a candidate came from, in the order the places are asked. Used
#: only to break a tie in the code score.
TIER_RANK = {"curated": 0, "kiwix": 1, "wikipedia_topic": 2, "gutenberg": 3,
             "openstax": 4, "wikibooks": 5, "archive": 6}
#: The places that hand over text rather than a whole book file.
TEXT_TIERS = ("wikipedia_topic", "gutenberg", "openstax", "wikibooks", "archive")


def tier_of(cand: dict[str, Any]) -> str:
    tier = str(cand.get("tier") or "")
    if tier:
        return tier
    return "kiwix" if str(cand.get("id") or "").startswith("kiwix:") else "curated"


def _order(candidates: list[dict[str, Any]], band: str) -> list[dict[str, Any]]:
    audience_rank = {"kids": 0, "general": 1, "technical": 2}

    def key(cand: dict[str, Any]) -> tuple:
        general = 1 if cand.get("general") else 0
        size = cand.get("size_bytes") or 0
        tier = TIER_RANK.get(tier_of(cand), 9)
        # After the match: the more people downloaded it, the earlier.
        liked = -int(cand.get("downloads") or 0)
        if band == "child":
            return (audience_rank.get(cand.get("audience"), 1), general,
                    -int(cand.get("_score", 0)), liked, tier, size)
        # Everyone else: matches first, and books written for children after
        # the grown-up ones at the same level. Order only; nothing is dropped.
        kids_last = 1 if cand.get("audience") == "kids" else 0
        return (general, kids_last, -int(cand.get("_score", 0)), liked, tier, size)

    return sorted(candidates, key=key)


def find(goal: str = "", terms: Iterable[str] = (), *, age: Any = None,
         lang: str = kiwix_opds.DEFAULT_LANG, live: bool = False,
         getter: Optional[kiwix_opds.Getter] = None, limit: int = DEFAULT_LIMIT,
         include_general: bool = True, registry: Optional[dict[str, Any]] = None,
         snapshot: Optional[dict[str, Any]] = None,
         reach_getter: Optional[kiwix_opds.Getter] = None,
         ask: Optional[Any] = None,
         on_library: Optional[Callable[[str, str], None]] = None,
         per_library: Optional[int] = None) -> dict[str, Any]:
    """Candidates for a goal. Never raises; a failed live search falls back to the snapshot.

    Returns ``{terms, candidates, searched, note, snapshot_date, not_searched,
    tiers}``. ``candidates`` use the shared Candidate shape plus ``fetchable``,
    ``why_not``, ``reason``, ``license_plain`` and ``tier``. ``tiers`` says,
    per place, how many it gave and whether it was asked live.

    ``getter`` answers the Kiwix library's requests; ``reach_getter`` answers
    Project Gutenberg's and OpenStax's. A caller that hands in only a Kiwix
    getter gets the saved lists for the others (a fake for one library is
    never sent another library's requests). ``ask`` is an optional local-model
    call used only to reorder candidates code found, by id. ``on_library``
    hears ``(library, state)`` as each library asked online answers
    (:func:`ask_libraries`). ``per_library`` asks Project Gutenberg, OpenStax,
    Wikibooks and the Internet Archive for that many books each instead of
    their usual few (the find step ranks a wider pool itself).
    """
    from puffpai import source_registry

    own = goal_words.goal_terms(goal, extra=[str(t) for t in terms])
    words = own + shelf_words(goal, own)
    band = age_band(age)
    result: dict[str, Any] = {"terms": words, "candidates": [], "searched": "",
                              "note": "", "snapshot_date": None,
                              "not_searched": catalogs.not_searched_line()}
    if not words:
        result["note"] = ("Name a subject - a hobby, a thing, a place - and I can "
                          "look for books about it.")
        return result

    # 1. our own list (and the owner's overlay)
    curated: list[dict[str, Any]] = []
    books_seen: set[str] = set()
    for hit in source_registry.search(" ".join(words), registry):
        entry = source_registry.get_source(str(hit.get("id")), registry)
        if entry is None:
            continue
        cand = registry_candidate(entry, hit.get("matched_terms") or [])
        cand["_score"] = 100 + int(hit.get("score") or 0)
        cand["reason"] = "On BuildBot's own list. Matched on: " + \
            ", ".join(hit.get("matched_terms") or []) + "."
        curated.append(cand)
        if cand["book"]:
            books_seen.add(cand["book"])

    # 2. the Kiwix library
    snap = snapshot if snapshot is not None else kiwix_opds.load_snapshot()
    result["snapshot_date"] = snap.get("taken_at")
    entries: list[dict[str, Any]] = []
    verified_at = snap.get("taken_at")
    reach_live = bool(live) and (getter is None or reach_getter is not None)
    asked: dict[str, tuple[str, Any]] = {}
    if live:
        jobs: dict[str, Callable[[], Any]] = {
            "kiwix": lambda: _kiwix_live(words, lang, getter)}
        if reach_live and lang in ("", kiwix_opds.DEFAULT_LANG):
            from puffpai.catalogs import internet_archive, openstax, wikibooks
            # Project Gutenberg is read from the catalog saved on this computer
            # it is not asked online, so it is not in this list.
            jobs["openstax"] = lambda: _live_only(openstax, words, reach_getter, per_library)
            # These two search engines are asked for the goal's own words: a
            # shelf word ("crafts" for knitting) would make every book need it.
            # Wikibooks keeps its few: every book it lists costs a request
            # for its size, and its site is asked at most once a second.
            jobs["wikibooks"] = lambda: _live_only(wikibooks, own, reach_getter)
            jobs["archive"] = lambda: _live_only(internet_archive, own, reach_getter,
                                                 per_library)
        asked = ask_libraries(jobs, on_library=on_library)
        state, got = asked["kiwix"]
        if state == "done":
            entries = got
            result["searched"] = "live"
            verified_at = _today()
        elif isinstance(got, kiwix_opds.LiveLookupOff):
            entries = list(snap.get("entries") or [])
            result["searched"] = "snapshot"
            result["note"] = (f"BuildBot is set not to ask the Kiwix library online, so "
                              f"this used the list saved on {snap.get('taken_at')}.")
        else:
            entries = list(snap.get("entries") or [])
            result["searched"] = "snapshot"
            result["note"] = _no_answer_line("kiwix", got)
    else:
        entries = list(snap.get("entries") or [])
        result["searched"] = "snapshot"

    found = [c for c in _kiwix_candidates(entries, words, lang, verified_at)
             if c["book"] not in books_seen]
    found = _smallest_per_book(found)
    tiers: dict[str, Any] = {"registry": {"found": len(curated), "searched": "saved"},
                             "kiwix": {"found": len(found),
                                       "searched": result["searched"]}}
    if lang in ("", kiwix_opds.DEFAULT_LANG):
        found += _reach_candidates(goal, terms, words, band, live=reach_live,
                                   getter=reach_getter, tiers=tiers, result=result,
                                   asked=asked, per_library=per_library)
    result["tiers"] = tiers
    if ask is not None:
        found = _rerank(found, goal, ask)
    if include_general:
        # the general encyclopedias come from the full listing, not the search
        pool = entries if not live else (entries + list(snap.get("entries") or []))
        refs = _smallest_per_book(_general_references(pool, lang, verified_at))
        matched_books = {c["book"] for c in found} | books_seen
        found += [c for c in refs if c["book"] not in matched_books]

    # A planned topic book is not evidence that a library has the subject: it
    # is only a promise to look. It does not count as a specific match here.
    specific = bool(curated) or any(
        not c.get("general") and tier_of(c) != "wikipedia_topic" for c in found)
    if not specific and found:
        planned = any(tier_of(c) == "wikipedia_topic" for c in found)
        general = (f"Nothing in the library is only about "
                   f"{' or '.join(words[:3])}. A general encyclopedia still "
                   f"covers it" + (", and a Wikipedia book can be made from "
                                   "your words:" if planned else ":"))
        result["note"] = (str(result.get("note") or "") + " " + general).strip()
    ordered = _order(curated, band) + _order(found, band)
    if band == "child":
        ordered = _order(ordered, band)
    for cand in ordered[:limit]:
        cand["score"] = int(cand.pop("_score", 0) or 0)
        result["candidates"].append(cand)
    if not result["candidates"] and not result["note"]:
        result["note"] = ("Nothing in the libraries matched those words. Try a "
                          "broader word (for example the hobby rather than the brand).")
    return result


def _reach_candidates(goal: str, terms: Iterable[str], words: list[str], band: str,
                      *, live: bool, getter: Optional[kiwix_opds.Getter],
                      tiers: dict[str, Any], result: dict[str, Any],
                      asked: Optional[dict[str, tuple[str, Any]]] = None,
                      per_library: Optional[int] = None) -> list[dict[str, Any]]:
    """The Wikipedia topic book, Project Gutenberg and OpenStax, scored like Kiwix.

    Each place fails on its own: one that cannot be read adds a plain note and
    nothing else, never an exception.
    """
    from puffpai.catalogs import gutendex, internet_archive, openstax, wikibooks
    from puffpai.catalogs import wikipedia_topics

    out: list[dict[str, Any]] = []
    notes: list[str] = []
    try:
        topic = wikipedia_topics.candidates_for(goal, [str(t) for t in terms],
                                                child_first=band == "child")
    except Exception as exc:  # noqa: BLE001 - one place never sinks the search
        logger.debug("topic book not planned: %s", exc)
        topic = []
    for cand in topic:
        score, matched = score_entry({"title": " ".join(cand["words"])}, words)
        cand["_score"] = score
        cand["match_terms"] = matched or list(cand["words"])
        cand["reason"] = ("Made from your own words: " + ", ".join(cand["words"])
                          + ". The articles are found when you pick it.")
        out.append(cand)
    tiers["wikipedia_topic"] = {"found": len(topic), "searched": "planned"}
    for name, module in (("gutenberg", gutendex), ("openstax", openstax),
                         ("wikibooks", wikibooks), ("archive", internet_archive)):
        answer = (asked or {}).get(name)
        if name in LIVE_ONLY and answer is None:
            continue          # not asked online: no saved list to read
        if answer is not None and answer[0] == "done" and isinstance(answer[1], dict):
            got = answer[1]
        elif answer is not None and isinstance(answer[1], _SavedListUsed):
            # It was asked and did not answer: its saved list, and one plain
            # line (never the socket's own words).
            got = dict(answer[1].got)
            got["note"] = _no_answer_line(name, got.get("note"))
        elif answer is not None:
            # Asked online and not answered in time: the saved list, said so.
            got = _saved_only(module, words, per_library)
            got["note"] = _no_answer_line(name, answer[1])
        else:
            try:
                got = module.find(words, live=live, getter=getter,
                                  **_limit(per_library))
            except Exception as exc:  # noqa: BLE001
                logger.debug("%s not searched: %s", name, exc)
                got = {"candidates": [], "searched": "not searched",
                       "note": f"{module.__name__.rsplit('.', 1)[-1]} could not be read."}
        out.extend(got.get("candidates") or [])
        tiers[name] = {"found": len(got.get("candidates") or []),
                       "searched": got.get("searched")}
        if got.get("note"):
            notes.append(str(got["note"]))
    if notes:
        result["note"] = (str(result.get("note") or "") + " " + " ".join(notes)).strip()
    return out


class _SavedListUsed(Exception):
    """A library asked online fell back to its saved list (it did not answer,
    or it is switched off); ``got`` is that saved-list answer."""

    def __init__(self, got: dict[str, Any]):
        super().__init__(str(got.get("note") or "saved list used"))
        self.got = got


def _limit(per_library: Optional[int]) -> dict[str, Any]:
    return {"limit": int(per_library)} if per_library else {}


def _live_only(module: Any, words: list[str], getter: Any,
               per_library: Optional[int] = None) -> dict[str, Any]:
    """``module.find`` asked online; raises :class:`_SavedListUsed` when it
    answered from its saved list instead, so progress says it did not answer."""
    got = module.find(words, live=True, getter=getter, **_limit(per_library))
    if got.get("searched") != "live":
        raise _SavedListUsed(got)
    return got


def _saved_only(module: Any, words: list[str],
                per_library: Optional[int] = None) -> dict[str, Any]:
    try:
        return dict(module.find(words, live=False, **_limit(per_library)))
    except Exception as exc:  # noqa: BLE001 - one place never sinks the search
        logger.debug("saved list not read: %s", exc)
        return {"candidates": [], "searched": "not searched", "note": ""}


def _kiwix_live(words: list[str], lang: str,
                getter: Optional[kiwix_opds.Getter]) -> list[dict[str, Any]]:
    """The Kiwix library's live answers for the first few words, de-duplicated."""
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for term in words[:LIVE_TERM_LIMIT]:
        for entry in kiwix_opds.search_live(term, lang=lang, getter=getter):
            key = (entry["name"], entry.get("flavour") or "", entry.get("updated") or "")
            if key not in seen:
                seen.add(key)
                entries.append(entry)
    return entries


def _no_answer_line(library: str, exc: Any) -> str:
    """One plain line for a library that was asked and did not answer."""
    name = LIBRARY_NAMES.get(library, "One library")
    if library in LIVE_ONLY:
        if exc is not None and "switched off" in str(exc):
            return f"{name} is switched off under What leaves your computer."
        return f"{name} didn't answer this time. Try again later."
    if exc is not None and "switched off" in str(exc):
        return (f"{name} is switched off under What leaves your computer, so "
                f"its saved list was used.")
    return (f"{name} didn't answer this time, so its saved list was used. "
            f"Try again later.")


def ask_libraries(jobs: dict[str, Callable[[], Any]], *,
                  wait: Optional[float] = None,
                  on_library: Optional[Callable[[str, str], None]] = None
                  ) -> dict[str, tuple[str, Any]]:
    """Ask every library at once; wait at most *wait* seconds for all of them.

    Returns ``{library: (state, value)}``: ``("done", answer)``,
    ``("failed", exception)`` or ``("no_answer", None)`` for one still asking
    when the wait ran out (its thread is left to finish on its own, and each
    of its requests waits at most :data:`LIBRARY_REQUEST_S`). ``on_library``
    hears ``(library, "asking" | "answered" | "no_answer")``; it never breaks
    the search.
    """
    from puffpai import source_fetch

    wait = LIBRARY_WAIT_S if wait is None else float(wait)

    def tell(name: str, state: str) -> None:
        if on_library is None:
            return
        try:
            on_library(name, state)
        except Exception:  # noqa: BLE001 - progress never sinks the search
            logger.debug("library progress not told", exc_info=True)

    answers: "queue.Queue[tuple[str, str, Any]]" = queue.Queue()

    def run(name: str, job: Callable[[], Any]) -> None:
        with source_fetch.quick_timeout(LIBRARY_REQUEST_S):
            try:
                answers.put((name, "done", job()))
            except Exception as exc:  # noqa: BLE001 - said, never raised
                answers.put((name, "failed", exc))

    for name in jobs:
        tell(name, "asking")
    for name, job in jobs.items():
        threading.Thread(target=run, args=(name, job), daemon=True,
                         name=f"library-{name}").start()
    out: dict[str, tuple[str, Any]] = {}
    deadline = time.monotonic() + wait
    while len(out) < len(jobs):
        left = deadline - time.monotonic()
        if left <= 0:
            break
        try:
            name, state, value = answers.get(timeout=left)
        except queue.Empty:
            break
        out[name] = (state, value)
        tell(name, "answered" if state == "done" else "no_answer")
    for name in jobs:
        if name not in out:
            out[name] = ("no_answer", None)
            tell(name, "no_answer")
    return out


def _rerank(found: list[dict[str, Any]], goal: str, ask: Any) -> list[dict[str, Any]]:
    """Let the local model reorder what code found (by id only), keeping scores honest.

    Reordering is expressed as a small score bonus so the age and general rules
    still apply afterwards; the model can neither add nor remove a candidate.
    """
    from puffpai.catalogs import live as live_catalog

    specific = [c for c in found if not c.get("general")]
    if len(specific) < 2:
        return found
    shaped = [{"id": c["id"], "name": c.get("name") or c["id"]} for c in specific]
    order = [c["id"] for c in live_catalog.rerank(shaped, goal, ask, keep=len(shaped))]
    bonus = {ident: len(order) - pos for pos, ident in enumerate(order)}
    for cand in specific:
        cand["_score"] = int(cand.get("_score", 0)) * 100 + bonus.get(cand["id"], 0)
    return found


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def size_words(size_bytes: Optional[int]) -> str:
    if not isinstance(size_bytes, int) or size_bytes <= 0:
        return "size not known"
    if size_bytes >= 1024 * MB:
        return f"about {size_bytes / (1024 * MB):.1f} GB"
    if size_bytes >= MB:
        return f"about {size_bytes / MB:.0f} MB"
    return "under 1 MB"


def download_words(cand: dict[str, Any]) -> str:
    """The size an owner reads for one candidate: known, a ceiling, or unknown."""
    size = cand.get("size_bytes")
    if isinstance(size, int) and size > 0:
        return size_words(size)
    ceiling = cand.get("size_ceiling")
    if isinstance(ceiling, int) and ceiling > 0:
        return "up to " + size_words(ceiling).replace("about ", "")
    return size_words(None)


def _where_words(cand: dict[str, Any]) -> str:
    """Where a candidate is fetched from, in plain words (no internal names)."""
    tier = tier_of(cand)
    if tier == "wikipedia_topic":
        return (f"{cand.get('source_catalog') or 'Wikipedia'} ({cand.get('host')}), "
                f"one article a second")
    if tier == "gutenberg":
        return "Project Gutenberg (www.gutenberg.org)"
    if tier == "openstax":
        pages = cand.get("pages")
        read = f", reading about {pages} web pages" if isinstance(pages, int) else ""
        return f"OpenStax (openstax.org){read}"
    if tier == "wikibooks":
        pages = cand.get("pages")
        read = f", reading {pages} pages" if isinstance(pages, int) else ""
        return f"Wikibooks (en.wikibooks.org){read}"
    if tier == "archive":
        return "the Internet Archive (archive.org)"
    return "the Kiwix library (download.kiwix.org and the mirrors it lists)"


def candidate_lines(cand: dict[str, Any]) -> list[str]:
    """Plain words for one candidate: what it is, size, license, where from."""
    # The id is shown only inside the command that uses it (plain words: no
    # bracketed ids in owner text).
    lines = [f"{cand['name']}",
             f"    What it is: {cand.get('what_it_is') or cand['name']}",
             f"    Download:   {download_words(cand)}",
             f"    License:    {cand.get('license_plain')}"]
    if tier_of(cand) in TEXT_TIERS:
        lines.append(f"    From:       {_where_words(cand)}")
    if cand.get("reason"):
        lines.append(f"    Why:        {cand['reason']}")
    if not cand.get("fetchable"):
        lines.append(f"    Cannot be added: {cand.get('why_not')}")
    else:
        lines.append(f"    To add it:  puffpai sources add {cand['id']}")
    return lines


#: The summary lists at most this many books that matched the goal, and this
#: many general encyclopedias after them.
SUMMARY_MATCHES = 3
SUMMARY_GENERAL = 2


def _item_detail(cand: dict[str, Any]) -> str:
    what = cand.get("what_it_is") or cand["name"]
    size = download_words(cand)
    tier = tier_of(cand)
    if tier == "wikipedia_topic":
        how = (f"Fetched once from {cand.get('host')} ({size} of text); it sends "
               f"only the words {', '.join(cand.get('words') or [])} and the "
               f"article names.")
    elif tier == "gutenberg":
        how = f"Fetched once from www.gutenberg.org ({size})."
    elif tier == "openstax":
        how = f"Read once from openstax.org ({size} of text kept)."
    elif tier == "wikibooks":
        how = f"Read once from en.wikibooks.org ({size} of text)."
    elif tier == "archive":
        how = f"Fetched once from archive.org ({size})."
    else:
        how = f"Downloads {size} from the Kiwix library once."
    return (f"{what} {how} After that it is read on this computer and needs no "
            f"internet. License: {cand.get('license_plain')}.")


def fits_goal(cand: dict[str, Any], goal: str, shelves: Iterable[str] = ()) -> bool:
    """Does a found book fit the bot's goal as the owner typed it?

    The same rule as every other place that offers something
    (:func:`goal_words.fits_request`): its title and opening, or the words
    the catalog files it under, must hold one of the goal's phrases, not one
    stray word of it. A library shelf the goal sits on counts when it is in
    the book's title (the whole book is about it). A Wikipedia book made
    from the owner's own words always fits: it is those words.
    """
    if tier_of(cand) == "wikipedia_topic":
        return True
    name = str(cand.get("name") or "")
    return goal_words.fits_request(
        goal, f"{name} {cand.get('what_it_is') or ''}",
        subjects=[str(t) for t in cand.get("subjects") or cand.get("match_terms") or ()],
        shelves=shelves, title=name)


def proposal_items(goal: str, terms: Iterable[str] = (), *, age: Any = None,
                   limit: int = 6) -> list[dict[str, Any]]:
    """Knowledge lines for the wizard's one summary (the shared ProposalItem shape).

    Read-only and offline: our own list, the shipped Kiwix, Project Gutenberg
    and OpenStax lists, and the planned Wikipedia topic book. Only things that
    can actually be fetched are listed (a book whose license is not known is
    never put in front of a "Looks good"). At most :data:`SUMMARY_MATCHES`
    books that matched the goal and :data:`SUMMARY_GENERAL` general
    encyclopedias. What starts ticked is decided by
    :func:`puffpai.wizard.sources_plan.knowledge_default_on`. Every item
    fetches, so every item needs the owner's yes.
    """
    from puffpai.wizard import sources_plan

    found = find(goal, terms, age=age, live=False, limit=max(limit * 2, 16))
    usable = [c for c in found["candidates"] if c.get("fetchable")]
    own = goal_words.goal_terms(goal)
    shelves = shelf_words(goal, own)
    specific = [c for c in usable if not c.get("general")
                and fits_goal(c, goal, shelves)]
    matched = specific[:SUMMARY_MATCHES]
    topic = next((c for c in specific if tier_of(c) == "wikipedia_topic"), None)
    if topic is not None and topic not in matched and matched:
        # The topic book is the one line made from the owner's own words, so it
        # always keeps a seat; the weakest other match gives way.
        matched = matched[:-1] + [topic]
    general = [c for c in usable if c.get("general")][:SUMMARY_GENERAL]
    shown = (matched + general)[:max(1, int(limit))]
    ticked = sources_plan.knowledge_default_on(shown, goal)
    items: list[dict[str, Any]] = []
    for cand in shown:
        size = cand.get("size_bytes")
        if not isinstance(size, int) or size <= 0:
            ceiling = cand.get("size_ceiling")
            size = ceiling if isinstance(ceiling, int) and ceiling > 0 else None
        size_part = f" ({download_words(cand)})"
        name = str(cand["name"])
        if len(name) + len(size_part) > 80:
            # the size always shows; the name gives way
            name = name[:80 - len(size_part) - 3].rstrip() + "..."
        label = name + size_part
        items.append({
            "id": "knowledge:" + str(cand["id"]),
            "section": "knowledge",
            "label": label,
            "detail": _item_detail(cand)[:400],
            "size_bytes": size,
            "outside_host": str(cand.get("host") or "") or kiwix_opds.DOWNLOAD_HOST,
            "default_on": cand["id"] in ticked,
            "needs_yes": True,
            "apply": {"kind": "knowledge.add", "ref": str(cand["id"])},
        })
    return items


def _reach_by_id(text: str, *, live: bool = False,
                 getter: Optional[kiwix_opds.Getter] = None) -> Optional[dict[str, Any]]:
    """A Wikipedia topic book, Gutenberg book or OpenStax textbook by its id."""
    from puffpai.catalogs import gutendex, internet_archive, openstax, wikibooks
    from puffpai.catalogs import wikipedia_topics

    if text.startswith(wikibooks.ID_PREFIX + ":"):
        return wikibooks.candidate_by_id(text, live=live, getter=getter)
    if text.startswith(internet_archive.ID_PREFIX + ":"):
        return internet_archive.candidate_by_id(text, live=live, getter=getter)
    if text.startswith(wikipedia_topics.ID_PREFIX + ":"):
        return wikipedia_topics.candidate_by_id(text)
    if text.startswith(gutendex.ID_PREFIX + ":"):
        return gutendex.candidate_by_id(text, live=live, getter=getter)
    if text.startswith(openstax.ID_PREFIX + ":"):
        return openstax.candidate_by_id(text, live=live, getter=getter)
    return None


def candidate_by_id(ident: str, *, live: bool = False,
                    getter: Optional[kiwix_opds.Getter] = None,
                    reach_getter: Optional[kiwix_opds.Getter] = None
                    ) -> Optional[dict[str, Any]]:
    """One candidate by id, from our list, the owner overlay, or the Kiwix library.

    The id must be one a catalog gave out: a registry id, ``kiwix:<book>``
    (``kiwix:<book>:<flavour>``), ``wikipedia:<en|simple>:<words>``,
    ``gutenberg:<number>``, ``openstax:<book>``, ``wikibooks:<page number>``
    or ``archive:<id>``. Nothing else resolves.
    """
    from puffpai import source_registry

    text = str(ident or "").strip()
    entry = source_registry.get_source(text)
    if entry is not None:
        return registry_candidate(entry)
    if text.split(":", 1)[0] in ("wikipedia", "gutenberg", "openstax", "wikibooks",
                                 "archive"):
        return _reach_by_id(text, live=live, getter=reach_getter)
    parsed = kiwix_opds.split_book_id(text)
    if parsed is None:
        return None
    name, flavour = parsed
    row = _kiwix_row()
    rules = catalogs.license_rules(kiwix_opds.CATALOG_ID)
    audience = row.get("audience") if isinstance(row.get("audience"), dict) else {}
    snap = kiwix_opds.load_snapshot()
    listing = kiwix_opds.newest(snap.get("entries") or [], name, flavour)
    verified = snap.get("taken_at")
    if live:
        try:
            listing = kiwix_opds.resolve_book(name, flavour, getter=getter)
            verified = _today()
        except kiwix_opds.CatalogError:
            pass
    if listing is None:
        return None
    return kiwix_opds.to_candidate(listing, rules, verified_at=verified,
                                   audience=audience)


__all__ = ["age_band", "score_entry", "registry_candidate", "find", "size_words",
           "download_words", "candidate_lines", "proposal_items", "candidate_by_id",
           "fits_goal", "shelf_words", "enough_words", "LIVE_ONLY",
           "tier_of", "TIER_RANK", "TEXT_TIERS", "SUMMARY_MATCHES", "SUMMARY_GENERAL",
           "DEFAULT_ON_MAX_BYTES", "DEFAULT_ON_MAX_ITEMS"]
