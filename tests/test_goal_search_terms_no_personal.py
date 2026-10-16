"""Personal words are never search words.

An earlier version searched Wikipedia for "wedding, planner, sister and getting".
``goal_words.search_terms`` is the goal's words with everything personal
taken out. (The Wikipedia book's own id, label and sends are checked in
this file too, since the book is routed through the subject.)
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.catalogs import goal_words

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS = json.load(open(os.path.join(HERE, "fixtures", "tuning_goals.json"),
                       encoding="utf-8"))["goals"]


def _personal(g) -> set[str]:
    out: set[str] = set()
    for entry in g["personal"]:
        for w in goal_words.raw_words(entry):
            out |= {w, goal_words.singular(w)}
    return out


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_search_terms_hold_no_personal_word(g):
    terms = goal_words.search_terms(g["text"])
    forms = set()
    for t in terms:
        forms |= {t, goal_words.singular(t)}
    assert not (forms & _personal(g)), (g["id"], sorted(forms & _personal(g)))
    assert terms, g["id"]
    assert len(terms) <= goal_words.MAX_TERMS


def test_the_round_two_wedding_words_are_gone_and_the_subject_stays():
    wedding = next(g for g in GOALS if g["id"] == "wedding")["text"]
    terms = goal_words.search_terms(wedding)
    assert terms[:2] == ["wedding", "planner"]
    for word in ("brother", "got", "engaged", "able", "plan", "whole", "come"):
        assert word not in terms
    assert "vendor" in terms and "budget" in terms


def test_plan_stays_when_it_names_the_bot():
    assert "plan" in goal_words.search_terms("a floor plan bot for my house remodel")
    assert "plan" not in goal_words.search_terms("a garden bot to plan my beds")


def test_a_health_word_that_names_the_bot_stays_and_others_go():
    assert goal_words.search_terms("a vegan recipe bot")[:2] == ["vegan", "recipe"]
    meals = next(g for g in GOALS if g["id"] == "meals")["text"]
    terms = goal_words.search_terms(meals)
    assert not ({"gluten", "free", "peanut", "allergy"} & set(terms))


def test_limit_is_respected():
    wedding = next(g for g in GOALS if g["id"] == "wedding")["text"]
    assert len(goal_words.search_terms(wedding, limit=3)) == 3


@pytest.mark.parametrize("word,single", [
    ("christmas", "christmas"), ("atlas", "atlas"), ("glass", "glass"),
    ("cactus", "cactus"), ("tennis", "tennis"),
    ("dogs", "dog"), ("batteries", "battery"), ("recalls", "recall"),
])
def test_singular_leaves_words_that_only_look_plural(word, single):
    assert goal_words.singular(word) == single


# -- more cases -----------------------------------------------------------------

@pytest.mark.parametrize("goal", [
    "a 10-year-old's homework helper",
    "a homework helper for my 10-year-old",
    "a gift helper for my sister-in-law",
])
def test_the_pieces_of_a_hard_word_are_not_search_words(goal):
    terms = goal_words.search_terms(goal)
    assert not ({"year", "old", "law", "sister"} & set(terms)), terms
    assert terms and terms[0] in ("homework", "gift")


def test_a_word_used_on_its_own_still_counts_next_to_an_age():
    # Only the pieces of the age go; "year" typed on its own stays.
    terms = goal_words.search_terms("a school year planner bot for my 10-year-old")
    assert "year" in terms and "old" not in terms


@pytest.mark.parametrize("word,single", [
    ("ideas", "idea"), ("areas", "area"), ("pizzas", "pizza"), ("bananas", "banana"),
    ("christmas", "christmas"), ("atlas", "atlas"), ("canvas", "canvas"),
    ("gas", "gas"),
])
def test_an_as_word_folds_only_when_the_word_list_knows_it_without_the_s(word, single):
    assert goal_words.singular(word) == single


def test_with_no_word_list_an_as_word_never_folds(monkeypatch):
    from puffpai.catalogs import personal_words
    monkeypatch.setattr(personal_words, "_DICT", {})
    assert goal_words.singular("ideas") == "ideas"
    assert goal_words.singular("christmas") == "christmas"
    assert goal_words.singular("dogs") == "dog"


# -- the Wikipedia book and the other extractors ----------------------------------

def _all_personal() -> dict[str, set[str]]:
    return {g["id"]: _personal(g) for g in GOALS}


def _book_lines(goal: str) -> list[dict]:
    from puffpai.catalogs import knowledge_resolver
    from puffpai.wizard import factory
    return [it for it in knowledge_resolver.proposal_items(goal, factory._knowledge_terms(goal))
            if ":wikipedia:" in it["id"] or it["id"].startswith("wikipedia:")]


def _words_of(*texts) -> set[str]:
    out: set[str] = set()
    for t in texts:
        for w in goal_words.raw_words(str(t or "")):
            out |= {w, goal_words.singular(w)}
    return out


def test_the_wedding_book_is_named_from_the_subject_only():
    from puffpai.catalogs import wikipedia_topics as wt
    wedding = next(g for g in GOALS if g["id"] == "wedding")
    lines = _book_lines(wedding["text"])
    assert lines, "the book line is still offered here (L5 decides when)"
    for line in lines:
        assert line["id"].endswith(":wedding-planner"), line["id"]
        assert "about wedding planner" in line["label"]
    cands = wt.candidates_for(wedding["text"])
    assert [c["sends"] for c in cands] == [["wedding", "planner"]] * 2
    meta = wt.book_meta(cands[0]["id"])
    assert meta["title"] == cands[0]["name"]                  # title equals the plan label
    assert meta["title"] == "Wikipedia articles about wedding planner"


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_no_book_id_label_or_send_holds_a_personal_word(g):
    from puffpai.catalogs import wikipedia_topics as wt
    personal = _personal(g)
    for line in _book_lines(g["text"]):
        assert not (_words_of(line["id"].replace("-", " "), line["label"]) & personal), \
            (g["id"], line["id"], line["label"])
    for cand in wt.candidates_for(g["text"]):
        assert not (_words_of(*cand["sends"]) & personal), (g["id"], cand["sends"])


class _RecordingWiki:
    """A fake Wikipedia: records every address, answers with nothing."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url, **kwargs):
        self.calls.append(url)
        return json.dumps({"query": {"search": [], "pages": [],
                                     "categorymembers": []}}).encode(), "ok"


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_a_fake_wikipedia_sees_no_personal_word(g):
    import urllib.parse
    from puffpai.catalogs import wikipedia_topics as wt
    cands = wt.candidates_for(g["text"])
    if not cands:
        return
    fake = _RecordingWiki()
    try:
        wt.discover(cands[0]["id"], getter=fake, sleeper=lambda s: None)
    except wt.WikipediaError:
        pass
    assert fake.calls
    sent = _words_of(*(urllib.parse.unquote_plus(u) for u in fake.calls))
    assert not (sent & _personal(g)), (g["id"], sorted(sent & _personal(g)))


