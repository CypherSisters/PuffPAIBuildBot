"""Book topics (find step): the brain names a few book topics
per job, the libraries are searched for them, the books are ranked by
meaning against the job (not by the subject word in the title), stories,
children's books and sermons are left out, and the best fitting books start
ticked.

No network: the brain, the libraries and the reading helper are fakes.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json

import pytest

WEDDING = ("I want to build a wedding planner bot. We have so much to do. I'll need "
           "to track themes from pictures, come up with ideas, find wedding vendors "
           "and plan the budget.")
PRINTING = ("I want a 3D printing helper bot: find models to print, suggest print "
            "settings and keep track of our filament.")


def _book(name, tier="gutenberg", subjects=(), **cand):
    return {"id": f"book:{tier}:{name}", "type": "book", "type_words": "book",
            "name": name, "tier": tier, "score": 3, "site_name": tier, "line": name,
            "raw": {"candidate": dict({"id": f"{tier}:{name}", "subjects": list(subjects),
                                       "fetchable": True, "commercial_ok": True,
                                       "size_bytes": 1_000_000, "tier": tier}, **cand)}}


def _fake_helper(likeness):
    """The reading helper as a fake: a question is [1, 0]; a book is as close
    to it as *likeness* says for the start of its title."""
    def embed(texts):
        out = []
        for text in texts:
            if text.startswith("a practical guide to"):
                out.append([1.0, 0.0])
                continue
            x = next(v for k, v in likeness.items() if text.startswith(k))
            out.append([x, (1 - x * x) ** 0.5])
        return out
    return embed


# -- the brain names the topics -------------------------------------------------------------

def _jobs():
    from puffpai.finding import find
    return [dict(find._job(0, "Everything for a wedding planner bot", "wedding planner"),
                 books=[]),
            dict(find._job(1, "Plan the budget", "wedding expense tracker spreadsheet"),
                 books=[]),
            dict(find._job(2, "Find wedding vendors", "wedding vendor directory"), books=[])]


def test_the_brain_names_two_or_three_book_topics_per_job(monkeypatch):
    from puffpai.finding import books
    seen = []

    def brain(messages):
        seen.append(messages)
        return json.dumps({"jobs": [
            {"id": "j0", "topics": ["Etiquette", "entertaining", "wedding"]},
            {"id": "j1", "topics": ["household budget", "budget spreadsheet app"]},
            {"id": "j2", "topics": []}]})

    monkeypatch.setattr(books, "TOPIC_ASKER", brain)
    jobs = _jobs()
    assert books.topics_for_jobs(WEDDING, jobs) == "brain"
    # the subject alone is searched already; shape words go; at most two a job
    assert jobs[0]["topics"] == ["etiquette", "entertaining"]
    assert jobs[1]["topics"] == ["household budget", "budget"]
    # a job the brain named nothing for: its own words
    assert jobs[2]["topics"] == ["vendor"]
    # one call for every job, with every job in it
    assert len(seen) == 1 and "j2: Find wedding vendors" in seen[0][1]["content"]


def test_without_the_brain_the_topics_come_from_the_jobs_words(monkeypatch):
    from puffpai.finding import books

    def broken(_messages):
        raise TimeoutError("slow")
    for asker in (broken, lambda _m: "not json", None):
        monkeypatch.setattr(books, "TOPIC_ASKER", asker)
        jobs = _jobs()
        assert books.topics_for_jobs(WEDDING, jobs) == "words"
        assert jobs[1]["topics"] == ["expense", "budget"]
        assert jobs[2]["topics"] == ["vendor"]


def test_the_topic_prompt_is_for_any_bot():
    from puffpai.finding import books
    prompt = books.TOPIC_PROMPT.lower()
    for word in ("wedding", "3d", "printing", "etiquette"):
        assert word not in prompt
    assert "—" not in prompt and "door" not in prompt


# -- what the libraries are asked ---------------------------------------------------------------

def test_the_libraries_are_asked_the_subject_then_the_jobs_topics():
    from puffpai.finding import books
    assert books.queries("wedding expense tracker", WEDDING, [],
                         ["etiquette", "household budget"]) == [
        "wedding", "etiquette", "household budget"]
    assert books.queries("3d print failure", PRINTING, [],
                         ["troubleshooting", "filament"]) == [
        "3d printing", "troubleshooting", "filament"]


def test_topic_searches_pass_the_outbound_privacy_screen():
    from puffpai.finding import books
    sent = books.queries("meal planning", "Emma's diabetes meals bot", [],
                         ["insulin dosing", "cookery", "emma"])
    for word in ("emma", "diabetes", "insulin"):
        assert word not in " ".join(sent).split()
    assert "cookery" in sent


# -- which books are kept, and in what order -------------------------------------------------

def test_stories_childrens_books_and_sermons_are_left_out():
    from puffpai.finding import books
    finds = [_book("The Wedding Ring: Discourses for Husbands and Wives",
                   subjects=["Marriage", "Religion/Spirituality"]),
             _book("A Flower Wedding Described by Two Wallflowers",
                   subjects=["Children's poetry", "Nursery rhymes"]),
             _book("The white wedding", "archive", subjects=["Fiction"]),
             _book("Sermons on marriage", "archive"),
             _book("The Wedding Day", subjects=["Marriage service", "How To ...",
                                                "Religion/Spirituality"]),
             _book("The etiquette of engagement and marriage",
                   subjects=["Wedding etiquette", "Romance", "How To ..."])]
    dropped = [f["name"] for f in finds if books.off_genre(f, ["wedding planner"])]
    assert dropped == ["The Wedding Ring: Discourses for Husbands and Wives",
                       "A Flower Wedding Described by Two Wallflowers",
                       "The white wedding", "Sermons on marriage"]
    # a bot about stories keeps them
    assert not books.off_genre(finds[2], ["bedtime stories", "fiction"])
    assert not books.off_genre(finds[3], ["church sermons"])


def test_a_topic_book_without_the_subject_word_is_ranked_by_meaning(monkeypatch):
    """Try-it A10: "The Laws of Etiquette" was only found when "etiquette"
    was searched; now the job's topic finds it and its meaning keeps it."""
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", _fake_helper({
        "The Laws of Etiquette": 0.62, "The etiquette of engagement": 0.73,
        "Strange Teas": 0.35, "Practical Book of Outdoor Rose": 0.3}))
    finds = [_book("Strange Teas, Dinners, Weddings and Fetes",
                   subjects=["Travel Writing"]),
             _book("The Laws of Etiquette", subjects=["Etiquette", "How To ..."]),
             _book("Practical Book of Outdoor Rose Growing", "archive"),
             _book("The etiquette of engagement and marriage", "archive",
                   subjects=["Wedding etiquette"])]
    kept = books.for_job(finds, WEDDING, "wedding etiquette", topics=["etiquette"])
    assert [f["name"] for f in kept] == ["The etiquette of engagement and marriage",
                                         "The Laws of Etiquette"]
    assert kept[1]["fit"] == pytest.approx(0.67, abs=0.01)   # 0.62 + practical shelf
    assert all(f["tickable"] for f in kept)


