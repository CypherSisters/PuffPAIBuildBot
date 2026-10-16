"""Books and guides for one job: the free libraries, found with the job's words.

The same search the Find more knowledge panel uses
(:func:`puffpai.catalogs.knowledge_resolver.find`): BuildBot's own list, the
Kiwix library (Stack Exchange Q&A, Wikivoyage, iFixit ...), Project
Gutenberg, OpenStax, Wikibooks, the Internet Archive, and a Wikipedia book
made from the words. Only books that can be fetched and that fit the words
are shown, best match first, then the most downloaded, at most
:data:`PER_JOB` a job and :data:`PER_LIBRARY` from one library, each with what it is, where it comes from, its
licence, its size and its format.

For a bot with a subject (find step, chat), each library is asked short
searches (:func:`queries`): the subject ("wedding", "3d printing"), once for
the whole bot, and per job the book topics the brain suggested for it
(:func:`topics_for_jobs`: "etiquette", "household budget"), or, without
them, the subject with the job's key words ("wedding planning"). The answers
are merged per job (:func:`for_job`): stories, children's books and sermons
are left out (catalog subjects and title cues, :func:`off_genre`) unless the
bot is about one of those; then the reading helper ranks the rest by how
close each is in meaning to the job's topics, and drops the clearly
unrelated. Without the helper, a word score does the same: the subject or
a topic named in the title or catalog subjects.

At most :data:`TICK_MAX` books for the whole bot start ticked
(:func:`pick_ticked`): the best fitting ones (:data:`TICK_FIT` or more),
openly licensed (never "free for personal use only") and small. Everything
else, a big Kiwix file included, shows its size and waits for her tap.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import os
import re
from typing import Any, Callable, Iterable, Optional

from puffpai.finding import sites
from puffpai.finding import wording as W

SITE = "libraries"
PER_JOB = 5
#: At most this many books from one library for one job.
PER_LIBRARY = 2
TICK_MAX = 2
TICK_MAX_BYTES = 300 * 1024 * 1024
#: A title (or name) match scores 3 or more; a blurb word alone is not clear.
TICK_MIN_SCORE = 3
#: Tests hand in recorded library answers here (None: the real libraries).
GETTER: Optional[Any] = None
#: Searches one job sends to the libraries, at most: the subject and two topics.
QUERIES_PER_JOB = 3
#: Book topics the brain is asked for, per job, and how long it may take.
TOPICS_PER_JOB = 3
#: Words a topic carries that no book title needs ("catering services",
#: "venue selection", "floral arrangements"): left out of its search.
TOPIC_FILLER = frozenset("""
service selection theme concept arrangement detail style basic tip method
technique skill idea option choice type kind planning management
""".split())
#: Different topic searches sent for one bot, at most (the find step waits
#: for every library to answer each one).
TOPIC_SEARCHES_MAX = 8
TOPIC_DEADLINE_S = 40.0
#: Tests hand in the brain here: ``messages -> answer text`` (None: the real one).
TOPIC_ASKER: Optional[Callable[[list], str]] = None
#: Words that name the shape of a part (an app, a sheet, a list), not its
#: subject: a library's books are found by the subject.
SHAPE_WORDS = frozenset("""
spreadsheet spreadsheets sheet sheets template templates checklist checklists
tracker trackers log logs form forms chart charts table tables database file
files pdf article articles book books tutorial tutorials manual manuals
handbook handbooks api apis python script scripts calculator generator
dashboard directory listing listings datasheet collection photo photos image
images picture pictures dataset online catalog catalogue
png jpg jpeg gif svg csv json xml html txt docx xlsx zip software code github
repo repository open source opensource app apps website websites plugin
""".split())
#: Job words that are a library subject too ("wedding planning").
TOPIC_WORDS = frozenset({"planning"})
#: A wide search (the find step): this many books from each library, at most
#: this many in all, before :func:`for_job` keeps the ones about the bot.
WIDE_PER_LIBRARY = 8
WIDE_MAX = 40
#: Words that say a book is a story, a poem or music, not one to learn from.
#: Not used when the bot's own subject is one of them (a bedtime story bot).
STORY_WORDS = frozenset("""
novel novels stories story tale tales comedy comedies poem poems poetry verse
verses cantata opera operas fiction drama dramas plays song songs ballad ballads
folklore fairy
romances
""".split())
#: The reading helper (0..1 likeness to the job's topics): below this, or
#: below this share of the best book's for the job, a book is clearly about
#: something else. Measured on the wedding and 3D printing runs: the guides
#: score 0.55 to 0.85 against their topic, a novel, a travel book or a type
#: specimen book that only shares the subject's word 0.3 to 0.48.
MEANING_FLOOR = 0.5
MEANING_SHARE = 0.65
#: A book fits a job this well (likeness, or the word score without the
#: helper) before it starts ticked.
TICK_FIT = 0.55
#: Catalog shelves that say a book is practical (Gutenberg's "How To ...").
PRACTICAL_SHELVES = re.compile(r"how to|reference|handbooks?|manuals?|guides?|instruction",
                               re.I)
#: A little extra fit for a book its library files as practical.
PRACTICAL_BONUS = 0.05
#: Books a bot rarely learns from, as word groups: the catalog subject words
#: and title cues that mark them. A group is kept when the bot's own subject
#: or the job names one of its words (a bedtime story bot, a church bot).
GENRES: dict[str, dict[str, frozenset]] = {
    "story": {"subject": frozenset("""fiction novel novels stories story tales tale romances
                 drama dramas plays poetry poems verse ballads songs opera literature
                 thrillers folklore fairy humour humor""".split()),
              "title": STORY_WORDS},
    "children": {"subject": frozenset("""juvenile children children's nursery picture
                    kids""".split()),
                 "title": frozenset("""juvenile nursery children children's kids
                    picture-book""".split())},
    "religious": {"subject": frozenset("""sermons sermon religion religious religion/spirituality
                     spirituality theology devotional hymns hymn bible prayers homilies
                     church""".split()),
                  "title": frozenset("""sermon sermons homilies hymns hymn prayers devotions
                     discourses""".split())},
}
#: The reading helper as ``texts -> vectors``; None: the one on this
#: computer, if it is there (tests hand in their own, or ``False`` for none).
EMBED: Any = None


def off() -> bool:
    """Book searching switched off for this process (tests do, by default)."""
    return os.environ.get("PUFFPAI_FIND_BOOKS", "").strip().lower() in ("off", "0", "no")


def kind_words(cand: dict[str, Any]) -> str:
    """book, guide, Q&A collection or textbook."""
    from puffpai.catalogs import knowledge_resolver as kr
    tier = kr.tier_of(cand)
    if tier == "openstax":
        return "textbook"
    if tier == "wikibooks":
        return "guide"
    if tier == "kiwix":
        category = str(cand.get("category") or "").lower()
        if category == "stack_exchange":
            return "Q&A collection"
        if category in ("wikivoyage", "ifixit", "wikihow"):
            return "guide"
    return "book"


def format_words(cand: dict[str, Any]) -> str:
    from puffpai.knowledge import find_more
    if find_more.is_text_book(cand.get("id")):
        return "plain text, kept as a book on this computer"
    return "one Kiwix file, read on this computer"


def _size(cand: dict[str, Any]) -> Optional[int]:
    for key in ("size_bytes", "size_ceiling"):
        value = cand.get(key)
        if isinstance(value, int) and value > 0:
            return value
    return None


def tickable(cand: dict[str, Any], words: str) -> bool:
    """Clearly about the words (its name says them), openly licensed, small."""
    from puffpai.catalogs import knowledge_resolver as kr
    size = _size(cand)
    return (kr.tier_of(cand) != "wikipedia_topic" and cand.get("fetchable") is True
            and cand.get("commercial_ok") is True and not cand.get("personal_use_only")
            and size is not None and size <= TICK_MAX_BYTES
            and int(cand.get("score") or 0) >= TICK_MIN_SCORE
            and kr.fits_goal(cand, words) and title_says(cand, words))


def title_says(cand: dict[str, Any], words: str) -> bool:
    """Its own name holds every subject word of the job ("3D Printing Q&A"
    for 3d printing; an old type specimen book filed under printing does not)."""
    from puffpai.finding.find import key_words
    name = str(cand.get("name") or "").lower()
    wanted = key_words(words, limit=6)
    return bool(wanted) and all(w[:5] in name for w in wanted)


def as_find(cand: dict[str, Any], words: str, rank: int) -> dict[str, Any]:
    from puffpai.catalogs import knowledge_resolver as kr
    downloads = cand.get("downloads")
    f = sites.make(site=SITE, type_="book", id="book:" + str(cand["id"]),
                   name=str(cand.get("name") or cand["id"]),
                   what=str(cand.get("what_it_is") or ""), url=str(cand.get("url") or ""),
                   popularity={"downloads": downloads} if isinstance(downloads, int)
                   and downloads > 0 else None,
                   can="add_book", rank=rank, raw={"candidate": cand})
    f["site_name"] = str(cand.get("source_catalog") or W.SITE_NAMES[SITE])
    f["type_words"] = kind_words(cand)
    f["licence"] = str(cand.get("license") or "")
    f["licence_words"] = str(cand.get("license_plain") or "")
    f["size_words"] = kr.download_words(cand)
    f["format_words"] = format_words(cand)
    f["tier"] = kr.tier_of(cand)
    f["score"] = int(cand.get("score") or 0)
    f["tickable"] = tickable(cand, words)
    f["can_trade"] = False
    f["line"] = W.line_for(f)
    return f


def search(words: str, limit: int = PER_JOB, *, wide: bool = False) -> list[dict[str, Any]]:
    """Up to :data:`PER_JOB` book finds for *words*. Never raises. *wide*:
    every book that fits, up to :data:`WIDE_PER_LIBRARY` from each library,
    for :func:`for_job` to rank."""
    if off():
        return []
    from puffpai.catalogs import knowledge_resolver as kr
    try:
        got = kr.find(words, live=True, include_general=False,
                      limit=WIDE_MAX if wide else PER_JOB * 3,
                      per_library=WIDE_PER_LIBRARY if wide else None,
                      getter=GETTER, reach_getter=GETTER)
    except Exception:  # noqa: BLE001 - the libraries never sink the find step
        return []
    cands = [c for c in got.get("candidates") or []
             if c.get("fetchable") and kr.fits_goal(c, words)]
    # A Wikipedia book made from the words is a promise to look, not a book
    # a library has: the real books come first.
    cands.sort(key=lambda c: kr.tier_of(c) == "wikipedia_topic")
    if wide:
        return [as_find(c, words, n) for n, c in enumerate(cands)]
    shown: list[dict[str, Any]] = []
    per_library: dict[str, int] = {}
    for cand in cands:
        tier = kr.tier_of(cand)
        if per_library.get(tier, 0) >= PER_LIBRARY:
            continue          # a few libraries each, not five books from one
        per_library[tier] = per_library.get(tier, 0) + 1
        shown.append(cand)
        if len(shown) >= min(limit, PER_JOB):
            break
    return [as_find(c, words, n) for n, c in enumerate(shown)]


def _topic_words(text: Any, skip: Iterable[str] = ()) -> list[str]:
    """The words of *text* that name a subject a library files books under."""
    from puffpai.finding.find import key_words
    skip = set(skip)
    out: list[str] = []
    for w in re.findall(r"[a-z0-9][a-z0-9'-]*", str(text or "").lower()):
        w = w.strip("'-")
        if w in skip or w in out or w in SHAPE_WORDS:
            continue
        if w in TOPIC_WORDS or key_words(w, limit=1):
            out.append(w)
    return out


def queries(job_words: str, subject: Any = "",
            extra: Optional[Iterable[str]] = None,
            topics: Optional[Iterable[str]] = None,
            phrase: Optional[str] = None) -> list[str]:
    """What the libraries are asked for one job: the bot's subject words
    ("wedding", "3d printing"), then the job's book *topics* as they are
    ("etiquette", "household budget": a guide to etiquette rarely says
    "wedding" in its title). Without topics, the subject with the job's key
    words as a short pair ("wedding planning", "3d printing filament"): for
    a job the brain worked out, the pair comes from its own book searches
    for the job (*extra*, a list, maybe empty: its other searches name apps
    and sheets); for her own search, from her words. Without a subject, the
    job's words as they are. *phrase* is the bot's subject when it is known
    already (:func:`book_subject`); *subject* stays her goal, which the
    privacy screen reads.

    At most :data:`QUERIES_PER_JOB` searches a job: each library is asked
    at most once a second (``source_fetch``), and the subject's own search
    answers every job, so it is sent once for the whole bot.

    Every search leaves only through the outbound privacy screen
    (:func:`_screened`), read against her goal and the job's words: one
    held word (a name, a health word, her town) holds that whole search."""
    from puffpai.catalogs import goal_words
    if phrase is None:
        phrase = goal_words.subject_phrase(str(subject or ""))
    subj = _topic_words(phrase)[:3]
    out = [" ".join(subj)] if subj else []
    for topic in topics or ():
        topic = clean_topic(topic)
        if topic and topic not in out:
            out.append(topic)
    if topics is None:
        # No topics (her own search, or none of the brain's could be
        # searched): the subject with the job's key words, or the job's
        # words alone.
        if not subj:
            words = _topic_words(job_words)[:MAX_QUERY_WORDS] or str(job_words or "").split()
            return _screened([" ".join(words[:MAX_QUERY_WORDS])], subject, job_words)
        for text in ([job_words] if extra is None else list(extra)[:2]):
            topic = _topic_words(text, skip=subj)[:2]
            if topic:
                out.append(" ".join((subj + topic)[:MAX_QUERY_WORDS]))
                break
        out = out[:2]
    return _screened(out[:QUERIES_PER_JOB], subject, job_words)


def book_subject(goal: Any, brain: Any = "") -> str:
    """The bot's subject for its books, for any wording of her goal: the
    goal's own subject ("wedding planner"), else the one the brain named
    ("puppy training"), else her goal less its filler words ("a bot to help
    me train my new puppy" gives "puppy")."""
    from puffpai.catalogs import goal_words
    return (goal_words.subject_phrase(str(goal or "")) or clean_topic(brain)
            or goal_words.plain_subject(str(goal or "")))


#: Without the reading helper, a retry word named by more books than this
#: on the saved Gutenberg list is too broad ("health").
RETRY_BROAD = 300


def retry_word(query: str, phrase: str = "") -> str:
    """For a search of two or more words that found no book: the one word
    of it that best keeps its meaning ("canine health" gives "canine",
    "event etiquette" gives "etiquette"), among the words books on the saved
    Gutenberg list name. Closest in meaning to the search for the bot's
    subject when the reading helper is here; else the most named word that
    is not too broad. "" when no word is named."""
    from puffpai.catalogs import goal_words, gutendex
    words = query.split()
    if len(words) < 2:
        return ""
    try:
        index = gutendex.load_snapshot().get("index") or {}
    except Exception:  # noqa: BLE001 - no saved list: no retry
        return ""
    named = {w: len(index.get(w) or index.get(goal_words.singular(w)) or ())
             for w in words}
    named = {w: n for w, n in named.items() if n > 0}
    if not named:
        return ""
    embed = _embedder()
    if embed is not None and len(named) > 1:
        whole = f"{query} for {phrase}" if phrase else query
        try:
            vectors = [list(map(float, v)) for v in embed([whole] + list(named))]

            def cos(a: list[float], b: list[float]) -> float:
                na = sum(x * x for x in a) ** 0.5 or 1.0
                nb = sum(x * x for x in b) ** 0.5 or 1.0
                return sum(x * y for x, y in zip(a, b)) / (na * nb)
            return max(zip(named, vectors[1:]), key=lambda wv: cos(vectors[0], wv[1]))[0]
        except Exception:  # noqa: BLE001 - the helper failed: the word count
            pass
    narrow = {w: n for w, n in named.items() if n <= RETRY_BROAD} or named
    return max(narrow, key=lambda w: (narrow[w], -words.index(w)))


def retry_search(query: str, subject: Any, job_words: str, phrase: str = "") -> str:
    """:func:`retry_word`, if the outbound privacy screen lets it out."""
    word = retry_word(query, phrase)
    sent = _screened([word], subject, job_words) if word else []
    return sent[0] if sent else ""


def clean_topic(text: Any) -> str:
    """A book topic as a library search: one to three plain words that name
    a subject, no shape words ("spreadsheet"); "" when nothing is left."""
    return " ".join(_topic_words(text)[:3])


def fallback_topics(job: dict[str, Any], subject: Any = "") -> list[str]:
    """Topics from the job's own words, when the brain gave none: its book
    searches, its words and its name, less the subject's words and the
    shape words ("wedding expense tracker" gives "expense")."""
    from puffpai.catalogs import goal_words
    subj = set(_topic_words(goal_words.subject_phrase(str(subject or ""))))
    subj |= {goal_words.singular(w) for w in _topic_words(subject)}
    filler = goal_words.stopwords()   # "Everything for a ... bot" never searches "everything"
    out: list[str] = []
    for text in list(job.get("books") or []) + [job.get("words"), job.get("name")]:
        for w in _topic_words(text, skip=subj):
            w = goal_words.singular(w)
            if (w not in out and w not in TOPIC_WORDS and w not in subj
                    and w not in filler):
                out.append(w)
    return out[:QUERIES_PER_JOB - 1]


#: What the brain is asked: general, for any bot (never one subject's words).
TOPIC_PROMPT = (
    "A home assistant bot will learn from free books in public libraries "
    "(Project Gutenberg, Wikibooks, the Internet Archive): mostly old practical "
    "books, manuals, handbooks and how-to guides. First name the bot's subject "
    "in one or two plain words, the way a library shelf is labelled. Then, for "
    "each job of the bot, name 2 or 3 topics such books are filed under that "
    "would teach someone to do that job well: concrete, one or two plain words "
    "each, the way a library shelf is labelled. For the first job (the whole "
    "bot), name the classic subjects of its world, such as its customs and "
    "manners or its main craft. Stay close to the bot's own subject: its "
    "customs, crafts, materials, tools and know-how. Never use file types, "
    "software words (app, alerts, tracking, data, open source) or broad school "
    "subjects (physics, art, economics, pedagogy). Examples of good shelf "
    "labels: \"beekeeping\", \"confectionery\", \"bookkeeping\", \"garden "
    "pests\", \"knots\", \"woodworking tools\". Never name people, places or "
    "health details. Answer only with JSON: "
    '{"subject": "...", "jobs": [{"id": "j1", "topics": ["...", "..."]}]}')

TOPIC_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"},
                   "jobs": {"type": "array", "items": {
                       "type": "object",
                       "properties": {"id": {"type": "string"},
                                      "topics": {"type": "array",
                                                 "items": {"type": "string"}}},
                       "required": ["id", "topics"]}}},
    "required": ["subject", "jobs"]}


