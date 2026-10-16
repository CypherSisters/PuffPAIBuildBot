"""Private-story words and what a private story lists.

* sensitive words she wrote herself left on the real path (z1);
* ordinary job words in a private story were listed as kept private, and
  "after" was listed as a name (z5);
* turned-down backgrounds were named (z backgrounds);
* device lines missed things in her list (z6).

Every probe is offline: the goal reader, the outbound screen and the device
lines read files on this computer, and nothing is sent anywhere.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

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


# -- z1: sensitive words she wrote herself ------------------------------------------

#: The validator's six sentences and what must stay.
VALIDATOR_SIX = [
    ("a meal planner bot for my teen son who just started puberty blockers",
     ["puberty", "blockers"]),
    ("a reminder bot for my clomid shots and ovulation days", ["clomid", "ovulation"]),
    ("a budget bot because my husband lost our savings on sports betting",
     ["sports", "betting"]),
    ("a homework bot for my son, he got shingles and is home this week", ["shingles"]),
    ("a reminder bot for my anger management class", ["anger", "management"]),
    ("a meal bot for my mom who has a DNR order", ["dnr"]),
]


@pytest.mark.parametrize("goal,private", VALIDATOR_SIX)
def test_the_validators_six_sentences_keep_their_private_words(goal, private):
    assert leaked(goal, private) == [], goal


#: Every word that still left at 165d7ea in the validator's sweep (z1_sens.py,
#: 221 words in five shapes; 344 of 1105 goals leaked there).
Z1_LEAKED_AT_HEAD = """shingles|lithium|crack|ed|acne|bunions|macular degeneration|als|ms|asperger|
night terrors|nightmares|bed bugs|pinworms|chapter 7|chapter 13|maxed out credit cards|title loan|
rent to own|section 8|workers comp|clomid|tubes tied|plan parenthood|ppd|baby blues|
puberty blockers|binder|drag|pronouns|bi|pan|aromantic|lent|republican|democrat|maga|socialist|
union dues|green party|nra|blm|antifa|pro life|pro choice|commissary|cell phone contraband|gang|
cartel|dealer|pills|chewing tobacco|blackout drunk|aa sponsor|sober coach|smart recovery|
sports betting|lottery tickets|one night stand|hazing|grooming|runaway|missing child|amber alert|
supervised visits|marriage counseling|couples therapy|survivor guilt|dnr|organ donor|ashes|wake|
shiva|sitting shiva|estate sale after death|inheritance fight""".replace("\n", "").split("|")
Z1_SHAPES = [
    "a grocery list bot, my brother has {w} so keep it simple",
    "make me a reminder bot for my {w} stuff",
    "I need a calendar bot to plan around {w} with my mom",
    "bot to help my teen son who is struggling with {w}",
    "A weekly planner. Context: {w}.",
]
#: Words of the validator's phrases that are not the detail itself.
_NOT_THE_DETAIL = {"out", "to", "after", "credit", "cards", "cell", "phone", "estate", "sale"}


@pytest.mark.parametrize("shape", Z1_SHAPES)
def test_the_sweep_words_that_left_at_head_stay_in_every_shape(shape):
    bad = []
    for w in Z1_LEAKED_AT_HEAD:
        goal = shape.format(w=w)
        private = [x for x in w.split() if x not in _NOT_THE_DETAIL and not x.isdigit()]
        got = leaked(goal, private)
        if got:
            bad.append((goal, got))
    assert bad == []


def test_an_asperger_subject_gets_the_health_note_not_a_brand_note():
    goal = "An asperger social skills bot"
    found = pw.find(goal)
    assert found.subject == "asperger social skills"
    assert found.subject_notes == ["health"]
    assert pw.subject_note_kind(goal) == "health"


@pytest.mark.parametrize("goal,unknown", [
    ("a grocery list bot, my brother has zentravil so keep it simple", "zentravil"),
    ("a meal bot for my mom who has a QRV order", "qrv"),
    ("a chore bot for my son who just started vexorin", "vexorin"),
    ("a homework bot for my son, he got morbillio and is home", "morbillio"),
    ("a chore bot for our house. Note: glimmerfoot.", "glimmerfoot"),
    ("A weekly planner. Background - quenditis.", "quenditis"),
])
def test_a_word_nobody_listed_stays_in_the_shapes_about_her_people(goal, unknown):
    """Grammar, not a list: what one of her people has, started or takes,
    and what she adds after "Context:"/"Note:", hold a word the spelling
    list does not know, whatever it is."""
    assert leaked(goal, [unknown]) == [], goal
    rows = {h["word"]: h for h in pw.find(goal).held}
    assert unknown in rows and not rows[unknown]["can_allow"]


def test_a_context_label_never_makes_the_subject():
    goal = "A weekly planner. Context: sober coach."
    assert pw.find(goal).subject == ""
    assert oq.subject_sendable(goal) is None
    assert pw.subject_of("a sober coach bot") == "sober coach"


@pytest.mark.parametrize("goal,kept", [
    ("a recipe bot for sheet pan dinners for my family", "pan"),
    ("a roofing estimate bot for my asphalt shingles", "shingle"),
    ("a bot to crack the daily wordle with my son", "crack"),
    ("a bot to track the books I lent to friends", "lent"),
    ("a budget bot to track ATM withdrawal fees for my account", "withdrawal"),
    ("a chess bot for my son to practice pawn endgames", "pawn"),
    ("a python tutor bot for my son on inheritance and classes", "inheritance"),
    ("a quiz bot for my daughter who loves horses", "horse"),
    ("a homework helper for my daughter who struggles with fractions", "fraction"),
    ("a carpool bot, my son has soccer practice on tuesdays", "carpool"),
    ("a beekeeping bot for my hives", "hive"),
    ("a bot for my dad who has a woodshop to plan projects", "project"),
])
def test_everyday_words_next_to_her_people_are_still_searched(goal, kept):
    """Over-holding: a word that is private only in her story ("pan",
    "shingles", "lent") is searched next to the word that makes it an
    everyday thing, and the shapes about her people stop where her job
    starts ("to plan projects")."""
    assert kept in gw.search_terms(goal) or kept in pw.find(goal).subject.split(), \
        (goal, gw.search_terms(goal), pw.find(goal).subject)


def test_full_hookups_at_a_campsite_are_not_private():
    goal = "a camping checklist bot, we have an RV with full hookups"
    assert not [h for h in pw.find(goal).held if h["why"] in ("health", "private")]
    assert "hookup" in gw.search_terms(goal)


@pytest.mark.parametrize("goal,held", [
    ("a carpool bot, my son has soccer practice on tuesdays", {"soccer", "practice"}),
    ("a grocery bot, also my sister has kidney infection lately", {"kidney", "infection"}),
    ("a practice bot for my son who just started piano lessons", {"piano", "lessons"}),
])
def test_what_her_people_have_or_started_is_held_whatever_it_is(goal, held):
    """Fail closed: what one of her
    people has or started, said bare, is held whatever the word is, so a
    condition no list names stays; an everyday thing there is held too,
    and the bot's subject is still searched."""
    rows = {h["word"] for h in pw.find(goal).held}
    assert held <= rows
    assert not set(gw.search_terms(goal)) & _forms(held)