def test_without_the_reading_helper_a_topic_in_the_title_keeps_a_book(monkeypatch):
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", False)
    finds = [_book("The Laws of Etiquette", subjects=["Etiquette"]),
             _book("Practical Book of Outdoor Rose Growing", "archive"),
             _book("The Wedding Day")]
    kept = books.for_job(finds, WEDDING, "wedding", topics=["etiquette"])
    assert [f["name"] for f in kept] == ["The Wedding Day", "The Laws of Etiquette"]
    assert kept[0]["fit"] == pytest.approx(0.6) and kept[1]["fit"] == pytest.approx(0.3)


# -- which start ticked ------------------------------------------------------------------------

def test_the_best_fitting_books_start_ticked_not_the_first_found(monkeypatch):
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", _fake_helper({
        "The Wedding Ring": 0.59, "Wedding hymns": 0.5, "The etiquette": 0.73,
        "Wedding Q&A": 0.7, "Wedding customs": 0.56}))
    finds = [_book("Wedding customs"),
             _book("Wedding Q&A", "kiwix", commercial_ok=False),     # not open
             _book("The etiquette of engagement and marriage", "archive")]
    kept = books.for_job(finds, WEDDING, "wedding", topics=["etiquette"])
    picked = books.pick_ticked(kept)
    assert [f["name"] for f in picked] == ["The etiquette of engagement and marriage",
                                           "Wedding customs"]
    # below the fit a book may start ticked at: shown, not ticked
    monkeypatch.setattr(books, "EMBED", _fake_helper({"Wedding customs": 0.5}))
    kept = books.for_job([_book("Wedding customs")], WEDDING, "wedding")
    assert kept and not kept[0]["tickable"]