def test_a_subject_the_screen_holds_gives_no_book(monkeypatch):
    from puffpai.catalogs import wikipedia_topics as wt
    from puffpai.security import outbound_query
    # A place the goal marks never names the book; a brand-like word does.
    assert wt.topic_words("a bakery bot in Portland") == ["bakery"]
    assert wt.topic_words("a Portland bakery bot") == ["portland", "bakery"]
    # Fail closed: when the screen holds the subject, no book is made at all.
    monkeypatch.setattr(outbound_query, "subject_sendable", lambda goal, home=None: None)
    assert wt.topic_words("a wedding planner bot") == []
    assert wt.candidates_for("a wedding planner bot") == []


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_offers_and_router_keywords_hold_no_personal_word(g):
    from puffpai import offers, persona_scaffold
    personal = _personal(g)
    assert not (_words_of(*offers.goal_terms(g["text"])) & personal), g["id"]
    assert not (_words_of(*persona_scaffold.goal_keywords(g["text"])) & personal), g["id"]


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_live_and_knowledge_terms_hold_no_personal_word(g):
    from puffpai.catalogs import live
    from puffpai.wizard import factory
    personal = _personal(g)
    assert not (_words_of(*live.terms_from_goal(g["text"])) & personal), g["id"]
    assert not (_words_of(*factory._knowledge_terms(g["text"])) & personal), g["id"]
