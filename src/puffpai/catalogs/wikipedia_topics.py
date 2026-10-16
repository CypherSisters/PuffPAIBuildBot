"""A Wikipedia topic book: the articles about one subject, saved as a book.

Kiwix covers the big subjects with whole books. Many goals are narrower than
any book in that library (knitting, a 2014 Honda Civic, vegetable beds in
Ohio), and a general encyclopedia of 100 GB is not a sensible answer to them.
A topic book is: the Wikipedia articles about the goal's own words, picked by
code, fetched once as plain text, and saved as one book on this computer.

How the articles are picked (all of it code; the local model may only drop
titles from the list code found, never add one):

1. **Search** Wikipedia for the goal's words together and for each of the
   first two words (the subject; later words only add weight).
2. **Seeds**: search hits whose title is one of those words ("Knitting").
3. **Categories**: the seeds' categories whose names contain one of those
   words ("Category:Knitting"), their articles, and one level of such
   subcategories.
4. **Check** every found title's first paragraph: an article is kept when it is
   in one of those categories, or when its title and first paragraph mention
   enough of the goal's words. Disambiguation pages are dropped.
5. **Cap** at :data:`MAX_ARTICLES`, best first, and at
   :data:`text_books.MAX_BOOK_BYTES` of text.

Politeness (Wikipedia's API etiquette): every request names BuildBot in its
User-Agent, carries ``maxlag`` and waits and retries when Wikipedia says it is
busy, and requests to one site are at least one second apart (the downloader's
own per-host limit). robots.txt disallows ``/w/`` for crawlers; this is the
documented API, asked on the owner's behalf for one book, not a crawl.

What leaves the computer: the book's subject words, and the article titles it
reads. Nothing about the owner.

Simple English Wikipedia is offered next to English Wikipedia. It is listed
first for a builder under 13; that changes the order only.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.parse
from typing import Any, Callable, Iterable, Optional

from puffpai.catalogs import goal_words
from puffpai.logging_config import get_logger

logger = get_logger(__name__)

CATALOG_ID = "wikipedia_topics"
ID_PREFIX = "wikipedia"
#: Wikipedia sites a topic book may come from, and their hosts. The hosts are
#: the named outside service ``wikipedia_books`` in the egress door table.
SITES: dict[str, str] = {"en": "en.wikipedia.org", "simple": "simple.wikipedia.org"}
SITE_WORDS = {"en": "Wikipedia", "simple": "Simple English Wikipedia"}
MAX_WORDS = 4
MAX_ARTICLES = 300
#: How many found titles are checked at most before the best are kept.
MAX_POOL = 600
#: How many subcategories of the subject's categories are read, at most.
MAX_SUBCATEGORIES = 12
#: When a book grows, how many more articles one "find more" may add.
GROW_ARTICLES = 150
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_ARTICLE_BYTES = 2 * 1024 * 1024
MAXLAG_SECONDS = 5
MAXLAG_RETRIES = 3
#: Plain text is roughly this share of an article's wiki source size. Used for
#: the size shown before the owner's yes, and always said as "about".
TEXT_SHARE = 0.55
LICENSE = "CC-BY-SA-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
PLAN_SECONDS = 15 * 60

_WORD_RE = re.compile(r"^[a-z0-9]{3,30}$")
_ID_RE = re.compile(r"^wikipedia:(en|simple):([a-z0-9]{3,30}(?:-[a-z0-9]{3,30}){0,3})$")

Getter = Callable[..., tuple[Optional[bytes], str]]


class WikipediaError(RuntimeError):
    """Wikipedia could not be asked or did not answer. Plain words."""


# ---------------------------------------------------------------------------
# ids and candidates (no network)
# ---------------------------------------------------------------------------

def topic_words(goal: str, extra: Iterable[str] = ()) -> list[str]:
    """The words a topic book is made of, at most :data:`MAX_WORDS`.

    So a book is not named "wedding, planner, sister and getting": for a goal that names a kind of bot, the words are the bot's subject
    only, as the outbound screen lets it leave
    (``outbound_query.subject_sendable``: "wedding planner"). A text that
    names no kind of bot (an Add-page request such as "dinosaurs") uses its
    search words with everything personal taken out
    (:func:`goal_words.search_terms`). *extra* is used only when the text
    itself gives nothing, and goes through the same filter. Her saved home
    town is read by the screen itself (``outbound_query.saved_home``), so
    it is never a word of the book ("a Tucson garden bot" at home in Tucson
    gives a book about "garden"). Fail closed: a subject the screen holds
    gives no book at all.
    """
    from puffpai.catalogs import personal_words
    text = str(goal or "")
    if personal_words.subject_of(text):
        try:
            from puffpai.security import outbound_query
            sent = outbound_query.subject_sendable(text)
        except Exception:  # noqa: BLE001 - no screen: nothing leaves
            sent = None
        if sent is None:
            return []
        words = goal_words.goal_terms(sent.phrase)
    else:
        words = _topic_words_screened(text)
        if not words:
            # So a bakery goal's book is not "about bakery recipe costing
            # taking": from a long sentence with no kind of
            # bot, only its first two thing words, the ones it leads with.
            words = _things(goal_words.search_terms(text, limit=MAX_WORDS * 3))
            words = words[:FALLBACK_WORDS]
        if not words:
            words = _things(goal_words.search_terms(" ".join(str(t) for t in extra),
                                                    limit=MAX_WORDS * 3))
    return [w for w in words if _WORD_RE.match(w)][:MAX_WORDS]


#: How many search words a book is made of when the goal names no kind of bot.
FALLBACK_WORDS = 2


def _is_doing(word: str) -> bool:
    """A doing word, also in its "-ing" form ("taking", "planning")."""
    from puffpai import bot_identity
    w = str(word or "").lower()
    if bot_identity.is_doing_word(w):
        return True
    if len(w) > 5 and w.endswith("ing"):
        stem = w[:-3]
        bases = {stem, stem + "e"}
        if len(stem) > 2 and stem[-1] == stem[-2]:
            bases.add(stem[:-1])               # "planning": "plan"
        return any(bot_identity.is_doing_word(b) for b in bases)
    return False


def _things(words: list[str]) -> list[str]:
    """*words* without doing words ("teach", "track", "taking"); [] when only
    doing words are left: a book about "track" is no book at all."""
    return [w for w in words if not _is_doing(w)]


def _topic_words_screened(text: str) -> list[str]:
    """The request's topic phrase ("I want to bake bread": "bread"), only
    when the outbound screen would let every word of it out; else []."""
    try:
        from puffpai import bot_identity
        from puffpai.security import outbound_query
        phrase = bot_identity.topic_phrase(text, form="subject")
        clean = " ".join(re.sub(r"[^a-z0-9 ]", " ", str(phrase or "").lower()).split())
        words = goal_words.goal_terms(clean)[:MAX_WORDS]
        if not words:
            return []
        res = outbound_query.screen([" ".join(words)], goal=text)
        return words if res.sendable and not res.held else []
    except Exception:  # noqa: BLE001 - no phrase: the search words decide
        return []


def about_words(words: Iterable[str]) -> str:
    """What a topic book is about, as its plan line and its saved title say
    it: its own words in order ("wedding planner"), without doing words
    ("bake bread" is about "bread"). Never the greeting's topic phrase, which
    can hold a name the owner typed."""
    from puffpai import bot_identity
    words = [str(w) for w in words]
    # A leading doing word only ("bake bread" is about bread; "baby name
    # idea" keeps its "name").
    while len(words) > 1 and bot_identity.is_doing_word(words[0]):
        words = words[1:]
    return " ".join(words)


def book_id(lang: str, words: Iterable[str]) -> Optional[str]:
    words = [w for w in words if _WORD_RE.match(str(w))][:MAX_WORDS]
    if lang not in SITES or not words:
        return None
    return f"{ID_PREFIX}:{lang}:{'-'.join(words)}"


def split_id(ident: str) -> Optional[tuple[str, list[str]]]:
    """``(lang, words)`` for a topic-book id, else ``None``.

    The words go through the same filter as a typed goal, so an id cannot
    carry filler or anything that is not a plain word.
    """
    match = _ID_RE.match(str(ident or "").strip())
    if not match:
        return None
    words = match.group(2).split("-")
    if goal_words.goal_terms(" ".join(words)) != words:
        return None
    return match.group(1), words


def _about(words: list[str]) -> str:
    """The book's words as a list, without doing words ("bake", "track"):
    only for a book known by its id alone. A book planned from a goal is
    named with the bot's topic phrase instead (:func:`candidates_for`)."""
    from puffpai import bot_identity
    things = [w for w in words if not bot_identity.is_doing_word(w)] or words
    if len(things) == 1:
        return things[0]
    return ", ".join(things[:-1]) + " and " + things[-1]


