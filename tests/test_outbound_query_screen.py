"""The outbound screen.

Nothing about the owner leaves unless it is a ``Sendable`` of ``[a-z0-9 ]``,
one to four words, made by ``outbound_query.screen``. One failing word drops
the whole phrase. The bot's subject is always sendable. These tests use the
12 tuning goals and the 334 phrases the local model wrote for them
(``search_phrase_screen.json``); nothing here touches the network.
"""
from __future__ import annotations

import copy
import dataclasses
import json
import os
import pickle
import urllib.parse

import pytest

from puffpai.security import outbound_query as oq

HERE = os.path.dirname(os.path.abspath(__file__))
GOALS = {g["id"]: g for g in json.load(open(
    os.path.join(HERE, "fixtures", "tuning_goals.json"), encoding="utf-8"))["goals"]}
V4 = json.load(open(os.path.join(HERE, "fixtures", "search_phrase_screen.json"),
                    encoding="utf-8"))["qwen2.5_7b-instruct"]
WEDDING = GOALS["wedding"]["text"]


def _sent(result):
    return [s.phrase for s in result.sendable]


def _held(result):
    return [h["phrase"] for h in result.held]


# -- rule 1: hard words drop the whole phrase -------------------------------------

@pytest.mark.parametrize("phrase,why", [
    ("sister wedding ideas", "relative"),
    ("wedding vendors near me", "pronoun"),
    ("our wedding budget", "pronoun"),
    ("june wedding ideas", "date"),
    ("saturday wedding checklist", "date"),
    ("wedding for 150 guests", "number"),
    ("2026 wedding trends", "number"),
    ("wedding for five", "number"),
    # "married" and "getting" are
    # ordinary words now; a sensitive news word still drops the phrase.
    ("divorced couple checklist", "private"),
    ("widowed parent checklist", "private"),
    ("10yo party ideas", "number"),
])
def test_each_hard_class_drops_the_whole_phrase(phrase, why):
    got = oq.screen([phrase, "wedding ideas"], goal=WEDDING)
    assert _sent(got) == ["wedding ideas"]      # never a half-cut phrase
    assert _held(got) == [phrase]
    assert why in [w["why"] for w in got.held[0]["words"]]


def test_a_relative_the_goal_names_is_hard_even_as_audience():
    goal = "Minecraft server helper bot for my kids"
    assert _sent(oq.screen(["kids minecraft mods"], goal=goal)) == []
    # Without "my", kids is who a bot is for, not her family.
    assert _sent(oq.screen(["kids minecraft mods"], goal="a Minecraft server bot")) == \
        ["kids minecraft mods"]


# -- rule 2 and 3: held words pass only when she ticks them --------------------------

def test_held_words_pass_only_with_allowed_words():
    bakery = GOALS["bakery"]["text"]
    assert _sent(oq.screen(["instagram scheduling"], goal=bakery)) == []
    assert _sent(oq.screen(["instagram scheduling"], goal=bakery,
                           allowed_words=["Instagram"])) == ["instagram scheduling"]
    fitness = GOALS["fitness"]["text"]
    assert _sent(oq.screen(["dana running plan"], goal=fitness)) == []
    assert _sent(oq.screen(["dana running plan"], goal=fitness,
                           allowed_words=["dana"])) == ["dana running plan"]
    # A proper noun she never typed is held by the dictionary (rule 3).
    assert _sent(oq.screen(["christmas budget"], goal=WEDDING)) == []
    assert _sent(oq.screen(["christmas budget"], goal=WEDDING,
                           allowed_words=["christmas"])) == ["christmas budget"]
    # Languages and acronyms are not names.
    assert _sent(oq.screen(["spanish flashcards", "gps tracker"], goal=WEDDING)) == \
        ["spanish flashcards", "gps tracker"]


def test_home_town_region_and_health_can_never_be_ticked():
    home = {"town": "Tucson", "state": "Arizona"}
    garden = GOALS["garden"]["text"]
    got = oq.screen(["tucson frost dates", "arizona planting", "southwest garden plants",
                     "gluten free recipes", "raised bed plans"],
                    goal=garden, home=home,
                    allowed_words=["tucson", "arizona", "southwest", "gluten", "free"])
    assert _sent(got) == ["raised bed plans"]


def test_a_home_town_word_is_held_even_in_the_subject_position():
    home = {"town": "Portland", "state": "Oregon"}
    goal = "a Portland bakery bot"
    got = oq.screen(["portland bakery", "bakery", "bakery recipes"], goal=goal, home=home)
    assert got.subject == "bakery"
    assert _held(got) == ["portland bakery"]
    assert _sent(got) == ["bakery", "bakery recipes"]
    assert oq.subject_sendable(goal, home=home).phrase == "bakery"
    # A multi-word home town is held as a run of its words.
    got = oq.screen(["salt lake weather", "lake fishing spots"], goal="a fishing bot",
                    home={"town": "Salt Lake City", "state": "Utah"})
    assert _sent(got) == ["lake fishing spots"]