# -- z5: what a private story lists ----------------------------------------------------
# In a "my <kin> with <condition>" story the job after the condition
# tells the story when it manages the condition ("stop crying", "count
# calories", "remember her ... tasks"), so it is held unless it is the bot's
# subject. A plain pastime after the condition
# ("find old baseball games", "plan weekly grocery trips") is searched; only
# the condition is held and listed.

@pytest.mark.parametrize("goal,listed", [
    ("a bot to help my mom with dementia remember her garden tasks",
     {"dementia", "remember", "garden", "tasks"}),
    ("help my grandpa with alzheimers find old baseball games", {"alzheimers"}),
    ("a bot to help my son with autism learn to tie his shoes", {"autism"}),
    ("a bot to help my wife with anxiety plan weekly grocery trips", {"anxiety"}),
    ("a bot to help my sister after chemo find easy soup recipes", {"chemo"}),
    ("a bot to help my dad with parkinsons keep track of his woodworking projects",
     {"parkinsons"}),
])
def test_a_private_story_lists_every_word_it_holds(goal, listed):
    assert {h["word"] for h in pw.find(goal).held} == listed


def test_the_job_after_a_health_word_is_held_only_when_it_manages_it():
    terms = gw.search_terms("help my grandpa with alzheimers find old baseball games")
    assert "alzheimers" not in terms and {"baseball", "game"} <= set(terms)
    terms = gw.search_terms("a bot to help my brother with alcoholism avoid bars")
    assert not {"bar", "bars", "avoid", "alcoholism"} & set(terms)
    goal = "an old baseball games bot for my grandpa with alzheimers"
    assert pw.find(goal).subject == "old baseball games"
    assert "alzheimers" not in gw.search_terms(goal)