def candidate(lang: str, words: list[str], *, match_terms: Iterable[str] = (),
              verified_at: Optional[str] = None,
              about: str = "") -> Optional[dict[str, Any]]:
    """A planned topic book in the shared Candidate shape. No network.

    *about* names it ("Wikipedia articles about bread"); by default the words.
    """
    from puffpai.knowledge import text_books

    ident = book_id(lang, words)
    if ident is None:
        return None
    site = SITE_WORDS[lang]
    about = " ".join(str(about or "").split()) or about_words(words)
    easy = " written in easier English" if lang == "simple" else ""
    return {
        "id": ident,
        "kind": "knowledge",
        "tier": "wikipedia_topic",
        "name": f"{site} articles about {about}"[:80],
        "what_it_is": (f"Up to {MAX_ARTICLES} {site} articles{easy} about {about}, "
                       f"picked by their titles and first paragraphs and saved on "
                       f"this computer as one book.")[:240],
        "source_catalog": site,
        "url": f"https://{SITES[lang]}/",
        "host": SITES[lang],
        "sample_endpoint": None,
        "auth": "none",
        "license": LICENSE,
        "commercial_ok": True,
        "size_bytes": None,
        "size_ceiling": text_books.MAX_BOOK_BYTES,
        "local_or_cloud": "local",
        "needs": [],
        "match_terms": [str(t) for t in (match_terms or words)],
        "verified_at": verified_at,
        "book": "",
        "flavour": "",
        "meta4_url": "",
        "license_url": LICENSE_URL,
        "license_plain": "CC-BY-SA-4.0, free to use, even in something you sell "
                         "(credit Wikipedia's writers)",
        "fetchable": True,
        "why_not": "",
        "audience": "kids" if lang == "simple" else "general",
        "category": "wikipedia",
        "updated": "",
        "sends": list(words),
        "lang": lang,
        "words": list(words),
    }


