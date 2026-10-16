"""The plan Sources writes and Integrate carries out. No bytes move here.

One Zone 2 step is split in two: Sources decides *what
this bot should learn from* and Integrate decides *what is actually fetched
now*. Both halves need the same handful of facts about a registry row - its
size in bytes, whether it is documents or a live source, whether it can be
fetched at all - so they live here rather than in either package.

Three rules this module exists to keep:

* **Nothing here opens a socket.** Every figure is read from the shipped
  registry, so a plan can be drawn on an air-gapped machine.
* **A size is either known or it is not.** A row without ``size_mb`` carries no
  byte figure at all and drags the whole estimate's basis to ``unknown``,
  because a total that quietly treats "no idea" as zero is a total that lies.
* **Paid and keyed rows are listed and never selectable**: this build has
  nowhere safe to put a key, so the honest thing is
  to name the source, say what it costs, and refuse to pretend it is one click
  away.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

from typing import Any, Optional

MB = 1024 * 1024

#: The one threshold in the split. A source bigger than this is deferred by
#: default rather than downloaded while somebody waits at a wizard, and a plan
#: totalling more than this makes its card consequential (``heavy_bytes``).
#: There was no such number in ``cards.py`` to reuse; it is stated once, here,
#: so Sources and Integrate cannot disagree about what "heavy" means.
HEAVY_BYTES = 2 * 1024 * 1024 * 1024

#: The plain words a paid or keyed row carries on the card, and the status it
#: is recorded under. Recorded, never entered.
NO_KEY_PATH = "needs a sign-up key (not offered yet)"
KEYED_NOTE = ("costs money / needs a key; this build has nowhere safe to put "
              "the key yet")


def size_bytes(size_mb: Any) -> Optional[int]:
    """Bytes for a registry row's declared size, or ``None`` when unknown."""
    try:
        value = float(size_mb)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    return int(value * MB)


def size_label(size_mb: Any) -> str:
    """Plain words for a size. Unknown stays unknown, and says so.

    One formatter for every size an owner reads (``cards.format_bytes``), so a
    row cannot say "about 57.2 GB" in words and 61.4 GB in the estimate beside
    it.
    """
    from puffpai.wizard import cards

    value = size_bytes(size_mb)
    if value is None:
        return "size not known"
    return f"about {cards.format_bytes(value)}"


def form_of(entry: dict[str, Any]) -> str:
    """``documents`` or ``live_source``, the way :mod:`build_sources` means it."""
    from puffpai.knowledge import build_sources

    return (build_sources.FORM_LIVE_SOURCE
            if str(entry.get("kind") or "") == "docs"
            else build_sources.FORM_DOCUMENTS)


def needs_key(entry: dict[str, Any]) -> bool:
    """Does this row need a credential? Nothing in the shipped registry does.

    Read from the row rather than from a list of ids on purpose: the registry
    grows, and a keyed row added to it must be caught by the code that refuses
    keys rather than by somebody remembering to update this module.
    """
    return bool(entry.get("requires_key") or entry.get("api_key")
                or entry.get("auth"))


def costs_money(entry: dict[str, Any]) -> bool:
    return bool(entry.get("paid") or entry.get("subscription")
                or entry.get("cost"))


def why_not_fetchable(entry: dict[str, Any]) -> str:
    """Why this row can never be selected here, or ``""`` if it can."""
    keyed, paid = needs_key(entry), costs_money(entry)
    if keyed and paid:
        return KEYED_NOTE
    if keyed:
        return ("needs a key; this build has nowhere safe to put the key yet")
    if paid:
        return ("costs money; a subscription is yours to hold, and this build "
                "has nowhere safe to put the key yet")
    return ""


