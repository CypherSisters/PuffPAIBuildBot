"""What the bot offers must fit what the owner asked for.

All real: "add tomato diseases
to you" (a garden bot) offered "Special Report on Diseases of the Horse";
"cross stitch tips" offered "Cross Validated" (a statistics site) and "a
daily cross update"; "car parts and recalls" offered "Langley Memoir on
Mechanical Flight"; a Honda goal got a "Toyota Corolla" question. Each shared
ONE word with the request. One rule now decides every place that offers
something (goal_words.match_strength); it knows no topic.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import random

import pytest

from add_more_support import built, guard_network
from puffpai import add_more, front_page, offers, plain_words, routines
from puffpai.catalogs import goal_words, knowledge_resolver
from puffpai.wizard import sources_plan

GARDEN = "A garden helper for my vegetable beds in Ohio"
KNITTING = "A knitting and crochet buddy with patterns and tips"
CIVIC = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
SPACE = ("I want a space bot that knows everything about space, shows me cool "
         "space stuff, and works with my smart telescope")


# -- the owner's phrases -----------------------------------------------------------

@pytest.mark.parametrize("words,runs", [
    ("cross stitch tips", [["cross", "stitch"]]),
    ("tomato diseases", [["tomato", "disease"]]),
    ("car parts and recalls", [["car", "part"], ["recall"]]),
    ("cross-stitch", [["cross", "stitch"]]),
    (CIVIC, [["honda", "civic"], ["maintenance"], ["recall"]]),
    (KNITTING, [["knitting"], ["crochet"], ["pattern"]]),
])
def test_phrases_are_the_words_typed_next_to_each_other(words, runs):
    assert goal_words.request_runs(words) == runs


# -- the rule ------------------------------------------------------------------------

HORSE = ("Special Report on Diseases of the Horse. A free old book from Project "
         "Gutenberg. About: Horses -- Diseases; Zoology; Nature/Gardening/Animals")
HORSE_SUBJECTS = ["Horses -- Diseases", "Zoology", "Nature/Gardening/Animals"]
TOMATO = ("Tomato Culture: A Practical Treatise on the Tomato. A free old book. "
          "About: Tomatoes; How To ...; Nature/Gardening/Animals")
TOMATO_SUBJECTS = ["Tomatoes", "How To ...", "Nature/Gardening/Animals"]
GARDEN_GOAL = dict(goal=["garden", "vegetable", "bed", "ohio"], shelves=["gardening"])


@pytest.mark.parametrize("request_words,text,kwargs", [
    ("tomato diseases", HORSE, dict(subjects=HORSE_SUBJECTS, **GARDEN_GOAL)),
    ("cross stitch tips", "Cross Validated. Stack Exchange Q&A for statistics",
     dict(subjects=["cross"], goal=["knitting", "crochet"], shelves=["crafts"])),
    ("car parts and recalls", "Langley Memoir on Mechanical Flight, Parts I and II",
     dict(subjects=["parts", "mechanical"], shelves=["vehicle", "mechanic"])),
    # One stray word of the request plus a goal word is not enough either:
    ("car parts and recalls", "The Mail Pay on the Burlington Railroad. Car Space",
     dict(subjects=["Railway mail service"], goal=["space", "telescope"])),
    ("black holes", "Cave Regions of the Ozarks and Black Hills",
     dict(subjects=["Caves", "Black Hills (S.D. and Wyo.)"], **GARDEN_GOAL)),
])
def test_one_shared_word_never_fits(request_words, text, kwargs):
    assert goal_words.match_strength(request_words, text, **kwargs)[0] == 0


@pytest.mark.parametrize("request_words,text,kwargs,level", [
    ("cross stitch tips", "Cross-stitch patterns for beginners", {}, 3),
    ("black holes", "Black hole. A black hole is a region of spacetime", {}, 3),
    ("spark plug replacement", "Spark plug. A spark plug is a device", {}, 3),
    ("tomato diseases", "Diseases of the tomato and how to treat them", {}, 3),
    ("car parts and recalls", "NHTSA recalls. Safety recalls for a car", {}, 2),
    ("pterosaurs", "Paleobiology Database. Fossil records",
     dict(subjects=["dinosaur", "pterosaur"]), 3),
    # The request's first word, filed by the catalog under it, with a goal word.
    ("tomato diseases", TOMATO, dict(subjects=TOMATO_SUBJECTS, **GARDEN_GOAL), 1),
])
def test_what_fits(request_words, text, kwargs, level):
    assert goal_words.match_strength(request_words, text, **kwargs)[0] == level


def test_a_shelf_in_the_title_fits_only_when_the_caller_says_so():
    text = "Astronomy Stack Exchange. Q&A for astronomers"
    assert not goal_words.fits_request(SPACE, text, shelves=["astronomy"])
    assert goal_words.fits_request(SPACE, text, shelves=["astronomy"],
                                   title="Astronomy Stack Exchange")


def test_goal_words_break_ties():
    a = goal_words.match_strength("tomato diseases", "Tomato diseases",
                                  goal=["garden"])
    b = goal_words.match_strength("tomato diseases", "Tomato diseases in the garden",
                                  goal=["garden"])
    assert a[0] == b[0] == 3 and b[1] > a[1]


# -- add more ------------------------------------------------------------------------

def _labels(session, section=None):
    return [i["label"] for i in session["items"]
            if section is None or i["section"] == section]


def test_tomato_diseases_for_a_garden_bot(monkeypatch):
    built(goal=GARDEN)
    guard_network(monkeypatch)
    session = add_more.find("tomato diseases", via="terminal", client="t")
    books = " | ".join(_labels(session, "knowledge"))
    assert "Horse" not in books and "TED gardening" not in books
    assert "Tomato Culture" in books
    assert _labels(session, "updates") == ["A daily garden update at 8 am"]


def test_tomato_diseases_for_a_bot_about_something_else_says_so(monkeypatch):
    built(goal="I love dinosaurs")
    guard_network(monkeypatch)
    session = add_more.find("tomato diseases", via="terminal", client="t")
    assert not add_more.actionable(session)
    lines = add_more.nothing_found_lines(session, "terminal")
    assert lines[0] == "I couldn't find anything I can add for \"tomato diseases\"."
    assert "Look online too?" in add_more.online_line(session, "terminal")


def test_cross_stitch_tips_never_offers_statistics_or_a_cross_update(monkeypatch):
    built(goal=KNITTING)
    guard_network(monkeypatch)
    session = add_more.find("cross stitch tips", via="terminal", client="t")
    text = " | ".join(_labels(session))
    assert "Cross Validated" not in text and "daily cross" not in text


def test_car_parts_and_recalls(monkeypatch):
    built(goal=CIVIC)
    guard_network(monkeypatch)
    session = add_more.find("car parts and recalls", via="terminal", client="t")
    text = " | ".join(_labels(session))
    for gone in ("Langley", "Aviation", "driverless"):
        assert gone not in text
    assert "Live: NHTSA recalls" in text
    # The car bot's own words let the mechanics Q&A in; a space bot's do not.
    assert "Motor Vehicle Mechanics" in text


def test_car_parts_for_a_space_bot_keeps_only_the_recalls(monkeypatch):
    built(goal=SPACE)
    guard_network(monkeypatch)
    session = add_more.find("car parts and recalls", via="terminal", client="t")
    books = [i for i in session["items"] if i["section"] == "knowledge"
             and not i["rides_along"]]
    assert books == []
    assert "Live: NHTSA recalls" in _labels(session, "live_info")


def test_the_update_line_is_plain_when_the_bot_has_no_topic(monkeypatch):
    built()
    guard_network(monkeypatch)
    session = add_more.find("pterosaurs", via="terminal", client="t")
    assert _labels(session, "updates") == ["A daily update at 8 am"]
    assert "Live: Paleobiology Database" in _labels(session, "live_info")


# -- rephrasings: a filler word must not cut a generic word loose ------------------
# Independent check on the first cut: "diseases THAT affect ...", "parts FOR my
# car", "problems DOES my car" isolated the generic word, and it fit alone.

@pytest.mark.parametrize("request_words,text,kwargs", [
    ("diseases that affect cats", HORSE, dict(subjects=HORSE_SUBJECTS, **GARDEN_GOAL)),
    ("diseases that affect my car", HORSE, dict(subjects=HORSE_SUBJECTS, **GARDEN_GOAL)),
    ("recall issues and parts for my car",
     "Langley Memoir on Mechanical Flight, Parts I and II",
     dict(subjects=["parts", "mechanical"], shelves=["vehicle", "mechanic"])),
    ("problems that affect tomato plants", "Lifehacks Q&A. Everyday problems",
     dict(subjects=["problems"], **GARDEN_GOAL)),
    ("what problems does my car have", "Lifehacks Q&A. Everyday problems",
     dict(subjects=["problems"], goal=["honda", "civic"], shelves=["vehicle"])),
])
def test_a_tied_word_never_fits_alone(request_words, text, kwargs):
    assert goal_words.match_strength(request_words, text, **kwargs)[0] == 0


@pytest.mark.parametrize("words,subjects", [
    ("tomato diseases", ["tomato"]),
    ("car parts and recalls", ["car", "recall"]),
    ("problems that affect tomato plants", ["affect", "tomato", "plant"]),
    ("what problems does my car have", ["car"]),
    # "that" is about the bot, so "music" stays whole:
    ("A music bot that knows bands and albums", ["music", "band", "album"]),
])
def test_subject_words(words, subjects):
    assert goal_words.subject_words(words) == subjects


def test_a_word_tied_to_what_it_names_still_fits():
    cats = ("Cats: Their Points and Characteristics. About: Cats -- Diseases")
    assert goal_words.fits_request("diseases that affect cats", cats,
                                   subjects=["Cats -- Diseases"])
    # A listed word is whole on its own:
    assert goal_words.fits_request("car parts and recalls", "NHTSA recalls")


@pytest.mark.parametrize("goal,ask,gone", [
    (GARDEN, "diseases that affect cats", ["Horse", "Farm Animals"]),
    (GARDEN, "diseases that affect my car", ["Horse", "Farm Animals"]),
    (CIVIC, "recall issues and parts for my car", ["Langley", "Wild Life"]),
    (GARDEN, "problems that affect tomato plants", ["Lifehacks", "Computational"]),
    (CIVIC, "what problems does my car have", ["Lifehacks"]),
])
def test_rephrasings_do_not_bring_back_off_topic_books(monkeypatch, goal, ask, gone):
    built(goal=goal)
    guard_network(monkeypatch)
    text = " | ".join(_labels(add_more.find(ask, via="terminal", client="t")))
    for word in gone:
        assert word not in text


def test_problems_that_affect_tomato_plants_still_finds_the_tomato_book(monkeypatch):
    built(goal=GARDEN)
    guard_network(monkeypatch)
    session = add_more.find("problems that affect tomato plants", via="terminal",
                            client="t")
    assert "Tomato Culture" in " | ".join(_labels(session, "knowledge"))


# -- the daily update's name -------------------------------------------------------

@pytest.mark.parametrize("goal,topic", [
    ("cross stitch tips", "cross stitch"),
    ("my Honda Civic", "Honda Civic"),
    ("I love dinosaurs, I want a bot that knows every dinosaur", "dinosaur"),
])
def test_the_update_keeps_a_typed_phrase_whole(goal, topic):
    assert offers.topic_phrase(goal, goal_words.goal_terms(goal)) == topic
    label = offers.proposal_items(goal, goal_words.goal_terms(goal))[0]["label"]
    assert label == f"A daily {topic} update at 8 am"
    assert plain_words.check(label) == []


# -- the wizard ----------------------------------------------------------------------

def test_the_wizard_keeps_the_knitting_books():
    labels = [i["label"] for i in knowledge_resolver.proposal_items(
        KNITTING, goal_words.goal_terms(KNITTING))]
    assert any(label.startswith("Handbook of Wool Knitting") for label in labels)
    assert any("Wikipedia articles about knitting" in label for label in labels)


def test_the_wizard_never_offers_a_one_word_match():
    goal = "cross stitch tips"
    labels = " | ".join(i["label"] for i in knowledge_resolver.proposal_items(
        goal, goal_words.goal_terms(goal)))
    assert "Cross Validated" not in labels
    rows = " | ".join(r["label"] for r in sources_plan.library_rows(goal))
    assert "Cross Validated" not in rows


def test_the_wizard_keeps_a_shelf_named_in_the_title():
    labels = " | ".join(i["label"] for i in knowledge_resolver.proposal_items(
        SPACE, goal_words.goal_terms(SPACE)))
    assert "Astronomy Stack Exchange" in labels
    assert "Space Exploration Stack Exchange" in labels


# -- the home page -------------------------------------------------------------------

def test_try_asking_needs_the_goals_phrase():
    rows = {"cross": [{"title": "Cross Validated", "relevance": 0.9,
                       "lead": "A site about statistics."},
                      {"title": "Cross-stitch", "relevance": 0.9,
                       "lead": "Cross-stitch is a form of sewing."}]}

    def hits(q):
        q = q.lower()
        if "cross validated" in q:
            return rows["cross"][:1]
        if "cross-stitch" in q:
            return rows["cross"][1:]
        return rows["cross"] if "cross" in q else []

    got = [q["text"] for q in front_page.book_questions("cross stitch patterns",
                                                       hits=hits)]
    assert got == ["Tell me about Cross-stitch"]


# -- the routines' library picker ---------------------------------------------------

class _Coll:
    def __init__(self, docs):
        self.docs = docs

    def get(self, **kw):
        docs = [d for d in self.docs
                if "where_document" not in kw
                or kw["where_document"]["$contains"].lower() in d[1].lower()]
        return {"documents": [d[1] for d in docs],
                "metadatas": [{"title": d[0], "chunk_index": 0,
                               "source_label": "Wikipedia"} for d in docs]}


def test_the_library_picker_skips_a_page_that_does_not_fit():
    corolla = ("Toyota Corolla", "The Toyota Corolla is a compact car, a rival "
                                 "of the Honda Civic line and others.")
    civic = ("Honda Civic", "The Honda Civic is a series of cars made by Honda.")
    only_corolla = _Coll([("Toyota Corolla", "The Toyota Corolla is a compact car "
                                             "made by Toyota.")])
    assert routines.pick_from_collection(only_corolla, ["Honda", "Civic"],
                                         request="my Honda Civic") is None
    got = routines.pick_from_collection(_Coll([corolla, civic]), ["Honda"],
                                        rng=random.Random(3),
                                        request="my Honda Civic")
    # The Corolla page names the Civic in its opening, so both may be read;
    # a page that names neither never is.
    assert got["title"] in ("Honda Civic", "Toyota Corolla")


def test_the_book_passage_must_fit_the_bot(monkeypatch):
    from puffpai.knowledge import book_search
    pages = [{"title": "Cross Validated", "text": "A site about statistics and data.",
              "library": "Q&A", "relevance": 0.9},
             {"title": "Cross-stitch", "text": "Cross-stitch is a form of sewing "
                                               "with X-shaped stitches.",
              "library": "Crafts", "relevance": 0.8}]
    monkeypatch.setattr(book_search, "book_hits", lambda *a, **k: list(pages))
    monkeypatch.setattr(book_search, "_any_article", lambda *a, **k: pages[0])
    for seed in range(5):
        got = book_search.pick_passage(["cross", "stitch"], rng=random.Random(seed),
                                       request="cross stitch patterns")
        assert got["title"] == "Cross-stitch"
    monkeypatch.setattr(book_search, "book_hits", lambda *a, **k: pages[:1])
    assert book_search.pick_passage(["cross"], request="cross stitch patterns") is None


def test_the_library_request_is_the_bots_goal():
    built(goal=CIVIC)
    assert routines.library_request(["Honda"]) == CIVIC