def candidates_for(goal: str, terms: Iterable[str] = (), *,
                   child_first: bool = False) -> list[dict[str, Any]]:
    """English and Simple English topic books for a goal (order only by age)."""
    words = topic_words(goal, terms)
    if not words:
        return []
    # Named with the same words it is made of and sends, so the plan
    # line, the saved book's title and what leaves the computer agree.
    about = about_words(words)
    order = ("simple", "en") if child_first else ("en", "simple")
    return [c for c in (candidate(lang, words, about=about) for lang in order)
            if c]


def candidate_by_id(ident: str) -> Optional[dict[str, Any]]:
    parsed = split_id(ident)
    if parsed is None:
        return None
    return candidate(parsed[0], parsed[1])


# ---------------------------------------------------------------------------
# asking Wikipedia (polite, bounded, injectable)
# ---------------------------------------------------------------------------

def _hosts() -> set[str]:
    """The hosts of the named outside service ``wikipedia_books``; nothing if absent."""
    try:
        from puffpai.security import egress_doors
        door = egress_doors.door("wikipedia_books")
    except Exception:  # noqa: BLE001 - no door table: nothing is allowed
        return set()
    return {str(h) for h in door.hosts} if door is not None else set()

def _logged_fetch(source_fetch: Any) -> Getter:
    """source_fetch.get_small, logged and switched under this site's own row
    on "What leaves your computer" (wikipedia_books)."""
    def fetch(url: str, **kwargs: Any) -> tuple[Optional[bytes], str]:
        return source_fetch.get_small(url, service="wikipedia_books", **kwargs)
    return fetch