def test_a_joining_word_is_never_a_name():
    rows = pw.find("a bot to help my sister after chemo find easy soup recipes").held
    assert all(h["word"] != "after" for h in rows)


def test_an_unknown_word_after_for_stays_a_name_she_can_tick():
    """Kept on purpose (fail closed): a word the spelling list does not know,
    right after "for" ("for fescue grass", "for jayden"), may be a child's
    name. It is held and shown as a name she can tick to let out."""
    rows = {h["word"]: h for h in pw.find("a lawn care schedule bot for fescue grass").held}
    assert rows["fescue"]["can_allow"] is True
    got = oq.screen(["fescue grass"], goal="a lawn care schedule bot for fescue grass",
                    allowed_words=["fescue"])
    assert [s.phrase for s in got.sendable] == ["fescue grass"]


# -- turned-down backgrounds ------------------------------------------------------------

@pytest.mark.parametrize("words", [
    "too many stars", "stars aren't for me", "stars? way too much",
    "stars would be overwhelming", "stars not needed", "clouds don't fit",
])
def test_a_turned_down_background_is_never_named(words):
    named, down = looks_find._background_words(words)
    assert named is None and down


@pytest.mark.parametrize("words,bg", [
    ("not too many stars please, but some", "stars"),
    ("stars aren't bad", "stars"),
    ("clouds don't bother me", "clouds"),
    ("stars wouldn't hurt", "stars"),
    ("stars, not too bright", "stars"),
])
def test_a_background_she_asks_for_is_never_turned_down(words, bg):
    assert looks_find._background_words(words) == (bg, set())


# -- z6: device lines -------------------------------------------------------------------

def _devices(goal: str) -> list[str]:
    return [i["id"] for i in live.proposal_items(goal) if i["section"] == "devices"]


@pytest.mark.parametrize("goal,want", [
    ("a bot for my Hue lights\nSonos speakers\nRing doorbell",
     ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("a bot for Hue, Sonos and Ring", ["device:ha:hue", "device:ha:sonos", "device:ha:ring"]),
    ("control my hue bulbs and my sonos", ["device:ha:hue", "device:ha:sonos"]),
    ("a bot for the Ring doorbell and Arlo cameras", ["device:ha:ring"]),
    ("A bot for the Ecobee thermostat, the August lock and our Roku",
     ["device:ha:ecobee", "device:ha:august", "device:ha:roku"]),
    ("a bot for my TP-Link Kasa plugs", ["device:ha:tplink"]),
])
def test_every_thing_in_her_list_gets_its_line(goal, want):
    got = _devices(goal)
    assert [d for d in got if d.startswith("device:ha:")] == want, got


@pytest.mark.parametrize("goal", [
    "a study bot for Google interviews",
    "a homework bot for Emma, Liam and Noah",
    "a quiz bot for Harry Potter fans",
    "a recipe bot for Thanksgiving",
])
def test_names_after_for_are_not_devices(goal):
    assert _devices(goal) == []