# -- the subject ------------------------------------------------------------------

@pytest.mark.parametrize("goal,subject,notes", [
    ("Minecraft server helper bot", "minecraft server", ["brand"]),
    ("a Home Assistant dashboard helper bot", "home assistant dashboard", []),
    ("a 3D printer helper", "3d printer", []),
    ("a vegan recipe bot", "vegan recipe", ["health"]),
    ("a pregnancy tracker bot", "pregnancy tracker", ["health"]),
    ("knitting patterns bot", "knitting patterns", []),
])
def test_subject_phrases_are_sendable_for_the_held_out_kinds(goal, subject, notes):
    s = oq.subject_sendable(goal)
    assert oq.is_sendable(s)
    assert (s.phrase, s.subject, s.source_hint) == (subject, True, "subject")
    got = oq.screen([subject], goal=goal, source_hint="j1")
    assert _sent(got) == [subject]
    assert got.sendable[0].subject is True and got.sendable[0].source_hint == "subject"
    assert got.subject == subject and got.subject_notes == notes


def test_subject_words_are_exempt_from_names_and_health_only():
    assert _sent(oq.screen(["minecraft server backup"], goal="Minecraft server helper bot")) \
        == ["minecraft server backup"]
    assert _sent(oq.screen(["home assistant themes"],
                           goal="a Home Assistant dashboard helper bot")) \
        == ["home assistant themes"]
    assert _sent(oq.screen(["vegan meal planner", "pregnancy week by week"],
                           goal="a vegan recipe bot")) == ["vegan meal planner"]
    assert _sent(oq.screen(["portland bakery recipes"], goal="a Portland bakery bot")) == \
        ["portland bakery recipes"]
    # ...but never from the hard words.
    assert _sent(oq.screen(["3d printer for 2 kids"], goal="a 3D printer helper")) == []
    # A digit-letter word leaves only as a subject word ("a1c"), except
    # "2d"/"3d"/"4d", kinds of things, never a number about her (without
    # this, "Ender 3 printer" holds every "3d" phrase).
    assert _sent(oq.screen(["a1c tracker"], goal=WEDDING)) == []
    assert _sent(oq.screen(["3d printer"], goal=WEDDING)) == ["3d printer"]


def test_no_subject_means_no_subject_sendable():
    assert oq.subject_sendable(GOALS["dnd"]["text"]) is None
    assert oq.screen(["dice roller"], goal=GOALS["dnd"]["text"]).subject == ""


def test_the_personal_detail_scan_applies_to_the_subject_too(monkeypatch):
    from puffpai.security import data_classifier

    class Flagged:
        has_pii = True

    monkeypatch.setattr(data_classifier.DataClassifier, "scan_for_pii",
                        lambda self, text, **kw: Flagged())
    assert oq.subject_sendable("a wedding planner bot") is None
    got = oq.screen(["wedding planner"], goal="a wedding planner bot")
    assert _sent(got) == [] and got.held[0]["words"][-1]["why"] == "private detail"


def test_a_check_that_cannot_run_holds_everything(monkeypatch):
    from puffpai.catalogs import personal_words

    def boom(*a, **k):
        raise RuntimeError("no")

    monkeypatch.setattr(personal_words, "find", boom)
    got = oq.screen(["wedding ideas"], goal=WEDDING)
    assert got.sendable == [] and _held(got) == ["wedding ideas"]
    assert oq.subject_sendable(WEDDING) is None


# -- shape -----------------------------------------------------------------------

@pytest.mark.parametrize("phrase", [
    "repo:foo wedding", 'wedding"', "user:x", "wedding in:readme", "stars:>5 wedding",
    "wedding planner app for couples", "", "   ", "wedding\tideas", "wedding\nideas",
    "gluten-free", "café menu", "Kitchen ideas", "wedding ideas!",
    "a" * 25, None, 42, b"wedding",
])
def test_anything_but_plain_words_is_not_sendable(phrase):
    got = oq.screen([phrase], goal=WEDDING)
    assert got.sendable == []
    assert len(got.held) == 1


def test_case_and_spaces_are_tidied_and_duplicates_come_back_once():
    got = oq.screen(["Wedding  Ideas", "wedding ideas", " wedding ideas "], goal=WEDDING,
                    source_hint="j3")
    assert [(s.phrase, s.source_hint, s.subject) for s in got.sendable] == \
        [("wedding ideas", "j3", False)]


# -- Sendable can only be made here ----------------------------------------------------

