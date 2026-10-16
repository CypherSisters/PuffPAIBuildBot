"""Sensitive words with an everyday meaning, and over-holding.

* sensitive words with an everyday meaning too ("stroke", "cutting",
  "cheating", "spectrum") left in goals about her own people, and so did
  "after his X" and "my teen who has been X" (n1, n2);
* ordinary job words were still listed ("bake", "low"), and a private story
  with no "<kind> bot" searched nothing, not even its job (n5);
* a list typed one thing a line, a list with slashes, and "Ring Pop" candy
  (n4);
* "clouds no" named the clouds (n3).

Every probe is offline: the goal reader, the outbound screen and the device
lines read files on this computer, and nothing is sent anywhere.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import ast
import inspect
import re

import pytest

from puffpai.catalogs import goal_words as gw
from puffpai.catalogs import live
from puffpai.catalogs import personal_words as pw
from puffpai.looks import find as looks_find
from puffpai.security import outbound_query as oq


def _forms(words) -> set[str]:
    out: set[str] = set()
    for w in words:
        out |= {w, gw.singular(w)}
    return out


def leaked(goal: str, private: list[str]) -> list[tuple[str, str]]:
    """The validator's harness (z_harness.leaks): the goal's search words,
    every word and pair of neighbours of the goal, run through the screen
    with the goal; any private word in what is sent, in the search words, or
    in the subject is a leak."""
    pf = _forms(private)
    terms = gw.search_terms(goal)
    ws = re.findall(r"[a-z0-9']+", goal.lower())
    phrases = list(terms) + ws + [f"{a} {b}" for a, b in zip(ws, ws[1:])] + [" ".join(private)]
    result = oq.screen(phrases, goal=goal)
    out: list[tuple[str, str]] = []
    for t in terms:
        if _forms(t.split()) & pf:
            out.append(("terms", t))
    for s in result.sendable:
        if _forms(s.phrase.split()) & pf:
            out.append(("sent", s.phrase))
    sub = oq.subject_sendable(goal)
    if sub is not None and _forms(sub.phrase.split()) & pf:
        out.append(("subject", sub.phrase))
    return out


# -- n1 / n2: sensitive words about her own people ------------------------------------

#: The validator's leaking goals, word for word, and their private words.
VALIDATOR_LEAKS = [
    ("a meal bot for my dad after his stroke", ["stroke"]),
    ("a chore bot, my wife caught me cheating and we are rebuilding", ["cheating"]),
    ("a calendar bot for my teen who has been cutting", ["cutting"]),
    ("a bot for my kid who is on the spectrum", ["spectrum"]),
    ("Meal planner - my husband's stroke", ["stroke"]),
    ("Meal planner - my husband's cheating", ["cheating"]),
    ("a homework helper for my kid after the cutting", ["cutting"]),
    ("a homework helper for my kid after the cheating", ["cheating"]),
    ("a homework helper for my kid after the stroke", ["stroke"]),
]


@pytest.mark.parametrize("goal,private", VALIDATOR_LEAKS)
def test_the_validators_leaking_goals_keep_their_private_word(goal, private):
    assert leaked(goal, private) == []


#: The validator's ten n1 shapes, with the four words that leaked there.
N1_SHAPES = ["a chore chart bot, my daughter deals with {w} so go easy",
             "set up a planner for my dad's {w} appointments",
             "A pantry bot. FYI: {w}",
             "my wife is going through {w}, I want a bot to track groceries",
             "a bot for my sister since she has {w}",
             "a homework helper for my kid after the {w}",
             "budget bot (we are dealing with {w})",
             "Meal planner - my husband's {w}",
             "a reminder bot for me because I have {w}",
             "a sleep tracker bot, background: {w}"]


@pytest.mark.parametrize("shape", N1_SHAPES)
@pytest.mark.parametrize("word", ["stroke", "cutting", "cheating", "spectrum"])
def test_the_everyday_sensitive_words_stay_in_every_shape(shape, word):
    goal = shape.format(w=word)
    assert leaked(goal, [word]) == [], goal


@pytest.mark.parametrize("goal,private", [
    ("a meal bot after my mom's stroke", ["stroke"]),
    ("a recipe bot for my wife after her miscarriage", ["miscarriage"]),
    ("a bot for my sister after her breakdown", ["breakdown"]),
    ("a calendar bot for my son who has been purging", ["purging"]),
    ("a chore bot for my wife who is drinking again", ["drinking"]),
    ("a calendar bot for my teen who keeps cutting", ["cutting"]),
    ("a budget bot, my husband has been cheating", ["cheating"]),
    ("a planner for my son, he is on the spectrum", ["spectrum"]),
])
def test_after_his_x_and_who_has_been_x_are_her_story(goal, private):
    assert leaked(goal, private) == []


def test_an_unlisted_word_after_his_is_held_whatever_it_is():
    # Grammar, not a list: "zorbitis" is on no list anywhere.
    goal = "a meal bot for my dad after his zorbitis"
    assert leaked(goal, ["zorbitis"]) == []


def test_an_unlisted_event_after_the_is_held_in_a_goal_about_her_people():
    # "accident" is on no list: grammar holds it (fail closed, so "after
    # the soccer game" holds "game" too in a goal about her kids).
    assert leaked("a homework helper for my kid after the accident", ["accident"]) == []
    assert leaked("a snack bot for my kids after the soccer game", ["game"]) == []


def test_after_the_in_a_goal_about_a_thing_is_still_searched():
    assert "party" in gw.search_terms("a bot to clean up after the party")


def test_an_unlisted_word_her_teen_has_been_doing_is_held():
    goal = "a calendar bot for my teen who has been florbing"
    assert leaked(goal, ["florbing"]) == []


@pytest.mark.parametrize("goal,word", [
    ("a swim stroke drill bot for my son", "stroke"),
    ("a calligraphy brush stroke practice bot for my daughter", "stroke"),
    ("a stroke order kanji bot", "stroke"),
    ("a paper cutting crafts bot for my kids", "cutting"),
    ("a lawn cutting schedule bot for my dad", "cutting"),
    ("a chess cheating detector bot for my club", "cheating"),
    ("a light spectrum science bot for my kid", "spectrum"),
])
def test_the_everyday_meaning_is_still_searched(goal, word):
    assert word in gw.search_terms(goal)


def test_personal_sensitive_has_no_category_twice():
    """A category written twice in the literal would silently drop the
    first one's words (Python keeps the last key)."""
    src = inspect.getsource(pw)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == \
                "PERSONAL_SENSITIVE":
            comp = node.value
            literal = comp.generators[0].iter.func.value  # {...}.items()
            keys = [k.value for k in literal.keys]
            assert len(keys) == len(set(keys)), keys
            for rows in literal.values:
                row_keys = [k.value for k in rows.keys]
                assert len(row_keys) == len(set(row_keys)), row_keys
            break
    else:
        pytest.fail("PERSONAL_SENSITIVE not found")
    for w in ("binder", "drag", "pan", "cheating"):
        assert pw.PERSONAL_SENSITIVE[w][0] == "sex"