def row(entry: dict[str, Any], *, reason: str = "",
        matched_terms: Optional[list[str]] = None,
        selected_by: Optional[list[str]] = None) -> dict[str, Any]:
    """One candidate as the card and the plan both carry it.

    Everything the owner has to see before a byte moves is on this row:
    license, size, commercial use, the address, and which word of their goal
    earned it a place.
    """
    source_id = str(entry.get("id") or "")
    why_not = why_not_fetchable(entry)
    return {
        "id": source_id,
        "label": str(entry.get("label") or source_id),
        "kind": str(entry.get("kind") or "unknown"),
        "form": form_of(entry),
        "domain": str(entry.get("domain") or ""),
        "what_it_is": str(entry.get("what_it_is") or ""),
        "license": str(entry.get("license") or ""),
        "license_url": str(entry.get("license_url") or ""),
        "commercial_use": bool(entry.get("commercial_use", False)),
        "size_mb": entry.get("size_mb"),
        "bytes": size_bytes(entry.get("size_mb")),
        "size_label": size_label(entry.get("size_mb")),
        "source_url": str(entry.get("source_url") or ""),
        "kiwix_book": str(entry.get("kiwix_book") or ""),
        "matched_terms": [str(t) for t in (matched_terms or [])],
        "reason": reason,
        "selected_by": [str(p) for p in (selected_by or [])],
        "selectable": not why_not,
        "why_not": why_not,
        "status": NO_KEY_PATH if why_not else "",
    }


def option_label(item: dict[str, Any]) -> str:
    """The one line an owner reads before choosing a source. Plain words."""
    use = ("free to use, even in something you sell" if item["commercial_use"]
           else "personal use only")
    license_words = (f"{item['license']}, {use}" if item["license"]
                     else "license not known")
    parts = [item["label"], item["size_label"] + " to download", license_words]
    if item["form"] == "live_source":
        parts.append("a web page, not something it can learn from")
    return " - ".join(p for p in parts if p)


def plan_rows(goal: str, packs: list[str], *,
              registry: Optional[dict[str, Any]] = None,
              terms: Any = (), age: Any = None,
              use_library: Optional[bool] = None) -> dict[str, Any]:
    """The deterministic half of Sources: registry search plus the pack picks.

    Two inputs, both the owner's: the goal they typed, matched against the
    registry by today's code (which cites the word that matched), and the
    ``pack:*`` ids they chose in the interview, mapped onto registry rows by
    :func:`build_sources.pack_source_ids` (a data table in the catalog index).

    ``offered`` stays exactly what today's sourcing step recorded - the search
    result, in search order - because the build manifest and
    :func:`build_sources.source_status` are already keyed on it. Pack-mapped
    rows are added to ``items`` for the card and not to ``offered``.
    """
    from puffpai import source_registry
    from puffpai.knowledge import build_sources

    if use_library is None:
        # A caller that hands in its own registry (a test fixture) gets exactly
        # that registry and nothing from the wider library.
        use_library = registry is None
    terms = [str(t) for t in (terms or ())]
    result = source_registry.search_summary(goal, registry)
    if terms:
        # The model's search words find more on our own list too; they are
        # searched exactly like the owner's words and cite what matched.
        known = {str(c.get("id")) for c in result.get("candidates") or []}
        extra = [c for c in source_registry.search(" ".join(terms), registry)
                 if str(c.get("id")) not in known]
        if extra:
            result = dict(result)
            result["candidates"] = list(result.get("candidates") or []) + extra
            result["message"] = None
    items: list[dict[str, Any]] = []
    seen: dict[str, dict[str, Any]] = {}
    offered: list[str] = []

    for candidate in result.get("candidates") or []:
        entry = source_registry.get_source(str(candidate.get("id")), registry)
        if entry is None:
            continue
        item = row(entry,
                   reason=str(candidate.get("reason") or ""),
                   matched_terms=candidate.get("matched_terms") or [])
        offered.append(item["id"])
        items.append(item)
        seen[item["id"]] = item

    unmapped: list[str] = []
    for pack in packs:
        mapped = build_sources.pack_source_ids(pack)
        if not mapped:
            unmapped.append(pack)
            continue
        for source_id in mapped:
            known = seen.get(source_id)
            if known is not None:
                if pack not in known["selected_by"]:
                    known["selected_by"].append(pack)
                continue
            entry = source_registry.get_source(source_id, registry)
            if entry is None and use_library:
                entry = _library_entry(source_id)
            if entry is None:
                continue
            item = row(entry,
                       reason=f"You chose {pack} when you said what this bot "
                              f"is for.",
                       selected_by=[pack])
            items.append(item)
            seen[item["id"]] = item

    taken = set(seen)
    for source_id in seen:
        entry = (source_registry.get_source(source_id, registry)
                 or (_library_entry(source_id) if use_library else None) or {})
        if entry.get("kiwix_book"):
            taken.add("book:" + str(entry["kiwix_book"]))
    library = library_rows(goal, terms, age=age, skip=taken) if use_library else []
    for item in library:
        offered.append(item["id"])
        items.append(item)
        seen[item["id"]] = item
    message = result.get("message")
    if use_library and not result.get("candidates") and _has_subject(goal, terms):
        # Our own list had nothing. Either the library did (and the "nothing on
        # our list" line would read as "nothing anywhere"), or neither did and
        # the card's own wording, which names both, is the true one.
        message = None

    return {
        "goal": str(result.get("goal") or ""),
        "message": message,
        "note": result.get("note"),
        "items": items,
        "offered": offered,
        "unmapped_packs": unmapped,
    }