def topic_messages(goal: str, jobs: list[dict[str, Any]]) -> list[dict[str, str]]:
    lines = [f"{j['id']}: {j.get('name') or j.get('words')}" for j in jobs]
    return [{"role": "system", "content": TOPIC_PROMPT},
            {"role": "user", "content": "The bot: " + " ".join(str(goal or "").split())[:400]
             + "\nIts jobs:\n" + "\n".join(lines)}]


def _ask_brain(messages: list, url: str, model: str, deadline_s: float) -> str:
    from puffpai.wizard import needs_llm
    backend = needs_llm.make_backend(url, model, timeout=deadline_s, max_tokens=600)
    return str(backend.chat(messages, temperature=0.0, max_tokens=600, format=TOPIC_SCHEMA))


def parse_topics(text: Any) -> dict[str, Any]:
    """``{job id: [topic]}`` from the brain's answer, and the bot's subject
    under ``""``; {} when it cannot be read."""
    import json
    try:
        data = json.loads(str(text or ""))
    except ValueError:
        return {}
    out: dict[str, Any] = {}
    if isinstance(data, dict) and clean_topic(data.get("subject")):
        out[""] = clean_topic(data.get("subject"))
    for row in (data.get("jobs") if isinstance(data, dict) else None) or []:
        if not isinstance(row, dict) or not isinstance(row.get("topics"), list):
            continue
        topics: list[str] = []
        for t in row["topics"]:
            t = clean_topic(t)
            if t and t not in topics:
                topics.append(t)
        out[str(row.get("id") or "")] = topics[:TOPICS_PER_JOB]
    return out


