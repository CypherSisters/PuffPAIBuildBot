"""THE check for "what do the books say about this?".

:func:`book_hits` is what the chat's context, the routines' daily passage
(:func:`pick_passage`) and the home page ask. It merges two look-ups of the
downloaded books:

* the word look-up every book has as soon as its quick stage is done
  (:mod:`puffpai.knowledge.fts_index`), so a book answers within seconds of
  arriving, with or without the reading helper;
* the meaning look-up (:func:`puffpai.knowledge.pack_context.pack_hits`)
  once a book's deeper stage has run, which also finds questions worded
  differently.

Both find the same article often; such hits are merged (the higher score
plus a small bonus, and the word look-up's passage, which holds the matched
words). Every row names its book. Book text is data: it is returned for the
caller's quoted context block, never acted on.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import bisect
import os
import random
import re
import sqlite3
import threading
from typing import Any, Callable, Iterable, Optional

from .fts_index import FTSSearcher, sanitize_terms, shard_labels

#: How much of an article a hit carries (the chat quotes at most this much).
PASSAGE_CHARS = 700
#: Word look-up relevance (0.5..1, see fts_index) is mapped onto 0.35..0.85,
#: the range the chat's meaning hits use (HomeBot's convention).
FTS_FLOOR, FTS_TOP = 0.35, 0.85
#: Added when both look-ups found the same article.
BOTH_BONUS = 0.05
#: A word hit's relevance is relative to the best hit of the same look-up, so
#: the top hit always scored FTS_TOP whether or not it held the question's
#: words (14 of 18 cited sources in one set of runs sat at exactly 0.85). A
#: page that holds fewer than this share of the question's own words is not
#: evidence for an answer: it drops below the chat's citing floor (0.25).
MIN_WORD_SHARE = 0.6
WEAK_RELEVANCE = 0.2
COLLECTION = "books"
#: A page longer than this many passages (a whole book kept as one page, as
#: Project Gutenberg's and the Internet Archive's books are) gives its best
#: few passages, not one around the first word that matched.
LONG_PAGE_PASSAGES = 2
PASSAGES_PER_PAGE = 3
#: A page this long is a whole book: it gives :data:`PASSAGES_PER_PAGE`
#: passages; a shorter page gives its one best.
BOOK_PAGE_CHARS = 50_000
#: What a passage scores for each of the question's words in it, for each of
#: its two-word phrases ("best man"), and for a heading (a line on its own)
#: that names one of them. Without this, "what does the best man do?" is
#: answered from the book's first "man", not its "The Best Man."
PHRASE_POINTS = 3
HEADING_POINTS = 3
#: Added when a page's own title holds a phrase of the question, or all its
#: words; taken off a page that holds none of the question's phrases.
TITLE_BONUS = 0.08
NO_PHRASE = 0.1
#: What a passage adds to its page's relevance: for each time it holds a
#: phrase of the question (at most 3), and for a heading in it that names one.
PHRASE_REL = 0.02
HEADING_REL = 0.04
#: The most a word look-up hit can score, bonuses included.
BEST_RELEVANCE = 0.95

_searchers: dict[str, FTSSearcher] = {}
_searchers_lock = threading.Lock()


def _books_dir(fts_dir: Optional[str]) -> str:
    if fts_dir:
        return fts_dir
    from .learn import books_dir
    return books_dir()


def searcher(fts_dir: Optional[str] = None) -> FTSSearcher:
    """One searcher per folder, rediscovering books on every call."""
    folder = _books_dir(fts_dir)
    with _searchers_lock:
        found = _searchers.get(folder)
        if found is None:
            found = FTSSearcher(folder)
            _searchers[folder] = found
    found.refresh()
    return found


def fts_relevance(raw: Any) -> float:
    """A word look-up relevance (0.5..1) on the chat's 0.35..0.85 scale."""
    try:
        r = float(raw or 0)
    except (TypeError, ValueError):
        r = 0.0
    return max(FTS_FLOOR, min(FTS_TOP, FTS_FLOOR + (r - 0.5)))


def _default_relevance(distance: Any, space: str) -> float:
    if distance is None:
        return 0.0
    rel = 1.0 - float(distance) if space in ("cosine", "ip") else 1.0 - float(distance) / 2.0
    return max(0.0, min(1.0, rel))