def test_sendable_cannot_be_made_outside_the_module():
    with pytest.raises(TypeError):
        oq.Sendable("wedding ideas")
    with pytest.raises(TypeError):
        oq.Sendable(phrase="wedding ideas", source_hint="j1", subject=True)
    real = oq.screen(["wedding ideas"], goal=WEDDING).sendable[0]
    with pytest.raises(TypeError):
        dataclasses.replace(real, phrase="my sister")
    forged = object.__new__(oq.Sendable)
    object.__setattr__(forged, "phrase", "my sister")
    object.__setattr__(forged, "source_hint", "")
    object.__setattr__(forged, "subject", False)
    assert not oq.is_sendable(forged)
    with pytest.raises(TypeError):
        oq.url_quote(forged)
    with pytest.raises(TypeError):
        pickle.dumps(real)
    assert copy.copy(real) is real and copy.deepcopy(real) is real
    assert oq.is_sendable(copy.deepcopy({"x": [real]})["x"][0])


def test_url_quote_refuses_anything_not_screened():
    for bad in ("wedding ideas", None, {"phrase": "wedding ideas"}, b"wedding"):
        with pytest.raises(TypeError):
            oq.url_quote(bad)
    s = oq.screen(["wedding ideas"], goal=WEDDING).sendable[0]
    assert oq.url_quote(s) == urllib.parse.quote_plus("wedding ideas") == "wedding+ideas"
    assert oq.is_sendable(s) and not oq.is_sendable("wedding ideas")


# -- the 334 phrases the local model wrote for the 12 goals ----------------------------

TRUE_PERSONAL = [
    ("garden", "tucson weather"), ("garden", "tucson climate"),
    ("garden", "tucson gardening guide"), ("garden", "tucson gardening schedule"),
    ("garden", "tucson gardeners"), ("space", "dayton weather"),
    ("pet", "son reminder"),
]


def _v4_rows():
    return [(r[0], r[1]) for r in V4["list"]] + [(r[0], r[1]) for r in V4["passed"]]


def test_the_recorded_model_phrases_hold_every_true_place_and_relative():
    rows = _v4_rows()
    assert len(rows) == V4["total"] == 334
    held = []
    for gid, phrase in rows:
        got = oq.screen([phrase], goal=GOALS[gid]["text"])
        if got.held:
            held.append((gid, phrase))
        else:
            assert oq.is_sendable(got.sendable[0])
    assert len(held) >= 15
    for row in TRUE_PERSONAL:
        assert row in held, row
    assert sum(1 for row in held if row == ("pet", "son reminder")) == 2
    assert len([r for r in held if r[1].startswith("tucson ")]) == 5


def test_regions_and_diet_words_are_held_when_they_are_not_the_subject():
    rows = _v4_rows()
    regional = [(g, p) for g, p in rows if p.startswith("southwest ")]
    diet = [(g, p) for g, p in rows if p.startswith("gluten free ")]
    assert regional and diet
    for gid, phrase in regional + diet:
        goal = GOALS[gid]["text"]
        got = oq.screen([phrase], goal=goal)
        assert "southwest" not in got.subject and "gluten" not in got.subject
        assert got.sendable == [], phrase


def test_every_sendable_phrase_is_plain_and_short():
    for gid, phrase in _v4_rows():
        for s in oq.screen([phrase], goal=GOALS[gid]["text"]).sendable:
            assert oq.PHRASE_RE.fullmatch(s.phrase)
            assert 1 <= len(s.phrase.split()) <= 4


# -- more shapes -----------------------------------------------

@pytest.mark.parametrize("phrase,goal", [
    ("grace chess", "a chess tutor for my daughter Grace"),
    ("max soccer", "My son Max wants a soccer coach bot"),
    ("rose garden", "a garden bot for my wife Rose"),
    ("jaxon soccer", "my son jaxon wants a soccer coach bot"),
    ("jaxon soccer", "my son Jaxon wants a soccer coach bot"),
    ("grace chess", "a chess tutor for my daughter grace"),
    ("biscuit food", "a feeding bot for my dog Biscuit"),
])
def test_a_relatives_or_pets_name_is_never_sent(phrase, goal):
    got = oq.screen([phrase], goal=goal)
    assert _sent(got) == []
    assert _held(got) == [phrase]


def test_a_relatives_name_passes_only_when_she_ticks_it():
    goal = "a chess tutor for my daughter Grace"
    assert _sent(oq.screen(["grace chess"], goal=goal)) == []
    assert _sent(oq.screen(["grace chess"], goal=goal, allowed_words=["grace"])) ==         ["grace chess"]
    # The subject still goes out on its own.
    assert oq.subject_sendable(goal).phrase == "chess"