def topics_for_jobs(goal: Any, jobs: list[dict[str, Any]], *, url: str = "",
                    model: str = "", deadline_s: float = TOPIC_DEADLINE_S) -> str:
    """Give each job ``topics``: 2 or 3 book topics from the brain (one call
    for all the jobs), or from the job's own words when the brain is not
    there, too slow, or names none for a job. The brain runs on this
    computer; the topics leave it only through :func:`queries` (the privacy
    screen). Returns where the topics came from: "brain" or "words"."""
    from puffpai.catalogs import goal_words
    got: dict[str, Any] = {}
    ask = TOPIC_ASKER
    if ask is None and url and model:
        def ask(m: list) -> str:
            return _ask_brain(m, url, model, deadline_s)
    if ask is not None and jobs:
        try:
            got = parse_topics(ask(topic_messages(str(goal or ""), jobs)))
        except Exception:  # noqa: BLE001 - a brain that fails picks no topics
            got = {}
    # The bot's subject for its books, whatever the wording of her goal.
    phrase = book_subject(goal, got.get(""))
    subject_words = set(_topic_words(phrase))
    marks = set(goal_words.subject_marks(phrase)) or subject_words
    for job in jobs:
        job["book_subject"] = phrase
    for job in jobs:
        # A topic that is only the subject's own words is searched already.
        mine = [t for t in got.get(job["id"]) or [] if set(t.split()) - subject_words]
        job["topics"] = (mine or fallback_topics(job, phrase))[:TOPICS_PER_JOB]
    # What is searched: each topic less the subject's telling word ("wedding
    # etiquette" asks for "etiquette": a library wants both words in a title;
    # "dog training" for a puppy training bot stays whole),
    # each job's first topic before any job's second, at most
    # TOPIC_SEARCHES_MAX for the bot (each library answers one a second).
    # Every topic still ranks the job's books by meaning.
    picked: set[str] = set()
    searchable: set[str] = set()
    for job in jobs:
        job["topic_searches"] = []
    # A one-word topic first: a library finds more for it.
    order = {job["id"]: sorted(job["topics"], key=lambda t: len(t.split()) > 1)
             for job in jobs}
    for n in range(TOPICS_PER_JOB):
        for job in jobs:
            if n >= len(job["topics"]) or len(job["topic_searches"]) >= QUERIES_PER_JOB - 1:
                continue
            words = [w for w in order[job["id"]][n].split() if w not in marks]
            # "catering services" asks for "catering": a library wants every
            # word in a title, and a filler word only narrows it.
            words = [w for w in words if goal_words.singular(w) not in TOPIC_FILLER] or words
            search = " ".join(words)
            if not search:
                continue
            searchable.add(job["id"])
            if search not in picked and len(picked) >= TOPIC_SEARCHES_MAX:
                continue
            picked.add(search)
            if search not in job["topic_searches"]:
                job["topic_searches"].append(search)
    for job in jobs:
        if job["id"] not in searchable:
            # No topic could be searched: the subject with the job's words.
            job["topic_searches"] = None
    return "brain" if any(k for k in got) else "words"


