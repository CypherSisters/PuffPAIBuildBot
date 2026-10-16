"""The find step searches the free
libraries with the bot's subject and short subject + topic pairs, keeps only
books about the subject, and the plan and the build keep the libraries on.

No network: library answers are made up here, and the reading helper is a
fake (or switched off).

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import pytest

WEDDING = ("I want to build a wedding planner bot. We have so much to do. I'll need "
           "to track themes from pictures, come up with ideas, find wedding vendors "
           "and plan the budget.")
PRINTING = ("I want a 3D printing helper bot: find models to print, suggest print "
            "settings and keep track of our filament.")


def _book(name, tier="gutenberg", subjects=()):
    return {"id": f"book:{tier}:{name}", "type": "book", "type_words": "book",
            "name": name, "tier": tier, "score": 3, "site_name": tier, "line": name,
            "raw": {"candidate": {"subjects": list(subjects)}}}


@pytest.fixture
def no_helper(monkeypatch):
    from puffpai.finding import books
    monkeypatch.setattr(books, "EMBED", False)
    return books


# -- what the libraries are asked ---------------------------------------------------------

def test_the_libraries_are_asked_the_subject_then_a_subject_topic_pair():
    from puffpai.catalogs import goal_words
    from puffpai.finding import books
    assert goal_words.subject_marks(WEDDING) == ["wedding"]
    assert goal_words.subject_marks(PRINTING) == ["3d"]
    # a job the brain worked out: the pair comes from its book searches only
    assert books.queries("wedding expense tracker spreadsheet", WEDDING, []) == ["wedding"]
    assert books.queries("wedding vendor directory", WEDDING,
                         ["wedding planning guide articles"]) == [
        "wedding", "wedding planning"]
    assert books.queries("3d print failure", PRINTING, ["filament temperature chart"]) == [
        "3d printing", "3d printing filament temperature"]
    # her own search in chat: the pair comes from her words
    assert books.queries("wedding etiquette books", WEDDING) == ["wedding", "wedding etiquette"]
    # no subject known: her words as they are
    assert books.queries("knitting", "") == ["knitting"]


# -- which books are kept -----------------------------------------------------------------

def test_only_books_about_the_subject_are_kept(no_helper):
    books = no_helper
    finds = [_book("American specimen book of type styles", "archive",
                   ["Printing -- Specimens", "Type and type-founding"]),
             _book("Areopagitica, a speech for unlicensed printing"),
             _book("3D Printing", "wikibooks"),
             _book("Wikipedia articles about printing", "wikipedia_topic"),
             _book("3D Printing Q&A", "kiwix")]
    kept = [f["name"] for f in books.for_job(finds, PRINTING, "print settings")]
    assert kept == ["3D Printing", "3D Printing Q&A", "Wikipedia articles about printing"]
    finds = [_book("Costume of prelates of the Catholic church", "archive",
                   ["Church vestments"]),
             _book("O Perfect Love: Wedding Song", subjects=["Wedding music"]),
             _book("The tinker's wedding and other plays", "archive"),
             _book("The etiquette of engagement and marriage",
                   subjects=["Courtship", "Wedding etiquette"]),
             _book("The Wedding Day")]
    kept = [f["name"] for f in books.for_job(finds, WEDDING, "wedding", topics=["etiquette"])]
    # a song is not a book to learn from; a vestments book is not about weddings
    assert kept == ["The etiquette of engagement and marriage", "The Wedding Day"]


def test_her_own_search_for_another_subject_is_not_narrowed(no_helper):
    from puffpai.finding import find
    books = no_helper
    assert not books.shares_subject("knitting", WEDDING)
    assert books.shares_subject("wedding etiquette", WEDDING)
    assert find._book_subject({"words": "knitting", "own": True}, WEDDING) == ""
    assert find._book_subject({"words": "image tagger"}, WEDDING) == WEDDING


def test_the_reading_helper_ranks_by_meaning_and_drops_the_clearly_unrelated(monkeypatch):
    from puffpai.finding import books
    likeness = {"Good manners for all occasions": 0.9, "The Wedding Day": 0.6,
                "The mouse's wedding": 0.3, "The wedding guest": 0.7}

    def embed(texts):
        out = [[1.0, 0.0]]                       # the question
        for text in texts[1:]:
            x = next(v for k, v in likeness.items() if text.startswith(k))
            out.append([x, (1 - x * x) ** 0.5])
        return out

    monkeypatch.setattr(books, "EMBED", embed)
    finds = [_book("The mouse's wedding", "archive"), _book("The Wedding Day"),
             _book("Good manners for all occasions", "archive", ["Wedding etiquette"]),
             _book("The wedding guest", "archive")]
    # The best one was shown for an earlier job: it is not shown again, but
    # the mouse's wedding is still far from it (0.3 < 0.6 x 0.9).
    kept = books.for_job(finds, WEDDING, "wedding",
                         shown={"book:archive:Good manners for all occasions"})
    assert [f["name"] for f in kept] == ["The wedding guest", "The Wedding Day"]
    assert kept[0]["likeness"] == pytest.approx(0.7, abs=0.01)


def test_the_find_step_sends_the_subject_once_and_spreads_books_over_jobs(monkeypatch,
                                                                         no_helper):
    from puffpai.finding import find
    answers = {
        "wedding": [_book("The Wedding Day"), _book("The Wedding Ring"),
                    _book("The etiquette of engagement and marriage",
                          subjects=["Wedding etiquette"]),
                    _book("Strange Teas, Dinners, Weddings and Fetes")],
        "wedding planning": [_book("Wikipedia articles about wedding planning",
                                   "wikipedia_topic")],
    }
    asked: list[str] = []

    def libraries(words, _n):
        asked.append(words)
        return [dict(f) for f in answers.get(words, [])]

    monkeypatch.setitem(find._SEARCHERS, "libraries", libraries)
    jobs = [dict(find._job(0, "Everything for a wedding planner bot", "wedding planner"),
                 books=[]),
            dict(find._job(1, "Generate ideas", "wedding theme checklist"),
                 books=["wedding planning guide articles"]),
            dict(find._job(2, "Manage the budget", "wedding expense tracker"), books=[])]
    result = find.search_jobs(jobs, kinds=["book"], subject=WEDDING)
    assert asked.count("wedding") == 1                  # sent once for the bot
    assert asked.index("wedding") < asked.index("wedding planning")
    shown = [[f["name"] for t in job["types"] for f in t["finds"]] for job in result["jobs"]]
    flat = [n for names in shown for n in names]
    assert len(flat) == len(set(flat))                  # each book once
    # at most two from one library a job, so the rest go to the next jobs
    assert len(shown[0]) == 2 and shown[1]
    assert {"The etiquette of engagement and marriage",
            "Strange Teas, Dinners, Weddings and Fetes"} <= set(flat)


# -- a yes while finding things, before the bot is made ------------------------------------

def test_a_book_added_while_finding_things_waits_for_the_build(monkeypatch, tmp_path):
    """Try-it B11: "Yes, add these" for a Kiwix file said "This bot has not
    been set up yet. Run `puffpai setup` first". It now waits for the build,
    which starts the fetch; nothing tells her to run a command."""
    from puffpai.finding import books, find, install
    from puffpai.knowledge import find_more
    from puffpai.wizard import factory
    assert find_more.recorded_posture() is None          # fresh: no build yet
    cand = {"id": "kiwix:3dprinting.stackexchange.com_en_all", "name": "3D Printing Q&A",
            "tier": "kiwix", "fetchable": True, "commercial_ok": True,
            "license": "CC-BY-SA-4.0", "license_plain": "CC-BY-SA-4.0, free to use",
            "size_bytes": 120_000_000, "what_it_is": "Questions and answers",
            "source_catalog": "Kiwix library", "score": 3}
    f = books.as_find(cand, "3d printing", 0)
    find.remember([f])
    out = install.install_thing(f["id"], via="wizard")
    assert out["status"] == "added", out
    assert "once your bot is made" in out["line"]
    assert "set up yet" not in out["line"] and "`" not in out["line"]
    assert cand["id"] in find_more.pending_orders()
    # The background learner keeps it waiting until the bot is made.
    got = find_more.run_orders()
    assert got == [{"id": cand["id"], "ok": False, "reason": "not_set_up"}]
    assert cand["id"] in find_more.pending_orders()
    # The build ends by starting the learner, which fetches it.
    started: list[str] = []
    monkeypatch.setattr(find_more, "_learn_rest_in_background", started.append)
    monkeypatch.setattr(find_more, "knowledge_folder", lambda create=False: str(tmp_path))
    factory._fetch_waiting_books()
    assert started == [str(tmp_path)]
    # Fetched another way (the plan's own line): no longer waiting.
    find_more._order_done(cand["id"])
    assert cand["id"] not in find_more.pending_orders()


# -- the plan's knowledge defaults --------------------------------------------------------

def test_an_off_subject_library_pack_is_never_ticked_by_default():
    """Try-it A12: "TED ideas" started ticked for a wedding planner because
    her sentence said "come up with ideas"."""
    from puffpai.wizard import sources_plan
    ted = {"id": "kiwix:ted_mul_ideas", "label": "TED ideas", "selectable": True,
           "selected_by": [], "bytes": 269_850_624, "matched_terms": ["ideas"]}
    qa = {"id": "kiwix:wedding_qa", "label": "Wedding Q&A", "selectable": True,
          "selected_by": [], "bytes": 10_000_000, "matched_terms": ["wedding"]}
    assert sources_plan.goal_match(ted, WEDDING)          # a word of her sentence
    assert sources_plan.default_selection([ted], WEDDING) == []
    assert sources_plan.default_selection([ted, qa], WEDDING) == ["kiwix:wedding_qa"]
    cand = {"id": "kiwix:ted_mul_ideas", "name": "TED ideas", "fetchable": True,
            "tier": "kiwix", "size_bytes": 269_850_624, "match_terms": ["ideas"]}
    assert sources_plan.knowledge_default_on([cand], WEDDING) == set()
    printing = dict(cand, id="kiwix:3dprinting", name="3D Printing Q&A",
                    match_terms=["printing"])
    assert sources_plan.knowledge_default_on([printing], PRINTING) == {"kiwix:3dprinting"}


# -- what may leave (security pass, item 6) -----------------------------------------------

def test_the_library_searches_pass_the_outbound_privacy_screen():
    """Names and health words in her goal never go to a library; the bot's
    subject still does. One held word holds the whole search."""
    from puffpai.finding import books
    for goal, job in (("Emma's diabetes meals", "meal planning"),
                      ("Jose's baseball stats", "daily log"),
                      ("a cooking bot", "insulin dosing")):
        sent = " ".join(books.queries(job, goal, [job]))
        for word in ("emma", "diabetes", "jose", "insulin"):
            assert word not in sent.split(), (goal, sent)
    assert books.queries("insulin dosing", "a cooking bot", ["insulin dosing"]) == ["cooking"]
    assert books.queries("3d print failure", PRINTING, ["filament temperature chart"]) == [
        "3d printing", "3d printing filament temperature"]
    # no subject: her key words, at most four, screened the same way
    assert books.queries("books about knitting patterns for beginners please", "") == [
        "knitting patterns beginners"]
    assert books.queries("Emma's knitting", "") == []


def test_the_library_searches_fail_closed(monkeypatch):
    from puffpai.finding import books
    from puffpai.security import outbound_query

    def broken(*_a, **_k):
        raise RuntimeError("screen down")
    monkeypatch.setattr(outbound_query, "screen", broken)
    assert books.queries("wedding etiquette books", WEDDING) == []