def _library_entry(source_id: str) -> Optional[dict[str, Any]]:
    """A downloadable Kiwix book as a registry-shaped entry, from the saved list."""
    from puffpai.catalogs import knowledge_resolver

    cand = knowledge_resolver.candidate_by_id(source_id)
    if not cand or not cand.get("fetchable") or not str(cand["id"]).startswith("kiwix:"):
        return None
    size = cand.get("size_bytes")
    return {
        "id": cand["id"], "label": cand["name"], "kind": "zim",
        "domain": "library", "what_it_is": cand.get("what_it_is") or "",
        "license": cand.get("license") or "", "license_url": cand.get("license_url") or "",
        "commercial_use": cand.get("commercial_ok") is True,
        "size_mb": (size / MB) if isinstance(size, int) and size > 0 else None,
        "source_url": "", "kiwix_book": cand.get("book") or "",
    }


def _has_subject(goal: str, terms: Any) -> bool:
    from puffpai.catalogs import goal_words
    return bool(goal_words.goal_terms(goal, extra=[str(t) for t in (terms or ())]))


#: How many books from the wider library the plan lists, at most.
LIBRARY_ROWS = 8


def library_rows(goal: str, terms: Any = (), *, age: Any = None,
                 skip: Optional[set[str]] = None) -> list[dict[str, Any]]:
    """Downloadable books from the Kiwix library for this goal, as plan rows.

    Offline: the shipped dated list is searched, never the network. Only books
    with a known license are listed - a book nobody can download here is not a
    choice. Only Kiwix books are plan rows: the Wikipedia topic book, Project
    Gutenberg and OpenStax reach the summary through
    :func:`knowledge_resolver.proposal_items` (apply ``knowledge.add``), whose
    ticks :func:`knowledge_default_on` decides. Rows carry the book id (``kiwix:...``); the owner's yes on the
    card is what later writes it into the build's overlay so it can be fetched.
    """
    from puffpai.catalogs import knowledge_resolver

    from puffpai.catalogs import goal_words

    found = knowledge_resolver.find(goal, terms or (), age=age, live=False,
                                    limit=LIBRARY_ROWS * 2)
    shelves = knowledge_resolver.shelf_words(goal, goal_words.goal_terms(goal))
    rows: list[dict[str, Any]] = []
    for cand in found.get("candidates") or []:
        if not str(cand.get("id") or "").startswith("kiwix:") or not cand.get("fetchable"):
            continue
        # A general encyclopedia is listed for every goal; anything else must
        # fit the goal as the owner typed it, not share one stray word with it.
        if not cand.get("general") and not knowledge_resolver.fits_goal(
                cand, goal, shelves):
            continue
        if skip and (cand["id"] in skip or "book:" + str(cand.get("book")) in skip):
            continue
        size = cand.get("size_bytes")
        entry = {
            "id": cand["id"], "label": cand["name"], "kind": "zim",
            "domain": "library", "what_it_is": cand.get("what_it_is") or "",
            "license": cand.get("license") or "", "license_url": cand.get("license_url") or "",
            "commercial_use": cand.get("commercial_ok") is True,
            "size_mb": (size / MB) if isinstance(size, int) and size > 0 else None,
            "source_url": "",
        }
        item = row(entry, reason=str(cand.get("reason") or ""),
                   matched_terms=list(cand.get("match_terms") or []))
        item["from_library"] = True
        item["audience"] = cand.get("audience") or "general"
        rows.append(item)
        if len(rows) >= LIBRARY_ROWS:
            break
    return rows