#: A search the screen lets out is one to four plain words.
MAX_QUERY_WORDS = 4


def _screened(phrases: list[str], subject: Any, job_words: str) -> list[str]:
    """The *phrases* that pass the outbound privacy screen, in order, each
    whole or not at all. Any error sends nothing (fail closed)."""
    context = ". ".join(t for t in (" ".join(str(subject or "").split())[:600],
                                    " ".join(str(job_words or "").split())[:200]) if t)
    try:
        from puffpai.security import outbound_query
        result = outbound_query.screen([p for p in phrases if p], goal=context)
        return [s.phrase for s in result.sendable if outbound_query.is_sendable(s)]
    except Exception:  # noqa: BLE001 - not sure what is private: send nothing
        return []


def shares_subject(words: str, subject: Any) -> bool:
    """Do *words* (her own search) name the bot's subject? "wedding
    etiquette" does for a wedding bot; "knitting" does not, so it is
    searched on its own."""
    from puffpai.catalogs import goal_words
    marks = goal_words.subject_marks(str(subject or ""))
    return bool(marks) and goal_words.mentions_subject(words, marks)


def _book_words(f: dict[str, Any]) -> str:
    cand = (f.get("raw") or {}).get("candidate") or {}
    subjects = cand.get("subjects") or []
    return " ".join([str(f.get("name") or "")] + [str(s) for s in subjects][:8])