def test_a_changed_sendable_is_refused():
    goal = "a wedding planner bot"
    for field_name, value in (("phrase", "my sister tucson"), ("source_hint", "x"),
                              ("subject", True)):
        s = oq.screen(["wedding ideas"], goal=goal, source_hint="j1").sendable[0]
        assert oq.is_sendable(s)
        object.__setattr__(s, field_name, value)
        assert not oq.is_sendable(s), field_name
        with pytest.raises(TypeError):
            oq.url_quote(s)


def test_a_sendable_with_a_str_subclass_phrase_is_refused():
    class Sly(str):
        def __eq__(self, other):
            return True
        __hash__ = str.__hash__

    s = oq.screen(["wedding ideas"], goal="a wedding planner bot").sendable[0]
    object.__setattr__(s, "phrase", Sly("wedding ideas"))
    assert not oq.is_sendable(s)


# -- more shapes, continued -----------------------------------------------

@pytest.mark.parametrize("phrase,name,goal", [
    # After one of her people, however it is written.
    ("rose garden", "rose", "a garden bot for my step-daughter Rose"),
    ("rose garden", "rose", "a garden bot for my little girl Rose"),
    ("grace chess", "grace", "a chess tutor bot for my 8-year-old Grace"),
    ("grace chess", "grace", "a chess tutor bot for my 8 year old Grace"),
    ("will chess", "will", "a chess bot for my son Will"),
    ("hope garden", "hope", "a garden bot for my daughter Hope"),
    ("max chess", "max", "a chess tutor bot for my kids grace and max"),
    ("grace chess", "grace", "a chess tutor bot for my kids grace and max"),
    ("biscuit food", "biscuit", "a feeding bot for my dog biscuit"),
    ("biscuit food", "biscuit", "a feeding bot for our puppy Biscuit"),
    ("rose garden", "rose", "a garden bot for my aunt rose"),
    # Given outright.
    ("grace chess", "grace", "a chess tutor bot for my daughter. Her name is Grace."),
    ("grace chess", "grace", "a chess tutor bot for my daughter, whose name is Grace"),
    # Right before one of her people.
    ("grace chess", "grace", "a chess tutor bot for Grace, my daughter"),
    ("grace chess", "grace", "Grace is my daughter and she wants a chess tutor bot"),
    ("rose garden", "rose", "Rose, my wife, wants a garden bot"),
    ("biscuit food", "biscuit", "a feeding bot for Biscuit, my dog"),
    ("max chess", "max", "Grace and Max, my kids, want a chess tutor bot"),
    # A capitalised word mid-sentence with no other cue.
    ("max soccer", "max", "a soccer coach bot for Max"),
])
def test_names_in_everyday_phrasings_are_never_sent(phrase, name, goal):
    from puffpai.catalogs import goal_words, personal_words
    got = oq.screen([phrase], goal=goal)
    assert _sent(got) == [], goal
    assert _held(got) == [phrase]
    assert name in {h["word"] for h in personal_words.find(goal).held}
    assert name not in goal_words.search_terms(goal)
    # Her own tick still lets it out (rule 2), and the subject is never held.
    assert _sent(oq.screen([phrase], goal=goal, allowed_words=[name])) == [phrase]
    assert oq.subject_sendable(goal) is not None


@pytest.mark.parametrize("home", [
    {"town": "Columbus", "state": "OH"},
    {"town": "Columbus, OH"},
    {"town": "Columbus", "state": "Ohio"},
    {"label": "Columbus, OH"},
])
def test_her_home_state_is_held_whichever_way_it_is_written(home):
    got = oq.screen(["ohio garden", "columbus garden", "garden tools"],
                    goal="a garden bot", home=home)
    assert _sent(got) == ["garden tools"]
    assert oq.subject_sendable("an Ohio garden bot", home=home).phrase == "garden"
    # Never tickable.
    assert _sent(oq.screen(["ohio garden"], goal="a garden bot", home=home,
                           allowed_words=["ohio"])) == []


def test_a_state_code_that_is_not_a_word_is_held_too():
    home = {"town": "Austin", "state": "Texas"}
    assert _sent(oq.screen(["tx garden", "texas garden"], goal="a garden bot",
                           home=home)) == []
    # "OR" is a joining word, so it is not held on its own; Oregon is.
    home = {"label": "Portland, OR"}
    got = oq.screen(["oregon garden", "garden or yard"], goal="a garden bot", home=home)
    assert _sent(got) == ["garden or yard"]


@pytest.mark.parametrize("phrase", ["alzheimer support", "crohn diet",
                                    "parkinson exercise", "alzheimers care"])
def test_a_condition_named_after_a_person_is_a_health_word(phrase):
    assert _sent(oq.screen([phrase], goal="a garden bot")) == []