# -- n5: over-holding -----------------------------------------------------------------

# The job after her person's condition tells the story when it
# manages the condition ("stop crying", "count calories", a diet changed for
# it: "bake low sugar cookies"), so it is held and listed unless it is the
# bot's subject. A plain pastime after the condition is searched; only the
# condition is held.
@pytest.mark.parametrize("goal,held,job", [
    ("a bot to help my grandma with diabetes bake low sugar cookies", ["diabetes"],
     ["bake", "low", "sugar"]),
])
def test_the_job_after_her_persons_condition_is_held_and_listed(goal, held, job):
    found = pw.find(goal)
    rows = [h["word"] for h in found.held]
    assert rows[0] == held[0] and _forms(job) <= _forms(rows)
    terms = gw.search_terms(goal)
    assert not _forms(job) & set(terms), (goal, terms)
    assert leaked(goal, held + job) == []


@pytest.mark.parametrize("goal,held,job", [
    ("a bot to help my aunt with lupus plan short walks", ["lupus"], ["walk"]),
    ("a bot for my son with ocd to practice piano scales", ["ocd"], ["piano", "scale"]),
    ("a bot to help my brother with adhd organize his comic books", ["adhd"],
     ["comic", "book"]),
])
def test_a_pastime_after_her_persons_condition_is_searched(goal, held, job):
    assert [h["word"] for h in pw.find(goal).held] == held
    terms = gw.search_terms(goal)
    assert set(job) <= set(terms) and not set(held) & set(terms), (goal, terms)
    assert leaked(goal, held) == []