def about(f: dict[str, Any], marks: list[str], job_words: str,
          stories_ok: bool = False) -> bool:
    """A book find is about the bot: its title or catalog subjects name the
    subject's telling word(s), or its own name says every word of the job
    (a "knitting" search in chat), and it is not a story, poem or song (a
    bot learns from guides). A Wikipedia book is made from the words."""
    from puffpai.catalogs import goal_words
    if f.get("tier") == "wikipedia_topic":
        return True
    if not stories_ok and set(re.findall(r"[a-z]+", _book_words(f).lower())) & STORY_WORDS:
        return False
    if not marks:
        return True
    return goal_words.mentions_subject(_book_words(f), marks) or \
        title_says({"name": f.get("name")}, job_words)


def _embedder() -> Optional[Callable[[list[str]], Any]]:
    if EMBED is False:
        return None
    if EMBED is not None:
        return EMBED
    try:
        from puffpai.knowledge import reading_helper
        return reading_helper.get_embedder(threads=1)
    except Exception:  # noqa: BLE001 - no helper: the word rule alone
        return None


def _subjects(f: dict[str, Any]) -> list[str]:
    cand = (f.get("raw") or {}).get("candidate") or {}
    return [str(s) for s in cand.get("subjects") or []]


def practical(f: dict[str, Any]) -> bool:
    """Its library files it as practical ("How To ...", "Reference")."""
    return any(PRACTICAL_SHELVES.search(s) for s in _subjects(f))