#: The one book the plan may tick on its own: the best match for the goal,
#: if it is no bigger than this and its size is known.
DEFAULT_ON_BYTES = 500 * MB


def goal_match(item: dict[str, Any], goal: str) -> bool:
    """Did this row match a word of the owner's goal (or the goal's shelf),
    not only a word the model suggested?"""
    from puffpai.catalogs import goal_words, knowledge_resolver
    words = goal_words.goal_terms(goal)
    words = words + knowledge_resolver.shelf_words(goal, words)
    return any(goal_words.term_hits_word(term, str(hit).lower())
               or goal_words.term_hits_word(str(hit).lower(), term)
               for term in words for hit in item.get("matched_terms") or ())


def about_subject(item: dict[str, Any], goal: str) -> bool:
    """Is a library pack about the bot's subject? Its name, or a catalog word
    it matched on, names the word that tells the subject apart, or the
    library shelf the subject sits on ("Arts & Crafts Q&A" for knitting).
    "TED ideas" is not about a wedding planner, though her sentence said
    "ideas". True when the goal names no subject."""
    from puffpai.catalogs import goal_words, knowledge_resolver
    name = str(item.get("label") or item.get("name") or "")
    matched = " ".join(str(t) for t in item.get("matched_terms")
                       or item.get("match_terms") or ())
    text = f"{name} {matched}"
    shelves = knowledge_resolver.shelf_words(goal, goal_words.goal_terms(goal))
    return (goal_words.mentions_subject(text, goal_words.subject_marks(goal))
            or any(goal_words.mentions_subject(text, [shelf]) for shelf in shelves))


def default_selection(items: list[dict[str, Any]], goal: str = "") -> list[str]:
    """What the card proposes: every fetchable row a pack picked, and the
    single best match for the goal when it is small.

    A row the owner's interview chose is a row they already said they wanted.
    Other matches are suggestions and start unticked - the wizard is not
    allowed to enrol somebody in 55 GB because their goal contained the word
    "reference" - except ONE: the first row that matched the owner's own goal
    words and is at most :data:`DEFAULT_ON_BYTES` with a known size. Without
    it, a bot "that knows every dinosaur" ships knowing no dinosaurs.
    """
    picked = [item["id"] for item in items
              if item["selectable"] and item["selected_by"]]
    if picked or not goal:
        return picked
    for item in items:
        size = item.get("bytes")
        if (item["selectable"] and isinstance(size, int)
                and 0 < size <= DEFAULT_ON_BYTES and goal_match(item, goal)
                and about_subject(item, goal)):
            return [item["id"]]
    return []


#: A Wikipedia topic book this small (its most, when its exact size is not
#: known until the articles are found) may start ticked.
TOPIC_BOOK_DEFAULT_BYTES = 50 * MB
#: Library books (curated or Kiwix) that may start ticked in one summary.
LIBRARY_DEFAULT_ITEMS = 2


def _goal_score(cand: dict[str, Any], goal: str) -> int:
    """How strongly a candidate's NAME matches the owner's own goal words.

    The same code score every library uses (a title word counts 3); a shelf
    word or a model's word does not count here, so "better" means "more about
    what the owner typed".
    """
    from puffpai.catalogs import goal_words, knowledge_resolver
    words = goal_words.goal_terms(goal)
    name = " ".join(cand.get("words") or []) or str(cand.get("name") or "")
    score, _matched = knowledge_resolver.score_entry({"title": name}, words)
    return score