def test_every_job_gets_its_topics_books(monkeypatch):
    from puffpai.finding import books, find
    monkeypatch.setattr(books, "EMBED", _fake_helper({
        "The Wedding Day": 0.58, "The etiquette": 0.73, "The Laws of Etiquette": 0.62,
        "Household budget": 0.6, "The white wedding": 0.4}))
    answers = {
        "wedding": [_book("The Wedding Day", subjects=["How To ..."]),
                    _book("The white wedding", "archive"),
                    _book("The etiquette of engagement and marriage",
                          subjects=["Wedding etiquette"])],
        "etiquette": [_book("The Laws of Etiquette", subjects=["Etiquette", "How To ..."])],
        "household budget": [_book("Household budget book", "archive")],
    }
    asked: list[str] = []

    def libraries(words, _n):
        asked.append(words)
        return [dict(f) for f in answers.get(words, [])]

    monkeypatch.setitem(find._SEARCHERS, "libraries", libraries)
    monkeypatch.setattr(books, "TOPIC_ASKER", lambda _m: json.dumps({"jobs": [
        {"id": "j0", "topics": ["wedding etiquette"]},
        {"id": "j1", "topics": ["household budget"]},
        {"id": "j2", "topics": ["etiquette"]}]}))
    jobs = _jobs()
    books.topics_for_jobs(WEDDING, jobs)
    # searched without the subject's word: a library wants both in a title
    assert jobs[0]["topic_searches"] == ["etiquette"]
    result = find.search_jobs(jobs, kinds=["book"], subject=WEDDING)
    assert asked[0] == "wedding" and asked.count("wedding") == 1
    assert asked.count("etiquette") == 1                 # sent once for two jobs
    shown = {job["name"]: [f["name"] for t in job["types"] for f in t["finds"]]
             for job in result["jobs"]}
    assert "The Laws of Etiquette" in shown["Everything for a wedding planner bot"]
    assert "Household budget book" in shown["Plan the budget"]
    flat = [n for names in shown.values() for n in names]
    assert "The white wedding" not in flat                # 0.4 < 0.6 x 0.73 and the floor
    ticked = [f["name"] for job in result["jobs"] for t in job["types"]
              for f in t["finds"] if f.get("ticked")]
    assert ticked == ["The etiquette of engagement and marriage", "The Laws of Etiquette"]