def off_genre(f: dict[str, Any], allowed: Iterable[str] = ()) -> bool:
    """A story, a children's book or a sermon (its catalog subjects or its
    title say so), when the bot is not about one of those (*allowed*: the
    bot's subject and the job's words). A book its library files as
    practical ("How To ...") is kept whatever else it is filed under."""
    if f.get("tier") == "wikipedia_topic" or practical(f):
        return False
    allowed_words = set(re.findall(r"[a-z'/]+", " ".join(allowed).lower()))
    name = str(f.get("name") or "")
    title = set(re.findall(r"[a-z'/-]+", name.lower()))
    subjects = set(re.findall(r"[a-z'/]+", " ".join(_subjects(f)).lower()))
    cand = (f.get("raw") or {}).get("candidate") or {}
    for group, cues in GENRES.items():
        if (cues["subject"] | cues["title"]) & allowed_words:
            continue          # the bot is about this (a bedtime story bot)
        if title & cues["title"] or subjects & cues["subject"]:
            return True
        if group == "children" and (cand.get("audience") == "kids" or re.search(
                r"\bfor (?:children|little)\b", name, re.I)):
            return True
    return False


def title_key(f: dict[str, Any]) -> str:
    """The same book's title in any library ("The ladies' book of etiquette,
    and manual of politeness; a ..." and "... politeness : a ...")."""
    return "title:" + re.sub(r"[^a-z0-9]+", "", str(f.get("name") or "").lower())[:30]


def _names_subject(name: str, marks: list[str], phrase: str) -> bool:
    """A Wikipedia book's words are the subject's ("3d printing", or just
    "printing" for it), not a topic's ("geometry")."""
    from puffpai.catalogs import goal_words
    words = re.sub(r"(?i)^wikipedia articles about\s+", "", name).lower().split()
    return goal_words.mentions_subject(name, marks) or (
        bool(words) and all(w in phrase.lower().split() for w in words))


