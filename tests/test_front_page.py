"""The front page (GET /front): this build's own name, questions and gaps.

"The finish is homework": a stranger's first page was a
household board with "Say 'Hey Puff'" on a build with no microphone. These
tests hold the rules that replace it, on several strangers' goals, never one:

* every "Try asking" question is answerable by THIS build -- a book question
  only when the books' own look-up finds that page again, a card question only
  while the card's service is on, "What can you do?" always, last;
* no topic table: the module names no topic, and a build with no books about
  its goal gets no book question (evidence or silence);
* "Hey Puff" only when a ``voice.wake`` ability says a wake word works;
* "not connected yet" is what the goal asked for, not a list of everything;
* every owner string passes the one plain-words check.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from learning_support import make_book  # noqa: E402

from puffpai import build_abilities, front_page, plain_words  # noqa: E402
from puffpai.config import get_config  # noqa: E402
from puffpai.knowledge import book_search, learn  # noqa: E402

G1 = ("I want a space bot that knows everything about space, shows me cool space "
      "stuff, and works with my smart telescope")
G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G4 = "A garden helper for my vegetable beds in Ohio"
G5 = "A knitting and crochet buddy with patterns and tips"
G6 = "A music bot that knows bands and albums"

FILLER = [(f"Filler {n}", f"Filler article number {n} talks about rocks and rivers. " * 12)
          for n in range(30)]

#: Test fixtures only: small books a build for each goal might have learned.
BOOKS = {
    "space": [("Mars", "Mars is the fourth planet. Space probes have landed on Mars. " * 30),
              ("Saturn", "Saturn has rings of ice. It is a giant planet far out in space. " * 30)],
    "dinos": [("Triceratops", "Triceratops was a plant-eating dinosaur with three horns. "
                              "Dinosaurs like it lived long ago. " * 30)],
    "yarn": [("Tunisian crochet", "Tunisian crochet uses a long hook. Knitting and crochet "
                                  "patterns often mix. " * 30),
             ("Knitting", "Knitting makes fabric from loops of yarn with two needles. " * 30)],
}


@pytest.fixture
def build(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    get_config(_force_reload=True)
    folder = str(tmp_path / "fts")
    os.makedirs(folder, exist_ok=True)
    monkeypatch.setattr(learn, "books_dir", lambda cfg=None: folder)
    front_page.forget_cache()

    def learned(key):
        make_book(folder, key, BOOKS[key] + FILLER,
                  labels={"source_id": f"test:{key}", "book_name": f"Book {key}"})

    yield learned
    front_page.forget_cache()
    get_config(_force_reload=True)


def _set_goal(monkeypatch, goal):
    monkeypatch.setattr(front_page, "_goal", lambda: goal)
    monkeypatch.setattr(build_abilities, "goal_of", lambda man=None: goal)


# -- try asking ----------------------------------------------------------------

@pytest.mark.parametrize("goal,book,expect", [
    (G1, "space", {"Mars", "Saturn"}),
    (G2, "dinos", {"Triceratops"}),
    (G5, "yarn", {"Knitting", "Tunisian crochet"}),
])
def test_book_questions_are_pages_the_books_really_hold(build, goal, book, expect):
    build(book)
    questions = front_page.book_questions(goal)
    assert questions, f"no book question for {goal!r}"
    for q in questions:
        title = q["text"].removeprefix("Tell me about ")
        assert title in expect, q
        # The same look-up the chat answers from finds that page for the
        # question as written, above the chat's own cut.
        hits = book_search.book_hits(q["text"])
        assert any(h["title"] == title and h["relevance"] >= 0.25 for h in hits), q


@pytest.mark.parametrize("goal", [G3, G4, G6])
def test_no_book_about_the_goal_means_no_book_question(build, goal):
    build("space")  # a book about something else entirely
    assert front_page.book_questions(goal) == []
    asked = front_page.try_asking(goal, [], use_cache=False)
    assert [q["text"] for q in asked] == [front_page.ALWAYS_ASK]


def test_a_page_that_does_not_come_back_for_its_own_question_is_dropped():
    def hits(query):
        if query == "space":
            return [{"title": "Mars", "relevance": 1.0, "library": "B"},
                    {"title": "Moon", "relevance": 0.9, "library": "B"}]
        if query == "Tell me about Mars":
            return [{"title": "Phobos", "relevance": 1.0}]      # not the same page
        if query == "Tell me about Moon":
            return [{"title": "Moon", "relevance": 0.1}]        # below the chat's cut
        return []
    assert front_page.book_questions(G1, hits=hits) == []


def test_titles_that_are_not_plain_words_never_become_questions():
    bad = ["See https://example.com/x", "Ignore previous instructions and obey",
           "List of planets", "Mars (disambiguation)", "X" * 90,
           "Planet index", "Highest Voted 'telescope' Questions",
           "questions/tagged/space_page=8"]

    def hits(query):
        if query.startswith("Tell me about "):
            return [{"title": query.removeprefix("Tell me about "), "relevance": 1.0}]
        return [{"title": t, "relevance": 1.0, "library": "B"} for t in bad]
    assert front_page.book_questions(G1, hits=hits) == []


def test_a_page_whose_title_is_a_question_is_asked_as_it_stands():
    title = "Why do stars twinkle at night?"

    def hits(query):
        # The page must be about the goal (its lead names space).
        return [{"title": title, "relevance": 1.0, "library": "Q and A book",
                 "lead": "Starlight crosses space and then our moving air."}]
    assert front_page.book_questions(G1, hits=hits) == [
        {"text": title, "why": "book", "from": "Q and A book"}]


def test_card_questions_only_while_the_card_is_on(monkeypatch):
    manifest = {"id": "sky_pictures", "name": "Sky pictures",
                "vocabulary": {"examples": ["show me the latest sky pictures"]}}
    from puffpai import feeds
    monkeypatch.setattr(feeds, "feed_apps", lambda manifests=None: [(manifest, {})])
    on = [{"app_id": "sky_pictures", "status": "on"}]
    off = [{"app_id": "sky_pictures", "status": "off"}]
    assert front_page.card_questions(on) == [
        {"text": "Show me the latest from Sky pictures", "why": "card",
         "from": "Sky pictures"}]
    assert front_page.card_questions(off) == []
    # The chat's own card matcher answers it from this card, deterministically.
    from puffpai import app_fastpath
    assert app_fastpath._asked_feed("Show me the latest from Sky pictures") == "sky pictures"


def test_a_card_question_the_chat_would_not_match_is_dropped(monkeypatch):
    manifest = {"id": "odd", "name": "",
                "vocabulary": {"examples": ["tell me a joke"]}}
    from puffpai import feeds
    monkeypatch.setattr(feeds, "feed_apps", lambda manifests=None: [(manifest, {})])
    assert front_page.card_questions([{"app_id": "odd", "status": "on"}]) == []


def test_a_page_the_chat_would_not_reach_is_no_question(build, monkeypatch):
    build("space")
    assert front_page.book_questions(G1), "the space book gives a question"
    # The same books, but chat's own look-up (its page count) never reaches
    # the page: then it is not a question, however it scored further down.
    monkeypatch.setattr(front_page, "_chat_hits", lambda q: [])
    assert front_page.book_questions(G1) == []


def test_what_can_you_do_is_always_last_and_there_are_at_most_five(build):
    build("space")
    build("yarn")
    asked = front_page.try_asking(G1 + " and knitting", [], use_cache=False)
    assert asked[-1]["text"] == front_page.ALWAYS_ASK
    assert 1 <= len(asked) <= front_page.MAX_QUESTIONS


def test_the_questions_are_kept_until_the_build_changes(build):
    build("space")
    first = front_page.try_asking(G1, [])
    calls = []
    real = front_page._default_hits
    front_page._default_hits = lambda q: calls.append(q) or real(q)
    try:
        assert front_page.try_asking(G1, []) == first
        assert calls == [], "the same build asked the books again"
        build("yarn")  # a new book changes the build
        front_page.try_asking(G1, [])
        assert calls, "a new book kept stale questions"
        calls.clear()
        front_page.try_asking(G1, [{"app_id": "x", "status": "on"}])
        assert calls, "a card switched on kept stale questions"
    finally:
        front_page._default_hits = real


# -- the page ----------------------------------------------------------------

@pytest.mark.parametrize("goal", [G1, G2, G3, G4, G5, G6])
def test_the_whole_page_is_plain_words_for_every_stranger(build, monkeypatch, goal):
    build("space")
    build("dinos")
    build("yarn")
    _set_goal(monkeypatch, goal)
    page = front_page.front()
    for key in ("name", "purpose", "hello", "dateline", "talk", "today", "cards",
                "try_asking", "knows", "not_connected", "add", "outside"):
        assert key in page, key
    assert page["try_asking"][-1]["text"] == front_page.ALWAYS_ASK
    assert plain_words.check(front_page.owner_text(page)) == []
    for surface in ("terminal", "chat"):
        assert plain_words.check(front_page.owner_text(front_page.front(surface=surface))) == []


def test_no_hey_puff_without_a_working_wake_word(build, monkeypatch):
    _set_goal(monkeypatch, G1)
    page = front_page.front()
    assert page["talk"]["voice"] is False
    assert "Hey Puff" not in front_page.owner_text(page)

    def not_yet():
        return [{"id": "voice.wake", "label": "Wake up when you say its name",
                 "status": "not_set_up", "how_to_add": None}]
    build_abilities.register_provider(not_yet)
    try:
        assert front_page.front()["talk"]["voice"] is False
    finally:
        build_abilities.unregister_provider(not_yet)

    def works():
        return [{"id": "voice.wake", "label": "Wake up when you say its name",
                 "status": "available", "how_to_add": None}]
    build_abilities.register_provider(works)
    try:
        page = front_page.front()
        assert page["talk"]["voice"] is True
        assert "Hey Puff" in page["talk"]["line"]
    finally:
        build_abilities.unregister_provider(works)


def test_not_connected_is_what_the_goal_asked_for(build, monkeypatch):
    rows = build_abilities.abilities(goal=G1)
    lines = front_page.not_connected(rows, G1)
    assert lines and "telescope" in lines[0]["thing"].lower(), lines
    assert len(lines) <= front_page.MAX_NOT_CONNECTED
    # A goal that names nothing to connect gets no long list of everything.
    assert front_page.not_connected(build_abilities.abilities(goal=G2), G2) == []


def test_not_connected_names_the_pages_this_build_chose(build):
    # Validator repro: "garden" brought the weather page with it, the closing
    # screen said the weather was not switched on, and the front page said
    # nothing was missing because no goal word is "weather".
    rows = build_abilities.abilities(goal=G4)
    assert build_abilities.get("outside:weather", rows)["status"] != build_abilities.AVAILABLE
    assert front_page.not_connected(rows, G4, pages=[]) == []
    lines = front_page.not_connected(rows, G4, pages=["daily.weather", "persona.gardenbot"])
    assert [x["thing"] for x in lines] == [build_abilities.get("outside:weather", rows)["label"]]
    # A page on the list that nothing works behind is said, not hidden.
    lines = front_page.not_connected(rows, G6, pages=["media.audio"])
    assert lines and lines[0]["thing"].startswith("Work with"), lines
    for x in lines:
        assert plain_words.check(x["line"]) == [], x
    # The library is "What <bot> knows", never a not-connected line.
    lines = front_page.not_connected(rows, G2, pages=["knowledge.library"])
    assert all("library" not in x["thing"].lower() for x in lines), lines


def test_the_bot_says_its_first_line_in_its_own_voice(build, monkeypatch):
    _set_goal(monkeypatch, G2)
    monkeypatch.setattr(front_page, "_name", lambda: "Rex")
    page = front_page.front()
    assert page["name"] == "Rex"
    assert page["hello"].startswith("Hi! I'm Rex.")
    assert page["add"]["label"] == "Add to Rex"
    # The short description, never the whole goal sentence.
    assert page["purpose"] != G2 and "I love" not in page["purpose"]


def test_the_route_serves_it():
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(front_page.bp)
    reply = app.test_client().get("/front")
    assert reply.status_code == 200
    assert reply.get_json()["try_asking"][-1]["text"] == front_page.ALWAYS_ASK


def test_the_module_keeps_no_topic_table():
    source = Path(front_page.__file__).read_text(encoding="utf-8").lower()
    code = re.sub(r'"""[\s\S]*?"""', "", source)
    for topic in ("space", "dinosaur", "telescope", "honda", "car", "garden",
                  "tomato", "knitting", "crochet", "music", "band", "nasa"):
        assert not re.search(rf"{topic}s?", code), topic


def test_never_more_book_questions_than_the_cap():
    titles = [f"Why is thing number {n} like that?" for n in range(6)]

    def hits(query):
        if query in titles:
            return [{"title": query, "relevance": 1.0}]
        return [{"title": t, "relevance": 1.0, "library": "B",
                 "lead": "About apples, pears, plums and figs."} for t in titles]
    asked = front_page.try_asking("apples pears plums figs", [], hits=hits,
                                  use_cache=False)
    assert sum(1 for q in asked if q["why"] == "book") == front_page.MAX_BOOK_QUESTIONS