def test_the_same_book_in_two_libraries_shows_once_and_shares_its_subjects(monkeypatch):
    """Try-it A10: the Internet Archive's scan of a children's picture book
    has no subjects; Gutenberg's copy says "Juvenile poetry"."""
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", _fake_helper({
        "A flower wedding": 0.6, "A Flower Wedding": 0.6, "The ladies' book": 0.55,
        "The etiquette": 0.73}))
    finds = [_book("A flower wedding : described by two wallflowers", "archive"),
             _book("A Flower Wedding Described by Two Wallflowers",
                   subjects=["Children's poetry", "Flowers -- Juvenile poetry"]),
             _book("The ladies' book of etiquette, and manual of politeness; a c", "archive"),
             _book("The ladies' book of etiquette, and manual of politeness : a", "archive"),
             _book("The etiquette of engagement and marriage")]
    kept = [f["name"] for f in books.for_job(finds, WEDDING, "wedding", topics=["etiquette"])]
    assert kept == ["The etiquette of engagement and marriage",
                    "The ladies' book of etiquette, and manual of politeness; a c"]
    # shown for an earlier job under another library's id: not shown again
    later = books.for_job([_book("The etiquette of engagement and marriage", "archive")],
                          WEDDING, "wedding", shown={books.title_key(finds[4])})
    assert later == []


def test_a_book_near_in_meaning_must_name_the_subject_or_a_topic(monkeypatch):
    """Dry run: an old type specimen book came back from the "3d printing"
    search and sat near it in meaning; it names neither 3d nor filament."""
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", _fake_helper({
        "American specimen book": 0.57, "3D Printing": 0.84, "Filament winding": 0.6,
        "Printing": 0.58}))
    finds = [_book("American specimen book of type styles", "archive",
                   subjects=["Printing -- Specimens"]),
             _book("Printing", "wikibooks"),
             _book("3D Printing", "wikibooks"),
             _book("Filament winding", "archive")]
    kept = books.for_job(finds, PRINTING, "track filament usage", topics=["filament", "spool"])
    assert [f["name"] for f in kept] == ["3D Printing", "Filament winding"]


# -- any wording of her goal -----------------------------------------

PUPPY = "a bot to help me train my new puppy"
GUITAR = "a bot to help me learn to play guitar"


def test_a_subject_for_any_wording_of_the_goal():
    from puffpai.catalogs import goal_words
    assert goal_words.subject_phrase(PUPPY) == ""          # the screen's own rule, unchanged
    assert goal_words.plain_subject(PUPPY) == "puppy"
    assert goal_words.plain_subject(GUITAR) == "guitar"
    assert goal_words.plain_subject("I want a puppy training bot") == "puppy training"
    assert goal_words.plain_subject(WEDDING) == "wedding planner"
    assert goal_words.plain_subject("a bot to help bot") == ""


def _puppy_jobs():
    from puffpai.finding import find
    return [dict(find._job(0, "Everything for a puppy bot", "puppy"), books=[]),
            dict(find._job(1, "Teach basic commands", "dog command list app"), books=[]),
            dict(find._job(2, "Feeding times", "feeding schedule tracker"), books=[])]


def test_the_brain_names_the_subject_and_every_topic_is_sent(monkeypatch):
    from puffpai.finding import books
    monkeypatch.setattr(books, "TOPIC_ASKER", lambda _m: json.dumps({
        "subject": "puppy training",
        "jobs": [{"id": "j0", "topics": ["dog training"]},
                 {"id": "j1", "topics": ["obedience"]},
                 {"id": "j2", "topics": ["dog feeding", "png"]}]}))
    jobs = _puppy_jobs()
    assert books.topics_for_jobs(PUPPY, jobs) == "brain"
    assert {j["book_subject"] for j in jobs} == {"puppy training"}
    sent = [books.queries(j["words"], PUPPY, j.get("books"), j["topic_searches"],
                          j["book_subject"]) for j in jobs]
    assert sent == [["puppy training", "dog training"], ["puppy training", "obedience"],
                    ["puppy training", "dog feeding"]]


def test_with_the_brain_down_the_goal_still_gives_a_subject_and_searches(monkeypatch):
    from puffpai.finding import books, find
    jobs, _line = find.jobs_for_goal(PUPPY, cfg={"ollama_url": "", "chosen_model": ""})
    assert jobs[0]["name"] == "Everything for a puppy bot"
    monkeypatch.setattr(books, "TOPIC_ASKER", lambda _m: (_ for _ in ()).throw(OSError()))
    jobs = _puppy_jobs()
    assert books.topics_for_jobs(PUPPY, jobs) == "words"
    assert jobs[0]["book_subject"] == "puppy"
    sent = books.queries(jobs[1]["words"], PUPPY, [], jobs[1]["topic_searches"],
                         jobs[1]["book_subject"])
    assert sent and sent[0] == "puppy" and "app" not in " ".join(sent).split()