def knowledge_default_on(cands: list[dict[str, Any]], goal: str = "") -> set[str]:
    """Which knowledge candidates start ticked on the summary, in list order.

    * A library book (our list or Kiwix) of known size up to
      :data:`knowledge_resolver.DEFAULT_ON_MAX_BYTES`: at most
      :data:`LIBRARY_DEFAULT_ITEMS` of them, and only when its name matches
      the owner's own goal words (a general or off-subject book waits for
      her pick).
    * The first Wikipedia topic book (English, or Simple English for a young
      builder) when it is at most :data:`TOPIC_BOOK_DEFAULT_BYTES` and no
      ticked book listed before it matches the owner's own words better.
    * A Project Gutenberg book or an OpenStax textbook: never on its own. They
      are listed for the owner to pick; a book "free for personal use only"
      is never ticked for them.

    A candidate that cannot be fetched is never ticked.
    """
    from puffpai.catalogs import knowledge_resolver

    ticked: set[str] = set()
    library = 0
    best_ticked = 0
    topic_seen = False
    for cand in cands:
        if not cand.get("fetchable"):
            continue
        tier = knowledge_resolver.tier_of(cand)
        size = cand.get("size_bytes")
        if tier in ("curated", "kiwix"):
            # Only a book whose name is about her goal starts ticked: "TED
            # ideas" for a wedding bot waits for her pick.
            if goal and (_goal_score(cand, goal) <= 0 or not about_subject(cand, goal)):
                continue
            if (library < LIBRARY_DEFAULT_ITEMS and isinstance(size, int)
                    and 0 < size <= knowledge_resolver.DEFAULT_ON_MAX_BYTES):
                ticked.add(str(cand["id"]))
                library += 1
                if not cand.get("general"):
                    best_ticked = max(best_ticked, _goal_score(cand, goal))
            continue
        if tier == "wikipedia_topic":
            if topic_seen:
                continue
            topic_seen = True
            most = size if isinstance(size, int) and size > 0 else cand.get("size_ceiling")
            if (isinstance(most, int) and 0 < most <= TOPIC_BOOK_DEFAULT_BYTES
                    and _goal_score(cand, goal) > best_ticked):
                ticked.add(str(cand["id"]))
    return ticked


def total_bytes(items: list[dict[str, Any]],
                ids: list[str]) -> tuple[int, bool]:
    """(bytes, every size known) for a selection. An unknown size is never 0."""
    wanted = set(ids)
    total, all_known = 0, True
    for item in items:
        if item["id"] not in wanted:
            continue
        if item["bytes"] is None:
            all_known = False
            continue
        total += int(item["bytes"])
    return total, all_known


def tool_needs(goal: str, items: list[dict[str, Any]],
               selected: list[str]) -> list[dict[str, Any]]:
    """What a bot like this will need to reach for. Ids, not prose.

    Sources names the needs and Tools resolves them.
    The ids are the catalog's own (``integration:weather``), matched from the
    goal by the same deterministic matcher the build interview uses, so nothing
    here invents a capability that does not ship.

    A live source in the plan is a need of its own kind: something has to be
    able to reach it at runtime, and saying so here is how Tools finds out.
    """
    from puffpai import build_interview

    needs: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        proposal = build_interview.propose(goal or "")
    except Exception:  # noqa: BLE001 - no catalog is "no needs", never a stop
        proposal = {"flat": []}
    for match in proposal.get("flat") or []:
        kind = str(match.get("kind") or "")
        if kind not in ("app", "integration"):
            continue
        need_id = str(match.get("id") or "")
        if not need_id or need_id in seen:
            continue
        seen.add(need_id)
        terms = ", ".join(str(t) for t in (match.get("matched_terms") or []))
        needs.append({
            "id": need_id,
            "label": str(match.get("label") or need_id),
            "kind": kind,
            "why": f"Your goal asked for it: {terms}." if terms
                   else "Matched what this bot is for.",
        })

    chosen = set(selected)
    for item in items:
        if item["id"] not in chosen or item["form"] != "live_source":
            continue
        need_id = f"source:{item['id']}"
        if need_id in seen:
            continue
        seen.add(need_id)
        needs.append({
            "id": need_id,
            "label": item["label"],
            "kind": "live_source",
            "why": "A live source, so something has to be able to reach it "
                   "while the bot is running.",
        })
    return needs


__all__ = [
    "HEAVY_BYTES", "KEYED_NOTE", "NO_KEY_PATH",
    "size_bytes", "size_label", "form_of", "needs_key", "costs_money",
    "why_not_fetchable", "row", "option_label", "plan_rows", "library_rows",
    "LIBRARY_ROWS",
    "default_selection", "total_bytes", "tool_needs",
    "knowledge_default_on", "TOPIC_BOOK_DEFAULT_BYTES",
]