def passage(body: str, words: Iterable[str], width: int = PASSAGE_CHARS) -> str:
    """About *width* characters of *body* around the first matched word."""
    body = (body or "").strip()
    if len(body) <= width:
        return body
    lowered = body.lower()
    at = -1
    for word in words:
        w = str(word).strip('"').lower()
        for probe in (w, w[:5] if len(w) > 6 else w):
            m = re.search(r"\b" + re.escape(probe), lowered)
            if m and (at < 0 or m.start() < at):
                at = m.start()
                break
    if at < 0:
        at = 0
    start = max(0, at - width // 3)
    end = min(len(body), start + width)
    start = max(0, end - width)
    if start > 0:
        space = body.find(" ", start)
        start = space + 1 if 0 <= space < start + 40 else start
    if end < len(body):
        space = body.rfind(" ", start, end)
        end = space if space > start + width // 2 else end
    text = body[start:end].strip()
    return ("..." if start > 0 else "") + text + ("..." if end < len(body) else "")


_LABELS: dict[str, tuple[float, dict[str, str]]] = {}


def _book_labels(db_path: str) -> dict[str, str]:
    try:
        stamp = os.path.getmtime(db_path)
    except OSError:
        return {}
    cached = _LABELS.get(db_path)
    if cached and cached[0] == stamp:
        return cached[1]
    labels = shard_labels(db_path)
    _LABELS[db_path] = (stamp, labels)
    return labels


def content_words(question: str) -> list[str]:
    """The question's words minus the common ones ("how", "do", "you" ...).

    A small book has few articles, so its look-up cannot tell on its own that
    "how" says nothing: "how do you knit" matched pages titled "How to help".
    """
    from .upload_search import STOP_WORDS
    return [w for w in (t.strip('"') for t in sanitize_terms(question))
            if w.lower() not in STOP_WORDS]


def _set_aside(fts_dir: Optional[str], rag: Any = None) -> tuple[set[str], set[str]]:
    """Books revoked since they were learned: hidden, and cleaned up if idle."""
    from .learn import forget_set_aside, set_aside
    books, sources = set_aside()
    if books:
        folder = _books_dir(fts_dir)
        if any(os.path.isfile(os.path.join(folder, f"{b}.db")) for b in books):
            forget_set_aside(books, rag=rag, fts_dir=folder)
    return books, sources


#: How much of a page's start counts as its lead (about a first paragraph).
LEAD_CHARS = 300


def word_share(words: Iterable[str], text: str) -> float:
    """How many of *words* the text holds (0..1), plural or not."""
    wanted = [str(w).strip('"').lower() for w in words if str(w).strip('"')]
    if not wanted:
        return 0.0
    lowered = str(text or "").lower()
    held = 0
    for w in wanted:
        stem = w[:-1] if len(w) > 3 and w.endswith("s") else w
        stem = stem[:6] if len(stem) > 6 else stem
        if re.search(r"\b" + re.escape(stem), lowered):
            held += 1
    return held / len(wanted)


def _listing_page(path: Any) -> bool:
    from .fts_index import listing_page_reason
    text = str(path or "")
    return bool(listing_page_reason(text.split("/", 1)[1] if "/" in text else text))


def question_phrases(question: str) -> list[tuple[str, str]]:
    """The question's two-word phrases: content words typed next to each
    other ("best man", "wedding day")."""
    from .upload_search import STOP_WORDS
    out: list[tuple[str, str]] = []
    prev = ""
    for word in re.findall(r"[a-z0-9]+", str(question or "").lower()):
        if word in STOP_WORDS or len(word) < 2:
            prev = ""
            continue
        if prev and (prev, word) not in out:
            out.append((prev, word))
        prev = word
    return out


def _stem(word: str) -> str:
    w = str(word).strip('"').lower()
    w = w[:-1] if len(w) > 3 and w.endswith("s") else w
    return w[:6] if len(w) > 6 else w


def _phrase_re(pair: tuple[str, str]) -> "re.Pattern[str]":
    a, b = (_stem(w) for w in pair)
    return re.compile(r"\b" + re.escape(a) + r"\w{0,4}\W{1,3}" + re.escape(b))


def _headings(body: str) -> list[tuple[int, str]]:
    """``(offset, text)`` of each line on its own (a blank line before and
    after), short enough to be a heading."""
    out: list[tuple[int, str]] = []
    lines = body.split("\n")
    at = 0
    for i, line in enumerate(lines):
        text = line.strip()
        if (text and len(text) <= 80 and (i == 0 or not lines[i - 1].strip())
                and (i + 1 == len(lines) or not lines[i + 1].strip())):
            out.append((at, text))
        at += len(line) + 1
    return out


def _count_in(positions: list[int], start: int, end: int) -> int:
    return bisect.bisect_left(positions, end) - bisect.bisect_left(positions, start)


def best_passages(body: str, words: Iterable[str], *, k: int = PASSAGES_PER_PAGE,
                  width: int = PASSAGE_CHARS, question: str = ""
                  ) -> list[tuple[str, float]]:
    """The best *k* passages of a page for the question, best first, each
    with what it adds to the page's relevance.

    A passage scores the question's words in it, its phrases ("best man")
    higher, and a heading (a line on its own) that names them higher still;
    a stretch that reads like a list of page numbers (a contents page, an
    index) counts half. What it adds: :data:`PHRASE_REL` for each time it
    holds a phrase (at most 3), and :data:`HEADING_REL` when a heading in it
    names a phrase.
    """
    body = body or ""
    words = [w for w in (str(x).strip('"') for x in words) if w]
    if not words:
        return [(passage(body, words, width), 0.0)]
    low = body.lower()
    stems = list(dict.fromkeys(_stem(w) for w in words))
    at = {s: [m.start() for m in re.finditer(r"\b" + re.escape(s), low)] for s in stems}
    pairs = [_phrase_re(p) for p in question_phrases(question)]
    phrases = [[m.start() for m in rx.finditer(low)] for rx in pairs]
    heads = [(pos, text.lower()) for pos, text in _headings(body)
             if any(re.search(r"\b" + re.escape(s), text.lower()) for s in stems)]
    head_at = [pos for pos, _t in heads]
    # a heading that names a phrase of the question names what is asked
    phrase_heads = [pos for pos, text in heads if any(rx.search(text) for rx in pairs)]

    def judge(start: int, end: int) -> tuple[float, float]:
        held = sum(min(3, _count_in(ps, start, end)) for ps in phrases)
        named = bool(_count_in(phrase_heads, start, end))
        score = float(sum(1 for ps in at.values() if _count_in(ps, start, end)))
        score += PHRASE_POINTS * held
        if named:
            score += 2 * HEADING_POINTS
        elif _count_in(head_at, start, end):
            score += HEADING_POINTS
        listing = sum(1 for line in body[start:end].split("\n")
                      if re.search(r"\d\W*$", line.strip()))
        if listing >= 4:
            score /= 2
        return score, min(3, held) * PHRASE_REL + (HEADING_REL if named else 0.0)

    if len(body) <= width * LONG_PAGE_PASSAGES:
        return [(passage(body, words, width), judge(0, len(body))[1])]
    anchors = sorted({p for ps in phrases for p in ps} | set(head_at))
    if len(anchors) < k:
        rarest = sorted((ps for ps in at.values() if ps), key=len)
        anchors = sorted(set(anchors) | set((rarest[0] if rarest else [])[:300]))
    heading_starts = set(head_at)
    scored: list[tuple[float, int, float]] = []
    for anchor in anchors[:2000]:
        # A heading starts its passage; a phrase sits a little way in.
        start = max(0, anchor - (40 if anchor in heading_starts else width // 4))
        score, bonus = judge(start, start + width)
        scored.append((score, start, bonus))
    scored.sort(key=lambda s: (-s[0], s[1]))
    picked: list[tuple[int, float]] = []
    for _score, start, bonus in scored:
        if all(abs(start - other) >= width for other, _b in picked):
            picked.append((start, bonus))
        if len(picked) >= k:
            break
    out = [(("..." if start > 0 else "") + passage(body[start:start + width], words, width),
            bonus) for start, bonus in picked]
    return out or [(passage(body, words, width), 0.0)]


def _title_holds(title: str, words: list[str], question: str) -> bool:
    """Does a page's own title hold a phrase of the question, or all its words?"""
    low = str(title or "").lower()
    if not low:
        return False
    if any(_phrase_re(p).search(low) for p in question_phrases(question)):
        return True
    return len(words) > 1 and word_share(words, low) >= 1.0


def word_hits(question: str, *, n: int = 5, fts_dir: Optional[str] = None,
              hidden: Iterable[str] = ()) -> list[dict[str, Any]]:
    """Hits from the books' word look-ups, each with its passage and book.

    Books in *hidden* (set aside since they were learned) never answer.
    """
    hidden = set(hidden or ())
    words = content_words(question)
    if not words:
        return []
    phrases = [_phrase_re(p) for p in question_phrases(question)]
    s = searcher(fts_dir)
    if not s.is_ready():
        return []
    try:
        # More than are kept: a long book ranks below short pages in the
        # look-up, and its best passage may still be the answer.
        found = s.search(" ".join(words), max_results=max(n * 3, 20) + len(hidden))
    except Exception:  # noqa: BLE001 - a bad look-up is no hits, never an error
        return []
    out: list[dict[str, Any]] = []
    for hit in found:
        if hit["library_id"] in hidden or _listing_page(hit.get("path")):
            continue
        db_path = s.shards.get(hit["library_id"])
        if not db_path:
            continue
        labels = _book_labels(db_path)
        body = ""
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                row = conn.execute("SELECT f.body FROM docs d JOIN fts f ON f.rowid = d.id "
                                   "WHERE d.path = ?", (hit["path"],)).fetchone()
                body = row[0] if row else ""
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        texts = best_passages(body, words, question=question,
                              k=PASSAGES_PER_PAGE if len(body) > BOOK_PAGE_CHARS else 1)
        rel = fts_relevance(hit.get("relevance"))
        if word_share(words, f"{hit.get('title') or ''} {body}") < MIN_WORD_SHARE:
            rel = min(rel, WEAK_RELEVANCE)
        elif _title_holds(str(hit.get("title") or ""), words, question):
            rel = rel + TITLE_BONUS
        elif phrases and not any(rx.search(body.lower()) for rx in phrases):
            rel = max(WEAK_RELEVANCE, rel - NO_PHRASE)
        for part, (text, bonus) in enumerate(texts):
            row = {
                "text": text or str(hit.get("title") or ""),
                "library": (labels.get("book_name") or labels.get("source_label")
                            or "Offline library"),
                "collection": COLLECTION,
                "distance": None,
                "relevance": (min(BEST_RELEVANCE, rel + bonus) if rel > WEAK_RELEVANCE
                              else rel),
                "title": str(hit.get("title") or ""),
                "url": "",
                "is_user_upload": False,
                "source_id": labels.get("source_id", ""),
                "source_license": labels.get("source_license", ""),
                "book_id": hit["library_id"],
                # The page's own first sentence: what the page is about, for
                # checking it fits a goal (not the passage around the words).
                "lead": _clean(body, LEAD_CHARS),
            }
            if len(texts) > 1:
                row["part"] = part
            out.append(row)
    out.sort(key=lambda r: r["relevance"], reverse=True)
    return out[:n]


def _key(row: dict[str, Any]) -> tuple[str, str, Any]:
    """One page (or one passage of a long page) of one book."""
    return (str(row.get("source_id") or row.get("library") or ""),
            str(row.get("title") or "").strip().lower(), row.get("part"))


#: How many passages the chat is given from the books, at most.
BOOK_HITS = 8


def book_hits(question: str, *, n: int = BOOK_HITS, rag: Any = None, query_emb: Any = None,
              relevance: Optional[Callable[[Any, str], float]] = None,
              listed: Iterable[str] = (), fts_dir: Optional[str] = None
              ) -> list[dict[str, Any]]:
    """The books' best passages for *question*, merged and labelled, best first.

    Uses the word look-up always, and the meaning look-up too when *rag* is
    given and holds passages. ``[]`` when no book has anything. A book set
    aside (revoked) after it was learned never answers, from either look-up.
    """
    hidden_books, hidden_sources = _set_aside(fts_dir, rag)
    rows = word_hits(question, n=n, fts_dir=fts_dir, hidden=hidden_books)
    vector: list[dict[str, Any]] = []
    if rag is not None:
        try:
            from .pack_context import pack_hits
            vector = pack_hits(rag, question, query_emb,
                               relevance=relevance or _default_relevance,
                               listed=listed, n_results=n)
        except Exception:  # noqa: BLE001 - no meaning look-up means word hits only
            vector = []
        vector = [r for r in vector
                  if str(r.get("book_id") or "") not in hidden_books
                  and str(r.get("source_id") or "") not in hidden_sources]
    merged: dict[tuple, dict[str, Any]] = {}
    for row in rows:
        key = _key(row)
        if key not in merged or row["relevance"] > merged[key]["relevance"]:
            merged[key] = dict(row)
    for row in vector:
        key = _key(row)
        if key in merged and merged[key].get("distance") is None:
            mine = merged[key]
            mine["relevance"] = min(1.0, max(mine["relevance"], row["relevance"]) + BOTH_BONUS)
            continue  # keep the word look-up's passage: it holds the words
        if key in merged:
            # Another passage of the same page (a whole book is one page):
            # its own row, not folded into the first.
            key = key + (str(row.get("text") or "")[:80],)
        merged[key] = dict(row)
    return sorted(merged.values(), key=lambda r: r["relevance"], reverse=True)[:n]


# -- one passage for a daily update ----------------------------------------------------

def _clean(text: Any, limit: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    return s if len(s) <= limit else s[:limit].rsplit(" ", 1)[0] + "..."


def _first_sentence(text: str, title: str) -> str:
    body = re.sub(r"\s+", " ", (text or "").strip().lstrip(".")).strip()
    if title and body.lower().startswith(title.lower()):
        body = body[len(title):].lstrip(" :-.")
    for part in re.split(r"(?<=[.!?])\s+", body):
        if len(part.strip()) >= 20:
            return _clean(part, 240)
    return _clean(body, 240)


def _any_article(fts_dir: Optional[str], rng: random.Random) -> Optional[dict[str, Any]]:
    s = searcher(fts_dir)
    hidden, _ = _set_aside(fts_dir)
    shards = [(k, v) for k, v in s.shards.items() if k not in hidden]
    rng.shuffle(shards)
    for lib_id, db_path in shards:
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                top = conn.execute("SELECT max(id) FROM docs").fetchone()[0] or 0
                if not top:
                    continue
                row = conn.execute(
                    "SELECT d.title, substr(f.body, 1, 2000) FROM docs d "
                    "JOIN fts f ON f.rowid = d.id WHERE d.id >= ? AND "
                    "length(f.body) >= 200 ORDER BY d.id LIMIT 1",
                    (rng.randint(1, top),)).fetchone()
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        if row:
            labels = _book_labels(db_path)
            return {"title": row[0], "text": row[1],
                    "library": labels.get("book_name") or "Offline library",
                    "source_license": labels.get("source_license", "")}
    return None


#: How many random pages are tried for one that fits the bot, when the
#: look-up found none.
_FIT_TRIES = 6


def pick_passage(terms: list, *, rag: Any = None, fts_dir: Optional[str] = None,
                 rng: Optional[random.Random] = None,
                 request: Optional[str] = None) -> Optional[dict[str, Any]]:
    """``{title, first_sentence, credit}`` from the books for a daily update.

    Books only: the owner's own uploads are never read out on a card or aloud.
    The page must fit *request* (the bot's goal as the owner typed it; the
    *terms* when not given) by the rule every offer uses
    (:func:`goal_words.fits_request`): a page that shares one stray word
    with it ('Toyota Corolla' for a Honda Civic bot) is never read out.
    No page fits: no passage.
    """
    from puffpai.catalogs import goal_words
    rng = rng or random.Random()
    words = [t for t in (terms or []) if isinstance(t, str) and t.strip()][:4]
    want = request if request is not None else " ".join(words)

    def fits(row: Optional[dict[str, Any]]) -> bool:
        return bool(row) and (not want.strip() or goal_words.fits_request(
            want, f"{row.get('title') or ''} {str(row.get('text') or '')[:400]}"))

    hits = book_hits(" ".join(words), n=5, rag=rag, fts_dir=fts_dir) if words else []
    hits = [h for h in hits if fits(h)]
    hit = rng.choice(hits[:3]) if hits else None
    for _ in range(1 if not want.strip() else _FIT_TRIES):
        if hit:
            break
        page = _any_article(fts_dir, rng)
        hit = page if fits(page) else None
    if not hit:
        return None
    title = _clean(hit.get("title") or "", 90)
    label = _clean(hit.get("library") or "", 80)
    licence = _clean(hit.get("source_license") or "", 40)
    sentence = _first_sentence(str(hit.get("text") or "").lstrip("."), title)
    if not sentence:
        return None
    return {"title": title, "first_sentence": sentence,
            "credit": f"{label}, {licence}" if licence else label}


__all__ = ["COLLECTION", "PASSAGE_CHARS", "book_hits", "passage", "pick_passage",
           "searcher", "word_hits"]