def test_the_job_words_leave_only_as_the_subject():
    goal = "a bot to help my grandma with diabetes bake low sugar cookies"
    assert oq.screen(["low sugar cookies"], goal=goal).sendable == []
    goal = "a short walks planner bot for my aunt with lupus"
    assert [s.phrase for s in oq.screen(["short walks"], goal=goal).sendable] == \
        ["short walks"]
    assert leaked(goal, ["lupus", "aunt"]) == []


@pytest.mark.parametrize("goal,private", [
    # A private word in the job keeps the whole job private.
    ("a bot for my brother with schizophrenia to track his meds",
     ["schizophrenia", "meds", "track"]),
    ("a bot for my son with ocd to plan his therapy visits", ["ocd", "therapy", "visit"]),
    ("a bot for my wife with bipolar to log her mania days", ["bipolar", "mania"]),
    ("a bot for my son with ocd to plan Christmas with Grandma Rose", ["ocd", "christmas",
                                                                        "rose"]),
])
def test_a_job_that_tells_the_story_stays_private(goal, private):
    assert leaked(goal, private) == []


def test_low_vision_is_not_cut_into_a_job():
    goal = "a bot to help my mom with low vision read recipes"
    held = [h["word"] for h in pw.find(goal).held]
    assert "vision" in held
    assert leaked(goal, ["vision"]) == []


def test_insulin_pump_still_holds_pump_outside_the_with_frame():
    goal = "a log bot for my dad's insulin pump schedule"
    assert leaked(goal, ["insulin", "pump"]) == []


# -- n4: device lines -----------------------------------------------------------------

def _devices(goal: str) -> list[str]:
    return [i.get("id") for i in live.proposal_items(goal)]


@pytest.mark.parametrize("goal,want", [
    ("lights:\n- Philips Hue\n- LIFX\n- Wyze cam",
     ["device:ha:hue", "device:ha:lifx", "device:none:wyze_cam"]),
    ("my devices:\nHue\nSonos\nRing", ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("a home bot for:\n* Ecobee\n* Roku\n* Kasa plugs",
     ["device:ha:ecobee", "device:ha:roku"]),
    ("a home bot for Hue lights / Sonos speakers / Ring doorbell",
     ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("a home bot for my Hue lights; Sonos speakers; Ring doorbell",
     ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("things to pack:\n1. Sonos speaker\n2. Hue bulbs", ["device:ha:sonos", "device:ha:hue"]),
])
def test_every_thing_in_her_list_gets_its_line(goal, want):
    got = _devices(goal)
    for w in want:
        assert w in got, (goal, got)
    # Every line of a device list gets one line, nothing more.
    if "\n" in goal:
        assert len([g for g in got if g.startswith("device:")]) == len(goal.split("\n")) - 1


@pytest.mark.parametrize("goal", [
    "a bot for Ring Pop candy reviews",
    "a bot for the Ring Pop fan club",
    "a grocery bot:\n- Apples\n- Bread\n- Milk",
    "shopping list:\n- Ring Pops\n- Apple juice\n- Nest eggs",
    "chores:\n- dishes\n- laundry\n- vacuum",
    "a bot for Apple fans",
    "for Nest egg savings tips",
])
def test_things_that_are_not_her_devices_get_no_line(goal):
    assert not [g for g in _devices(goal) if g.startswith("device:")]


def test_on_off_with_a_slash_is_not_a_list():
    assert _devices("turn lights on/off for my Hue lights") == ["device:ha:hue"]


# -- n3: backgrounds ------------------------------------------------------------------

@pytest.mark.parametrize("words", ["clouds no", "clouds no thanks", "stars nope",
                                   "clouds nah, stars", "clouds no please"])
def test_a_no_right_after_the_background_turns_it_down(words):
    named, down = looks_find._background_words(words)
    assert named != "clouds" or "clouds" not in words
    assert ("clouds" if "clouds" in words else "stars") in down


def test_clouds_no_stars_is_about_the_stars():
    named, down = looks_find._background_words("clouds no stars")
    assert "clouds" not in down