def test_with_the_brain_down_the_whole_bot_job_never_searches_filler(monkeypatch):
    from puffpai.finding import books
    goal = "a bot to help me keep backyard chickens"
    job = {"id": "j0", "name": "Everything for a backyard chicken bot",
           "words": "backyard chicken"}
    topics = books.fallback_topics(job, "backyard chicken")
    assert "everything" not in topics and "bot" not in topics
    monkeypatch.setattr(books, "TOPIC_ASKER", lambda _m: (_ for _ in ()).throw(OSError()))
    jobs = [dict(job)]
    assert books.topics_for_jobs(goal, jobs) == "words"
    sent = books.queries(jobs[0]["words"], goal, [], jobs[0]["topic_searches"],
                         jobs[0]["book_subject"])
    assert sent and "everything" not in " ".join(sent).split()


def test_topics_are_sent_without_a_subject_and_unsearchable_ones_fall_back():
    from puffpai.finding import books
    assert books.queries("tuning", "", [], ["chords", "scales"]) == ["chords", "scales"]
    # file types and software words never leave
    assert books.queries("chord diagrams", "", [], ["guitar chord diagrams png",
                                                   "habit open source"]) == [
        "guitar chord diagrams", "habit"]
    jobs = [{"id": "j1", "name": "Make a chart", "words": "guitar chord chart"}]
    books.TOPIC_ASKER = lambda _m: json.dumps({"subject": "guitar",
                                               "jobs": [{"id": "j1", "topics": ["guitar"]}]})
    try:
        books.topics_for_jobs(GUITAR, jobs)
    finally:
        books.TOPIC_ASKER = None
    # its only topic is the subject itself: the job's words make the pair
    assert jobs[0]["topics"] == ["chord"] and jobs[0]["topic_searches"] == ["chord"]
    assert books.queries("guitar chord chart", GUITAR, None, None, "guitar") == [
        "guitar", "guitar chord"]


def test_a_two_word_search_with_no_book_is_asked_again_with_one_word(monkeypatch):
    from puffpai.catalogs import gutendex
    from puffpai.finding import books, find
    monkeypatch.setattr(books, "EMBED", False)
    monkeypatch.setattr(gutendex, "load_snapshot", lambda *a, **k: {
        "index": {"canine": {1, 2}, "health": set(range(900))}})
    assert books.retry_word("canine health") == "canine"     # "health" is too broad
    assert books.retry_word("layer adhesion") == ""
    answers = {"puppy": [], "canine": [_book("The canine companion", subjects=["Dogs"])]}
    asked: list[str] = []

    def libraries(words, _n):
        asked.append(words)
        return [dict(f) for f in answers.get(words, [])]

    monkeypatch.setitem(find._SEARCHERS, "libraries", libraries)
    job = dict(find._job(0, "Everything for a puppy bot", "puppy"), books=[],
               topics=["canine health"], topic_searches=["canine health"],
               book_subject="puppy")
    result = find.search_jobs([job], kinds=["book"], subject=PUPPY)
    assert asked == ["puppy", "canine health", "canine"]
    names = [f["name"] for t in result["jobs"][0]["types"] for f in t["finds"]]
    assert names == ["The canine companion"]


def test_a_wikipedia_book_is_never_scored(monkeypatch):
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", False)
    finds = [_book("Wikipedia articles about puppy", "wikipedia_topic"),
             _book("The puppy book")]
    kept = books.for_job(finds, PUPPY, "puppy", phrase="puppy")
    wiki = next(f for f in kept if f["tier"] == "wikipedia_topic")
    assert "fit" not in wiki and not wiki.get("tickable")