def meaning_texts(phrase: str, topics: Iterable[str] = ()) -> list[str]:
    """What a job's books are measured against: a practical guide to the
    bot's subject, and to each of the job's topics for it ("a practical
    guide to etiquette for a wedding planner")."""
    return [f"a practical guide to {phrase}"] + [
        f"a practical guide to {t} for {phrase}" for t in topics if t]


def likeness(finds: list[dict[str, Any]], texts: list[str]) -> Optional[dict[int, float]]:
    """``{id(find): likeness}``: how close in meaning (0..1) each book's
    title and catalog subjects are to the closest of *texts*. None without
    the reading helper (or when it fails)."""
    embed = _embedder() if finds else None
    if embed is None:
        return None
    try:
        vectors = [list(map(float, v)) for v in
                   embed(list(texts) + [_book_words(f) for f in finds])]
    except Exception:  # noqa: BLE001 - the helper failed: the word rule alone
        return None

    def norm(v: list[float]) -> float:
        return sum(x * x for x in v) ** 0.5 or 1.0

    qs, vs = vectors[:len(texts)], vectors[len(texts):]
    return {id(f): max(sum(a * b for a, b in zip(q, v)) / (norm(q) * norm(v)) for q in qs)
            for f, v in zip(finds, vs)}


def word_fit(f: dict[str, Any], marks: list[str], subject_words: list[str],
             topics: Iterable[str]) -> float:
    """The fit without the reading helper (0..1): the subject named in its
    title or catalog subjects, its title saying the whole subject, a topic's
    word in its title (or, less, in its catalog subjects)."""
    from puffpai.catalogs import goal_words
    title = str(f.get("name") or "").lower()
    subjects = " ".join(_subjects(f)).lower()
    fit = 0.0
    if marks and goal_words.mentions_subject(_book_words(f), marks):
        fit += 0.4
    if subject_words and all(w[:5] in title for w in subject_words):
        fit += 0.2
    stems = _topic_stems(topics)
    if any(s in title for s in stems):
        fit += 0.3
    elif any(s in subjects for s in stems):
        fit += 0.2
    return min(fit, 1.0)


def _topic_stems(topics: Iterable[str], phrase: str = "") -> set[str]:
    """The topics' telling word stems, less filler and the subject's own
    words ("print" of "print speed" for a 3d printing bot: a book about
    printing presses names it too)."""
    from puffpai.catalogs import goal_words
    own = {w[:5] for w in phrase.lower().split()}
    return {w[:5] for t in topics for w in str(t).split()
            if len(w) >= 3 and goal_words.singular(w) not in TOPIC_FILLER
            and w[:5] not in own}