def api_url(lang: str, params: dict[str, Any]) -> str:
    if lang not in SITES:
        raise WikipediaError("That Wikipedia is not one BuildBot reads.")
    query = {"action": "query", "format": "json", "formatversion": "2",
             "maxlag": str(MAXLAG_SECONDS)}
    query.update({k: str(v) for k, v in params.items()})
    return f"https://{SITES[lang]}/w/api.php?" + urllib.parse.urlencode(query)


def ask(lang: str, params: dict[str, Any], *, getter: Optional[Getter] = None,
        sleeper: Callable[[float], None] = time.sleep,
        max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    """One API request, parsed. Waits and retries when Wikipedia is busy."""
    from puffpai import source_fetch

    fetch = getter or _logged_fetch(source_fetch)
    url = api_url(lang, params)
    for attempt in range(MAXLAG_RETRIES + 1):
        body, reason = fetch(url, allowed_hosts=_hosts(), max_bytes=max_bytes,
                             honour_robots=False)
        if body is None:
            if "503" in str(reason) and attempt < MAXLAG_RETRIES:
                sleeper(MAXLAG_SECONDS)
                continue
            raise WikipediaError(f"Wikipedia did not answer: {reason}")
        try:
            data = json.loads(body.decode("utf-8", errors="replace"))
        except ValueError as exc:
            raise WikipediaError("Wikipedia's answer could not be read.") from exc
        if not isinstance(data, dict):
            raise WikipediaError("Wikipedia's answer could not be read.")
        error = data.get("error")
        if isinstance(error, dict) and error.get("code") == "maxlag" \
                and attempt < MAXLAG_RETRIES:
            sleeper(MAXLAG_SECONDS)
            continue
        if isinstance(error, dict):
            raise WikipediaError(f"Wikipedia said no: {str(error.get('code'))[:40]}")
        return data
    raise WikipediaError("Wikipedia stayed busy. Try again later.")


def _title_ok(title: Any) -> bool:
    text = str(title or "")
    return 0 < len(text) <= 255 and all(ch.isprintable() for ch in text) \
        and "|" not in text


def _pages(data: dict[str, Any]) -> list[dict[str, Any]]:
    pages = ((data.get("query") or {}).get("pages")) or []
    return [p for p in pages if isinstance(p, dict)]


def _words_in(text: str) -> list[str]:
    return goal_words.raw_words(text[:3000])


def _hits(terms: list[str], words: Iterable[str]) -> set[str]:
    words = list(words)
    return {t for t in terms for w in words if goal_words.term_hits_word(t, w)}


def _is_seed(title: str, terms: list[str]) -> bool:
    words = goal_words.raw_words(title)
    return len(words) <= 2 and any(goal_words.term_hits_word(t, w)
                                   for t in terms for w in words) and \
        len(_hits(terms, words)) == len(words)


def anchors_of(terms: list[str]) -> list[str]:
    """The words the book is ABOUT: the first two the owner typed.

    "knitting, crochet, pattern": an article that only says "pattern" (a Turing
    pattern, a zigzag) is not about knitting, so later words only add weight
    to an article that already has one of the first two.
    """
    return list(terms[:2]) if len(terms) > 2 else list(terms)


def score_article(title: str, lead: str, terms: list[str], *,
                  in_topic_category: bool,
                  anchors: Optional[list[str]] = None) -> tuple[int, bool, list[str]]:
    """(score, keep, words that matched). Code only; evidence or nothing.

    The first paragraph is read with the title taken out, so "Dinosaur Jr is
    a rock band" does not count "dinosaur" twice. Outside the subject's own
    categories an article must match one of the ``anchors`` (the first words)
    and, for a goal of two or more words, two words in all.
    """
    title_words = goal_words.raw_words(title)
    lead_text = str(lead or "")
    if title:
        lead_text = lead_text.replace(title, " ")
    lead_words = _words_in(lead_text)
    in_title = _hits(terms, title_words)
    in_lead = _hits(terms, lead_words)
    matched = sorted(in_title | in_lead)
    score = 3 * int(in_topic_category) + 2 * len(in_title) + len(in_lead)
    if _is_seed(title, terms):
        score += 5
    need = 1 if len(terms) == 1 else 2
    anchors = anchors if anchors is not None else anchors_of(terms)
    anchored = bool(set(matched) & set(anchors))
    keep = bool(lead.strip()) and (in_topic_category or (
        anchored and len(set(matched)) >= need))
    if len(terms) == 1 and not in_topic_category and in_title and not in_lead:
        # a title that only borrows the word ("Dinosaur Jr", a band) and whose
        # first paragraph never mentions it again is kept only as a last resort
        score -= 2
    return score, keep, matched


# ---------------------------------------------------------------------------
# finding the articles
# ---------------------------------------------------------------------------

def _search(lang: str, text: str, limit: int, getter: Optional[Getter],
            sleeper: Callable[[float], None]) -> list[dict[str, Any]]:
    data = ask(lang, {"list": "search", "srsearch": text, "srlimit": str(limit),
                      "srnamespace": "0", "srprop": "size"},
               getter=getter, sleeper=sleeper)
    out = []
    for hit in ((data.get("query") or {}).get("search")) or []:
        if isinstance(hit, dict) and _title_ok(hit.get("title")):
            out.append({"title": str(hit["title"]), "length": int(hit.get("size") or 0)})
    return out


def _categories(lang: str, titles: list[str], getter: Optional[Getter],
                sleeper: Callable[[float], None]) -> dict[str, list[str]]:
    if not titles:
        return {}
    data = ask(lang, {"prop": "categories", "clshow": "!hidden", "cllimit": "max",
                      "titles": "|".join(titles[:20]), "redirects": "1"},
               getter=getter, sleeper=sleeper)
    out: dict[str, list[str]] = {}
    for page in _pages(data):
        cats = [str(c.get("title")) for c in page.get("categories") or []
                if isinstance(c, dict) and _title_ok(c.get("title"))]
        out[str(page.get("title"))] = cats
    return out


def _members(lang: str, category: str, getter: Optional[Getter],
             sleeper: Callable[[float], None], limit: int = 200
             ) -> tuple[list[str], list[str]]:
    data = ask(lang, {"list": "categorymembers", "cmtitle": category,
                      "cmtype": "page|subcat", "cmlimit": str(limit),
                      "cmnamespace": "0|14"}, getter=getter, sleeper=sleeper)
    pages, subcats = [], []
    for row in ((data.get("query") or {}).get("categorymembers")) or []:
        if not isinstance(row, dict) or not _title_ok(row.get("title")):
            continue
        (subcats if int(row.get("ns") or 0) == 14 else pages).append(str(row["title"]))
    return pages, subcats


def _category_about(category: str, terms: list[str]) -> bool:
    name = category.split(":", 1)[-1]
    return bool(_hits(terms, goal_words.raw_words(name)))


def _leads(lang: str, titles: list[str], getter: Optional[Getter],
           sleeper: Callable[[float], None]) -> dict[str, dict[str, Any]]:
    """First paragraph, size and disambiguation flag for up to 20 titles."""
    data = ask(lang, {"prop": "extracts|info|pageprops", "exintro": "1",
                      "explaintext": "1", "exlimit": "20", "ppprop": "disambiguation",
                      "titles": "|".join(titles[:20]), "redirects": "1"},
               getter=getter, sleeper=sleeper)
    out: dict[str, dict[str, Any]] = {}
    for page in _pages(data):
        if page.get("missing") or not _title_ok(page.get("title")):
            continue
        out[str(page["title"])] = {
            "lead": str(page.get("extract") or "")[:3000],
            "length": int(page.get("length") or 0),
            "disambiguation": "disambiguation" in (page.get("pageprops") or {}),
            "pageid": int(page.get("pageid") or 0),
        }
    return out


_PLANS: dict[str, tuple[float, dict[str, Any]]] = {}
_PLAN_LOCK = threading.Lock()


def remembered_plan(ident: str) -> Optional[dict[str, Any]]:
    """The list the owner was shown for this book, if it is recent."""
    with _PLAN_LOCK:
        kept = _PLANS.get(ident)
    if kept and time.time() - kept[0] < PLAN_SECONDS:
        return kept[1]
    return None


def discover(ident: str, *, getter: Optional[Getter] = None,
             have: Iterable[str] = (), cap: int = MAX_ARTICLES,
             prune: Optional[Callable[[list[str]], list[str]]] = None,
             sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Which articles this topic book would hold. Network; raises :class:`WikipediaError`.

    ``have`` are titles already in the book (a book that grows gets only new
    ones). ``prune`` may return titles to DROP; anything it names that code
    did not find is ignored.
    """
    parsed = split_id(ident)
    if parsed is None:
        raise WikipediaError("That is not a topic book BuildBot can make.")
    lang, terms = parsed
    anchors = anchors_of(terms)
    have_keys = {str(t).casefold() for t in have}
    requests = 0

    def counted(fn, *args):
        nonlocal requests
        requests += 1
        return fn(*args)

    pool: list[str] = []
    seen: set[str] = set()

    def add(titles: Iterable[str]) -> None:
        for title in titles:
            key = title.casefold()
            if key in seen or key in have_keys or len(pool) >= MAX_POOL:
                continue
            seen.add(key)
            pool.append(title)

    hits_all = counted(_search, lang, " ".join(terms), 50, getter, sleeper)
    add(h["title"] for h in hits_all)
    per_word: list[dict[str, Any]] = []
    if len(terms) > 1:
        for term in anchors:
            per_word += counted(_search, lang, term, 30, getter, sleeper)
    else:
        per_word = hits_all
    seeds = [h["title"] for h in hits_all + per_word if _is_seed(h["title"], anchors)]
    seeds = list(dict.fromkeys(seeds))[:8] or [h["title"] for h in hits_all[:3]]

    topic_cats: list[str] = []
    for cats in counted(_categories, lang, seeds, getter, sleeper).values():
        for cat in cats:
            if _category_about(cat, anchors) and cat not in topic_cats:
                topic_cats.append(cat)
    topic_cats = topic_cats[:6]
    in_category: set[str] = set()
    subcats: list[str] = []
    for cat in topic_cats:
        pages, subs = counted(_members, lang, cat, getter, sleeper)
        in_category.update(p.casefold() for p in pages)
        add(pages)
        # a subcategory of the subject's own category is about the subject;
        # the ones whose names say so are read first
        subcats += sorted(subs, key=lambda c: not _category_about(c, anchors))
    for cat in list(dict.fromkeys(subcats))[:MAX_SUBCATEGORIES]:
        if len(pool) >= MAX_POOL:
            break
        pages, _subs = counted(_members, lang, cat, getter, sleeper)
        in_category.update(p.casefold() for p in pages)
        add(pages)
    add(h["title"] for h in per_word)

    found: list[dict[str, Any]] = []
    order = {t.casefold(): i for i, t in enumerate(pool)}
    for start in range(0, len(pool), 20):
        batch = pool[start:start + 20]
        leads = counted(_leads, lang, batch, getter, sleeper)
        for title, info in leads.items():
            if info["disambiguation"] or title.casefold() in have_keys:
                continue
            score, keep, matched = score_article(
                title, info["lead"], terms,
                in_topic_category=title.casefold() in in_category, anchors=anchors)
            if not keep:
                continue
            found.append({"title": title, "length": info["length"], "score": score,
                          "matched": matched, "_order": order.get(title.casefold(), 9999)})
    found.sort(key=lambda a: (-a["score"], a["_order"]))
    # a redirect can bring the same article back under its real title
    unique: dict[str, dict[str, Any]] = {}
    for art in found:
        unique.setdefault(art["title"].casefold(), art)
    found = list(unique.values())
    pruned: list[str] = []
    if prune is not None and found:
        try:
            drop = {str(t).casefold() for t in (prune([a["title"] for a in found]) or [])}
        except Exception as exc:  # noqa: BLE001 - the model is optional
            logger.info("topic book prune skipped: %s", exc)
            drop = set()
        # the model may only remove what code found, and never most of it
        drop &= {a["title"].casefold() for a in found}
        if len(drop) <= len(found) // 2:
            pruned = [a["title"] for a in found if a["title"].casefold() in drop]
            found = [a for a in found if a["title"].casefold() not in drop]
    found = found[:max(0, int(cap))]
    for art in found:
        art.pop("_order", None)
    estimate = int(sum(a["length"] for a in found) * TEXT_SHARE)
    plan = {"id": ident, "lang": lang, "words": terms, "articles": found,
            "count": len(found), "estimate_bytes": estimate,
            "categories": topic_cats, "pruned": pruned, "requests": requests,
            "made_at": time.time()}
    with _PLAN_LOCK:
        _PLANS[ident] = (time.time(), plan)
    return plan


def plan_lines(plan: dict[str, Any]) -> list[str]:
    """What the owner reads before saying yes to the planned articles."""
    from puffpai.catalogs import knowledge_resolver

    words = _about(list(plan.get("words") or []))
    titles = [a["title"] for a in plan.get("articles") or []][:8]
    lines = [f"    Found:      {plan.get('count', 0)} articles about {words} "
             f"({knowledge_resolver.size_words(plan.get('estimate_bytes'))} of text)"]
    if titles:
        lines.append("    Such as:    " + "; ".join(titles)[:300])
    lines.append(f"    Sends:      the words {words} and the article names, "
                 f"nothing about you")
    return lines


# ---------------------------------------------------------------------------
# fetching the text
# ---------------------------------------------------------------------------

def fetch_article(lang: str, title: str, *, getter: Optional[Getter] = None,
                  sleeper: Callable[[float], None] = time.sleep) -> Optional[dict[str, Any]]:
    """One article as plain text with its address and revision, or ``None``."""
    data = ask(lang, {"prop": "extracts|revisions|info", "explaintext": "1",
                      "rvprop": "ids", "inprop": "url", "titles": title,
                      "redirects": "1"},
               getter=getter, sleeper=sleeper, max_bytes=MAX_ARTICLE_BYTES)
    for page in _pages(data):
        if page.get("missing") or not _title_ok(page.get("title")):
            continue
        text = str(page.get("extract") or "")
        if not text.strip():
            return None
        revs = page.get("revisions") or [{}]
        revision = str((revs[0] or {}).get("revid") or page.get("lastrevid") or "")
        url = str(page.get("canonicalurl") or page.get("fullurl") or "")
        expected = f"https://{SITES[lang]}/"
        if not url.startswith(expected):
            url = expected + "wiki/" + urllib.parse.quote(
                str(page["title"]).replace(" ", "_"))
        return {"title": str(page["title"]), "url": url, "revision": revision,
                "text": text}
    return None


def book_meta(ident: str) -> dict[str, Any]:
    parsed = split_id(ident)
    if parsed is None:
        raise WikipediaError("That is not a topic book BuildBot can make.")
    lang, words = parsed
    site = SITE_WORDS[lang]
    return {
        "id": ident, "kind": "wikipedia_topic", "catalog": site,
        # The plan line's own name (the title equals the plan label).
        "title": f"{site} articles about {about_words(words)}"[:80],
        "language": lang, "license": LICENSE, "license_url": LICENSE_URL,
        "license_plain": "CC BY-SA 4.0, free to use, even in something you sell",
        "credit": (f"Text from {site} articles by their many writers, under CC BY-SA "
                   f"4.0. Each page names its article's address and revision."),
        "commercial_use": True, "personal_use_only": False,
        "source_url": f"https://{SITES[lang]}/",
        "note": "Articles picked by BuildBot from their titles and first paragraphs.",
    }


def fetch_pages(plan: dict[str, Any], *, getter: Optional[Getter] = None,
                cancel: Optional[threading.Event] = None,
                on_progress: Optional[Callable[[dict[str, Any]], None]] = None,
                max_bytes: Optional[int] = None,
                sleeper: Callable[[float], None] = time.sleep) -> Iterable[dict[str, Any]]:
    """Yield the planned articles' text, one polite request each."""
    from puffpai.knowledge import text_books

    limit = max_bytes if max_bytes is not None else text_books.MAX_BOOK_BYTES
    lang = str(plan.get("lang") or "")
    total = 0
    arts = list(plan.get("articles") or [])
    for n, art in enumerate(arts, 1):
        if cancel is not None and cancel.is_set():
            return
        try:
            page = fetch_article(lang, str(art["title"]), getter=getter, sleeper=sleeper)
        except WikipediaError as exc:
            logger.info("article skipped: %s", exc)
            page = None
        if page is not None:
            total += len(page["text"].encode("utf-8"))
            yield page
        if on_progress is not None:
            try:
                on_progress({"done": n, "total": len(arts), "downloaded": total})
            except Exception:  # noqa: BLE001
                pass
        if total >= limit:
            return


def model_pruner(goal: str, chat: Callable[[str], str]) -> Callable[[list[str]], list[str]]:
    """A prune function over found titles for :func:`discover`. Optional.

    The model sees numbered titles only and may answer with numbers; anything
    else in its reply is ignored, so it can only remove titles code found.
    """
    def prune(titles: list[str]) -> list[str]:
        shown = titles[:MAX_ARTICLES]
        lines = [f"{i}. {t[:80]}" for i, t in enumerate(shown, 1)]
        prompt = ("Below are Wikipedia article titles found for this goal. Reply "
                  "with ONLY the numbers of titles that are clearly NOT about the "
                  "goal (for example a film or band that merely shares a word), "
                  "separated by commas. Reply 0 if all fit.\n"
                  f"Goal: {' '.join(str(goal).split())[:200]}\n" + "\n".join(lines))
        reply = str(chat(prompt) or "")
        picked = []
        for token in re.findall(r"\d+", reply):
            idx = int(token)
            if 1 <= idx <= len(shown):
                picked.append(shown[idx - 1])
        return picked
    return prune


__all__ = ["CATALOG_ID", "SITES", "MAX_ARTICLES", "LICENSE", "WikipediaError",
           "anchors_of",
           "topic_words", "book_id", "split_id", "candidate", "candidates_for",
           "candidate_by_id", "api_url", "ask", "score_article", "discover",
           "remembered_plan", "plan_lines", "fetch_article", "book_meta",
           "fetch_pages", "model_pruner"]
