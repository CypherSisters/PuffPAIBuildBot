"""The bot's subject, one pinned algorithm.

The subject is what every outside search starts from ("wedding planner",
"minecraft server"). It must never carry a personal word: a relative, a
name, a number, a date or a place the goal marks. Every example below is
pinned and tested exactly.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.catalogs import goal_words, personal_words

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS = json.load(open(os.path.join(HERE, "fixtures", "tuning_goals.json"),
                       encoding="utf-8"))["goals"]

WEDDING = ("I want to build a wedding planner bot. My brother just got engaged and "
           "there is a lot to do and plan. I'll need to be able to track themes from "
           "pictures, come up with ideas, find wedding vendors and materials to rent, "
           "plan the budget, and do a whole wedding plan.")


@pytest.mark.parametrize("goal,subject,notes", [
    (WEDDING, "wedding planner", []),
    ("Minecraft server helper bot for my kids", "minecraft server", ["brand"]),
    ("a Home Assistant dashboard helper bot", "home assistant dashboard", []),
    ("a 3D printer helper", "3d printer", []),
    ("a vegan recipe bot", "vegan recipe", ["health"]),
    ("a pregnancy tracker bot", "pregnancy tracker", ["health"]),
    ("knitting patterns bot", "knitting patterns", []),
    ("my sister's wedding bot", "wedding", []),
])
def test_required_examples(goal, subject, notes):
    assert personal_words.subject_of(goal) == subject
    found = personal_words.find(goal)
    assert found.subject == subject
    assert found.subject_notes == notes


def test_a_town_the_goal_marks_as_a_place_is_not_the_subject():
    goal = "We live in Portland. I want a Portland bakery bot"
    assert personal_words.subject_of(goal) == "bakery"
    assert personal_words.find(goal).subject == "bakery"


def test_her_home_town_is_not_the_subject():
    found = personal_words.find("a Portland bakery bot",
                                home={"town": "Portland", "state": "Oregon"})
    assert found.subject == "bakery"
    assert found.subject_notes == []
    assert {"word": "portland", "why": "place", "can_allow": False} in found.held


def test_a_town_that_is_not_marked_stays_and_is_noted_as_a_name():
    found = personal_words.find("a Portland bakery bot")
    assert found.subject == "portland bakery"
    assert found.subject_notes == ["brand"]


def test_ampersand_becomes_and_and_the_subject_keeps_at_most_four_words():
    assert personal_words.subject_of("a Dungeons & Dragons helper bot") == \
        "dungeons and dragons"
    assert len(personal_words.subject_of(
        "I want a big red shiny fast racing car game bot").split()) <= 4


def test_last_bot_noun_of_the_first_sentence_with_one():
    # "assistant" is a bot noun too, but "bot" is the last one.
    assert personal_words.subject_of("A Home Assistant dashboard bot") == \
        "home assistant dashboard"
    # The first sentence without a bot noun is skipped.
    assert personal_words.subject_of("We love the outdoors. I want a hiking app.") == "hiking"


@pytest.mark.parametrize("goal", [
    "I want a bot that helps me",           # nothing between "a" and "bot"
    "Please make an app.",
    "My 10 year old son wants a bot",
])
def test_no_subject_gives_empty(goal):
    assert personal_words.subject_of(goal) == ""


@pytest.mark.parametrize("goal,unwanted", [
    ("My 12 year old wants a 2026 March coding tutor", {"12", "2026", "march"}),
    ("a dog named Biscuit care bot", set()),
    ("I'm Leo and I want Leo's space bot", {"leo"}),
    ("a sister-in-law gift helper bot", {"sister", "sisterinlaw", "law"}),
])
def test_personal_words_never_reach_the_subject(goal, unwanted):
    subject = personal_words.subject_of(goal)
    assert not (set(subject.split()) & unwanted), subject
    for word in subject.split():
        assert not personal_words.is_hard_word(word) or any(c.isdigit() for c in word)


@pytest.mark.parametrize("g", GOALS, ids=[g["id"] for g in GOALS])
def test_the_subject_of_each_tuning_goal_has_no_personal_word(g):
    personal = set()
    for entry in g["personal"]:
        for w in goal_words.raw_words(entry):
            personal |= {w, goal_words.singular(w)}
    subject = personal_words.find(g["text"]).subject
    words = set()
    for w in subject.split():
        words |= {w, goal_words.singular(w)}
    assert not (words & personal), (g["id"], subject)


def test_find_sorts_hard_and_held_words():
    found = personal_words.find(WEDDING)
    assert {"brother", "my"} <= found.hard
    # Ordinary news words are not hard.
    assert not ({"engaged", "got"} & found.hard)
    # A relative's news is held for good and listed; "got" is never held.
    assert found.held == [{"word": "engaged", "why": "private", "can_allow": False}]
    bakery = next(g for g in GOALS if g["id"] == "bakery")["text"]
    held = {h["word"]: h for h in personal_words.find(bakery).held}
    assert held["sweet"]["why"] == "name" and held["sweet"]["can_allow"] is True
    assert held["portland"]["why"] == "place"
    meals = next(g for g in GOALS if g["id"] == "meals")["text"]
    health = {h["word"] for h in personal_words.find(meals).held
              if h["why"] == "health"}
    assert {"gluten", "peanut", "allergy", "free"} <= health
    assert all(not h["can_allow"] for h in personal_words.find(meals).held
               if h["why"] == "health")


def test_the_word_list_is_read_once_and_knows_names_from_words(monkeypatch):
    assert personal_words.dict_kind("Tucson") == "proper"
    assert personal_words.dict_kind("garden") == "common"
    assert personal_words.dict_kind("API") == "common"       # an acronym, not a name
    assert personal_words.dict_kind("minecraft") == "unknown"
    calls = []
    monkeypatch.setattr(personal_words, "_load_dict",
                        lambda: calls.append(1) or {})
    personal_words.dict_kind("rose")
    assert calls == []   # already loaded: never read twice


def test_a_missing_word_list_fails_closed(monkeypatch):
    monkeypatch.setattr(personal_words, "_DICT", None)
    monkeypatch.setattr(personal_words, "_dict_path", lambda: os.path.join(HERE, "nope.dic"))
    try:
        assert personal_words.dict_kind("Tucson") == "unknown"
        # Unknown capitalised words are still held (as brands), never let out.
        held = {h["word"] for h in personal_words.find(
            "a garden bot for my yard near Gilbert and Mesa").held}
        assert {"gilbert", "mesa"} <= held
    finally:
        monkeypatch.setattr(personal_words, "_DICT", None)


# -- more cases ---------------------------------------------------------------------

@pytest.mark.parametrize("goal,subject", [
    # The pinned window stops only after a/an/my/the/build/make/want/need;
    # joining words inside it are filler, so the head noun is kept.
    ("a speech to text app", "speech text"),
    ("a text to speech app", "text speech"),
    ("a gifts for teachers bot", "gifts teachers"),
    ("a guitar for beginners tutor", "guitar beginners"),
    ("a photo to cartoon bot", "photo cartoon"),
    ("a trip to the moon bot", "moon"),
])
def test_the_window_is_the_pinned_one(goal, subject):
    assert personal_words.subject_of(goal) == subject


@pytest.mark.parametrize("goal,subject", [
    ("a pay as you go budget bot", "as go budget"),
    ("a paint by numbers helper bot", "paint by numbers"),
])
def test_ordinary_words_are_not_noted_as_brands(goal, subject):
    found = personal_words.find(goal)
    assert found.subject == subject
    assert found.subject_notes == []


def test_the_word_list_knows_word_forms_and_function_words():
    for word in ("as", "numbers", "ideas", "batteries", "baked", "baking", "planning"):
        assert personal_words.dict_kind(word) == "common", word
    for word in ("Tucson", "christmas", "Dayton"):
        assert personal_words.dict_kind(word) == "proper", word


@pytest.mark.parametrize("goal,name", [
    ("a chess tutor for my daughter Grace", "grace"),
    ("My son Max wants a soccer coach bot", "max"),
    ("a garden bot for my wife Rose", "rose"),
    ("my son jaxon wants a soccer coach bot", "jaxon"),
    ("my son Jaxon wants a soccer coach bot", "jaxon"),
    ("a reading app for my kid Emma", "emma"),
])
def test_a_relatives_name_is_held(goal, name):
    found = personal_words.find(goal)
    row = next(h for h in found.held if h["word"] == name)
    assert row == {"word": name, "why": "name", "can_allow": True}
    assert name not in found.subject.split()


def test_more_than_one_relatives_name_is_held():
    held = {h["word"] for h in personal_words.find(
        "a homework bot for my daughters Grace and Rose Anne").held}
    assert {"grace", "rose", "anne"} <= held


def test_a_relatives_name_never_reaches_the_subject():
    assert personal_words.subject_of("My daughter Grace chess tutor bot") == "chess tutor"
    assert personal_words.subject_of("My son Max wants a soccer coach bot") ==         "soccer coach"
    # A lower-case ordinary word after a relative may just be the subject.
    assert personal_words.subject_of("my son soccer coach bot") == "soccer coach"
    # A relative's own thing is not a name.
    found = personal_words.find("a bot for my son's Minecraft server")
    assert "minecraft" not in {h["word"] for h in found.held if h["why"] == "name"}


def test_a_pets_name_is_held():
    held = {h["word"]: h for h in personal_words.find(
        "a feeding bot for my dog Biscuit").held}
    assert held["biscuit"]["why"] == "name"


# -- more cases, continued ----------------------------------------------------------

@pytest.mark.parametrize("goal,subject", [
    ("an Alzheimer's care bot", "alzheimer care"),
    ("a Parkinson's exercise bot", "parkinson exercise"),
    ("a Crohn's diet bot", "crohn diet"),
    ("a parkinsons exercise bot", "parkinsons exercise"),
])
def test_a_health_subject_carries_the_health_note_not_brand(goal, subject):
    from puffpai.security import outbound_query
    found = personal_words.find(goal)
    assert found.subject == subject
    assert found.subject_notes == ["health"]
    assert outbound_query.subject_sendable(goal).phrase == subject
    # The "s" of "Alzheimer's" is not a held word of its own.
    assert all(len(h["word"]) > 1 for h in found.held)


def test_a_capitalised_word_mid_sentence_is_held_unless_it_names_the_bot():
    assert "max" in {h["word"] for h in personal_words.find(
        "a soccer coach bot for Max").held}
    found = personal_words.find("a Soccer coach bot")
    assert found.subject == "soccer coach" and found.held == []
    # A title joined by "&" is a product, not a person.
    dnd = next(g["text"] for g in GOALS if "Dungeons" in g["text"])
    held = {h["word"] for h in personal_words.find(dnd).held}
    assert not held & {"dungeons", "dragons"}


def test_a_word_before_her_person_needs_a_capital_a_comma_or_is():
    # "Help" starts the sentence, and "reminds" is a lower-case word with no
    # comma: neither is taken for a name.
    assert personal_words.find("Help my daughter learn chess with a tutor bot").held == []
    pets = next(g["text"] for g in GOALS if "Biscuit" in g["text"])
    assert "reminds" not in {h["word"] for h in personal_words.find(pets).held}
    assert "rose" in {h["word"] for h in personal_words.find(
        "a garden bot for rose, my wife").held}


def test_a_name_list_keeps_going_but_stops_at_other_words():
    held = {h["word"] for h in personal_words.find(
        "a chess tutor bot for my kids Grace, Max and Leo").held}
    assert {"grace", "max", "leo"} <= held
    # "and her" ends the list; the subject is untouched.
    found = personal_words.find("a chess tutor bot for my daughter Grace and her friends")
    assert found.subject == "chess tutor"
    assert "friends" not in {h["word"] for h in found.held}