def meaning_order(finds: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """*finds* ranked by how close each title (and catalog subjects) is in
    meaning to *text*, dropping those clearly about something else. As they
    are when the reading helper is not on this computer."""
    books_only = [f for f in finds if f.get("tier") != "wikipedia_topic"]
    near = likeness(books_only, [text])
    if near is None:
        return finds
    for f in books_only:
        f["likeness"] = round(near[id(f)], 3)
    floor = max(MEANING_FLOOR, MEANING_SHARE * max(near.values()))
    kept = [f for f in books_only if near[id(f)] >= floor]
    kept.sort(key=lambda f: -near[id(f)])
    return kept + [f for f in finds if f.get("tier") == "wikipedia_topic"]


def open_and_small(cand: dict[str, Any]) -> bool:
    """A book that may start ticked: a library's own book, fetchable, openly
    licensed (never "free for personal use only") and small."""
    from puffpai.catalogs import knowledge_resolver as kr
    size = _size(cand)
    return (kr.tier_of(cand) != "wikipedia_topic" and cand.get("fetchable") is True
            and cand.get("commercial_ok") is True and not cand.get("personal_use_only")
            and size is not None and size <= TICK_MAX_BYTES)


def for_job(finds: list[dict[str, Any]], subject: Any, job_words: str,
            limit: int = PER_JOB, shown: Iterable[str] = (),
            topics: Optional[Iterable[str]] = None,
            phrase: Optional[str] = None) -> list[dict[str, Any]]:
    """One job's books, from every search it sent: no stories, children's
    books or sermons (unless the bot is about them, :func:`off_genre`),
    closest in meaning to the job's *topics* first, at most
    :data:`PER_LIBRARY` from one library, the Wikipedia book last, none
    already *shown* for an earlier job (they still count when judging what
    is clearly unrelated). Each gets ``fit`` (0..1) and ``tickable`` (a fit
    of :data:`TICK_FIT` or more, openly licensed, small).

    With the reading helper a book a topic found need not name the subject
    ("The Laws of Etiquette" for a wedding planner): its meaning decides.
    Without it, a book must name the subject, or a topic in its title."""
    from puffpai.catalogs import goal_words
    from puffpai.finding.find import key_words
    topics = [str(t) for t in (topics or ()) if t]
    if phrase:          # the bot's subject, known already (book_subject)
        marks = goal_words.subject_marks(phrase)
    else:
        marks = goal_words.subject_marks(str(subject or ""))
        phrase = goal_words.subject_phrase(str(subject or ""))
    stories_ok = bool(set(f"{phrase} {job_words}".lower().split()) & STORY_WORDS)
    # The same book in two libraries ("A Flower Wedding" is a children's book
    # at Gutenberg; the Internet Archive's scan has no such subject): what
    # one library files it under counts for every copy, and one copy shows.
    twins: dict[str, list[dict[str, Any]]] = {}
    for f in finds:
        twins.setdefault(title_key(f), []).append(f)

    def off(f: dict[str, Any]) -> bool:
        same = twins[title_key(f)]
        return not any(practical(t) for t in same) and \
            any(off_genre(t, [phrase, job_words]) for t in same)

    kept = [f for f in finds if not off(f)]
    # A Wikipedia book made from a topic's words ("geometry") is not about
    # the bot: only the subject's own is shown.
    kept = [f for f in kept if f.get("tier") != "wikipedia_topic" or not marks
            or _names_subject(str(f.get("name") or ""), marks, phrase)]
    books_only = [f for f in kept if f.get("tier") != "wikipedia_topic"]
    near = likeness(books_only, meaning_texts(phrase or job_words, topics))
    if near is None:
        stems = _topic_stems(topics, phrase or "")
        subject_words = key_words(phrase or job_words, limit=6)
        kept = [f for f in kept if about(f, marks, job_words, stories_ok)
                or (f.get("tier") != "wikipedia_topic"
                    and any(s in str(f.get("name") or "").lower() for s in stems))]
        for f in kept:
            # Her own search with no subject: her words are the subject.
            if f.get("tier") != "wikipedia_topic":
                f["fit"] = word_fit(f, marks or subject_words, subject_words, topics)
    else:
        floor = max(MEANING_FLOOR, MEANING_SHARE * max(near.values()))
        for f in books_only:
            f["likeness"] = round(near[id(f)], 3)
            f["fit"] = near[id(f)]
        # Close in meaning, and naming the subject or one of the job's topics
        # (an old type specimen book is near "3d printing" in meaning, but
        # names neither 3d nor filament).
        stems = _topic_stems(topics, phrase or "")
        kept = [f for f in kept if f.get("tier") == "wikipedia_topic"
                or (near[id(f)] >= floor and (
                    (marks and goal_words.mentions_subject(_book_words(f), marks))
                    or any(st in _book_words(f).lower() for st in stems)
                    or not (marks or stems)))]
    for f in kept:
        if f.get("tier") != "wikipedia_topic":
            f["fit"] = round(min(1.0, float(f.get("fit") or 0)
                                 + (PRACTICAL_BONUS if practical(f) else 0.0)), 3)
    shown = set(shown)
    kept = [f for f in kept if f["id"] not in shown and title_key(f) not in shown]
    kept.sort(key=lambda f: (f.get("tier") == "wikipedia_topic", -float(f.get("fit") or 0)))
    out: list[dict[str, Any]] = []
    per_library: dict[str, int] = {}
    titles: set[str] = set()
    for f in kept:
        if title_key(f) in titles:
            continue          # the same book from another library: once
        tier = str(f.get("tier") or "")
        if per_library.get(tier, 0) >= PER_LIBRARY:
            continue
        per_library[tier] = per_library.get(tier, 0) + 1
        titles.add(title_key(f))
        out.append(f)
        if len(out) >= limit:
            break
    for rank, f in enumerate(out):
        f["rank"] = rank
        cand = (f.get("raw") or {}).get("candidate")
        if isinstance(cand, dict) and cand.get("id"):
            f["tickable"] = open_and_small(cand) and float(f.get("fit") or 0) >= TICK_FIT
    return out


def pick_ticked(finds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tick at most :data:`TICK_MAX` of the books shown for the whole bot:
    the best fitting first, then the best word match, the most downloaded."""
    best = sorted((f for f in finds if f.get("type") == "book" and f.get("tickable")),
                  key=lambda f: (-float(f.get("fit") or 0), -int(f.get("score") or 0),
                                 -float(f.get("likeness") or 0),
                                 -int((f.get("popularity") or {}).get("downloads") or 0),
                                 int(f.get("rank") or 0)))
    for f in best[:TICK_MAX]:
        f["ticked"] = True
    return best[:TICK_MAX]


__all__ = ["PER_JOB", "SITE", "TICK_FIT", "TICK_MAX", "TICK_MAX_BYTES", "about", "as_find",
           "clean_topic", "fallback_topics", "for_job", "format_words", "kind_words",
           "meaning_order", "off", "off_genre", "open_and_small", "pick_ticked", "queries",
           "search", "shares_subject", "tickable", "title_key", "topics_for_jobs"]
