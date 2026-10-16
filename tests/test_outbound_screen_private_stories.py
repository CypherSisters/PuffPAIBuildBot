"""Private stories and held names.

* Held names: a lower-case name after "for" is held, and a name's plural or
  possessive typed without the apostrophe ("emmas") never leaves, also on
  the rule-only needs path and in rule 3 (proper nouns).
* Sensitive stories: when a goal tells a private story that is not the bot's
  subject, only the subject leaves; no list has to name every word of it.
* Live info: each of the goal's own words is checked on its own, so a
  well-matched website is kept and off-purpose ones stay off.
* The topic book's words never end on a doing word ("taking").
* Over-holding: ordinary news words are neither hard nor held.

Unit tests only; no network (the conftest socket guard).
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai.catalogs import goal_words, live, personal_words as pw
from puffpai.catalogs import wikipedia_topics as wt
from puffpai.security import outbound_query as oq

HERE = os.path.dirname(os.path.abspath(__file__))


def _sent(res) -> list[str]:
    return [s.phrase for s in res.sendable]


def _goals() -> list[dict]:
    with open(os.path.join(HERE, "fixtures", "tuning_goals.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return data if isinstance(data, list) else data["goals"]


def _rule_only_sends(goal: str) -> list[str]:
    from puffpai.wizard import needs
    old = needs._brain
    needs._brain = lambda cfg: ("", "")
    try:
        return [s.phrase for s in needs.sends(needs.run(goal, cfg=None, deadline_s=5), goal=goal)]
    finally:
        needs._brain = old


# --- held names -----------------------------------------------------------------

NAME_GOALS = [
    ("I want a party planner bot for emma. I'll need to plan emmas birthday party and "
     "track the guest list", {"emma", "emmas"}),
    ("I want a party planner bot for jake. I'll need to plan jakes birthday party and "
     "track the guest list", {"jake", "jakes"}),
    ("I want a birthday bot for olivia. I'll need to plan olivias party", {"olivia", "olivias"}),
    ("I want a chess coach bot for noah. I'll need to track noahs games", {"noah", "noahs"}),
    ("I want a party planner bot. I'll need to plan jaydens party", {"jaydens", "jayden"}),
    ("a quiz bot for sofia and anna", {"sofia", "anna"}),
]


@pytest.mark.parametrize("goal,names", NAME_GOALS)
def test_a_lower_case_name_never_leaves_on_the_rule_only_path(goal, names):
    sent = _rule_only_sends(goal)
    words = {w for p in sent for w in p.split()}
    assert not words & names, sent
    assert not set(goal_words.search_terms(goal)) & names
    held = {h["word"] for h in pw.find(goal).held}
    assert held & names, held


@pytest.mark.parametrize("phrase", ["emmas party", "olivias cake", "annas party",
                                    "sofias gift", "emma party"])
def test_rule_3_holds_a_names_plural_typed_without_the_apostrophe(phrase):
    # Also on a goal that never names the person.
    assert _sent(oq.screen([phrase], goal="a party planner bot")) == []
    assert _sent(oq.screen([phrase, "party planner"],
                           goal="a party planner bot for emma")) == ["party planner"]


def test_search_terms_leave_a_possessive_name_out():
    assert goal_words.search_terms("plan emmas party. party planner bot") == ["party", "planner"]


@pytest.mark.parametrize("goal,held_none", [
    ("a quiz bot for beginners", True),
    ("a bot for bill payments", True),
    ("a homework helper bot for school", True),
])
def test_ordinary_words_after_for_are_not_names(goal, held_none):
    assert [h for h in pw.find(goal).held if h["why"] == "name"] == []


def test_an_unknown_plural_word_still_names_the_bot():
    # Held when it may be a name, but it stays in the subject it names.
    assert pw.subject_of("a postgres backup helper bot") == "postgres backup"
    assert pw.subject_of("I want an emmas birthday bot") == "birthday"


def test_a_name_that_is_an_ordinary_word_is_held_at_the_end_of_a_clause():
    assert "grace" in {h["word"] for h in pw.find("a quiz bot for grace.").held}


# --- sensitive stories ----------------------------------------------------------

STORIES = [
    ("I want a journal bot. I'll need to be able to log my tinder dates, track my weed use, "
     "and note my hookups", "journal"),
    ("I want a planner bot. I'll need to track my court dates, renew my visa, and pay down "
     "my student loans", "planner"),
    ("I want a journal bot to help me process the loss of my mom. I'll need to plan her "
     "memorial, visit her grave, and find the cemetery hours", "journal"),
    ("I want a safety planner bot. My husband has beaten me. I'll need to find a shelter, "
     "keep a log of the bruises, and plan my escape", "safety planner"),
    ("I want a budget bot. I'll need to track overdue bills, deal with collections calls, "
     "and fix my credit score after foreclosure", "budget"),
    ("I want a planner bot. I'll need to track my hiv meds, my prep refills, and my std "
     "tests", "planner"),
    ("I want a prayer bot. I'll need to track my daily salah, fasting in ramadan, and quran "
     "reading", "prayer"),
    ("I want a journal bot. I'll need to log my coming out, my pronouns, and my transition "
     "milestones", "journal"),
    ("I want a planner bot. I'll need to plan my chemo appointments and my radiation "
     "schedule", "planner"),
    ("I want a planner bot. I'll need to track my rehab meetings, my aa sponsor calls, and "
     "my relapse triggers", "planner"),
]


@pytest.mark.parametrize("goal,subject", STORIES)
def test_a_private_story_sends_only_the_kind_of_bot(goal, subject):
    assert pw.subject_of(goal) == subject
    assert pw.private_goal_words(goal)
    assert _rule_only_sends(goal) == [subject]
    assert set(goal_words.search_terms(goal)) <= set(subject.split())


@pytest.mark.parametrize("goal,subject", STORIES[:3])
def test_a_private_story_is_never_ticked_out(goal, subject):
    stop = goal_words.stopwords()
    words = [w for w in goal_words.raw_words(goal) if len(w) > 3 and w not in stop
             and w not in subject.split()][:12]
    assert len(words) >= 5
    got = oq.screen([f"{subject.split()[0]} {w}" for w in words], goal=goal,
                    allowed_words=words)
    assert _sent(got) == []


def test_words_that_are_not_hers_still_leave_a_private_story():
    goal = STORIES[3][0]
    assert _sent(oq.screen(["safety planner", "safety plan template", "shelter finder"],
                           goal=goal)) == ["safety planner"]
    assert _sent(oq.screen(["safety app", "planner template"], goal=goal)) == [
        "safety app", "planner template"]


def test_a_sensitive_subject_alone_is_not_a_private_story():
    goal = "I want a sobriety tracker bot. Track my sobriety milestones"
    assert pw.private_goal_words(goal) == {}
    assert _sent(oq.screen(["sobriety milestone"], goal=goal)) == ["sobriety milestone"]


def test_no_fixture_goal_is_a_private_story():
    # Everyday words that are on the sensitive list ("drug names" for a
    # nursing student, a pet's medications, a gluten-free meal plan) hold
    # themselves but never make the whole goal private.
    for row in _goals():
        assert pw.private_goal_words(row["text"]) == {}, row.get("id")
    for goal in ("I'm in nursing school and my pharmacology exam has 200 drug names",
                 "a fitness coach bot for my physical therapy exercises",
                 "a family budget bot to track our income and spending",
                 "a D&D helper bot to track death saves",
                 "a garden helper bot for soil fertility",
                 "a church bake sale planner bot"):
        assert pw.private_goal_words(goal) == {}, goal


@pytest.mark.parametrize("word", ["visa", "beaten", "cemetery", "grave", "memorial",
                                  "dating", "bruises", "hiv", "chemo"])
def test_new_sensitive_words_are_held_on_a_neutral_goal(word):
    assert _sent(oq.screen([word, f"planner {word}", "planner ideas"],
                           goal="I want a planner bot")) == ["planner ideas"]


# --- live info fits by each of the goal's words -----------------------------------

def _live(goal: str) -> list[str]:
    return [i["apply"]["ref"] for i in live.proposal_items(goal) if i["section"] == "live_info"]


@pytest.mark.parametrize("goal,wanted", [
    ("a earthquake alert bot", {"seed:usgs_quakes"}),
    ("a book club helper bot that finds books",
     {"seed:gutendex", "seed:openlibrary", "seed:internet_archive"}),
    ("a bird watching log bot", {"seed:gbif_species", "seed:inaturalist"}),
    ("a word of the day bot to learn new english words",
     {"seed:free_dictionary", "seed:datamuse"}),
    ("a vegetable garden helper bot", {"seed:hardiness_zone"}),
])
def test_a_well_matched_website_is_kept(goal, wanted):
    assert wanted <= set(_live(goal))


def test_off_purpose_websites_stay_off_the_fixture_goals():
    by_id = {row["id"]: row["text"] for row in _goals()}
    assert not {"seed:pokeapi", "seed:mlb_stats"} & set(_live(by_id["dnd"]))
    assert not {"seed:swpc_kp", "seed:nws"} & set(_live(by_id["car"]))
    assert not {"seed:pokeapi", "seed:worldbank"} & set(_live(by_id["budget"]))
    assert not {"seed:tvmaze", "seed:fruityvice"} & set(_live(by_id["pet"]))
    assert "seed:seventimer" not in _live("a movie night picker bot")
    for gid, text in by_id.items():
        if gid != "space":
            assert not [r for r in _live(text) if "nasa" in r or "nager" in r], gid


def test_most_fixture_goals_keep_a_live_line():
    by_id = {row["id"]: row["text"] for row in _goals()}
    with_lines = {gid for gid, text in by_id.items() if _live(text)}
    assert {"garden", "dnd", "bakery", "car", "pet", "budget", "band", "space",
            "meals"} <= with_lines


def test_english_only_word_lists_do_not_fit_a_spanish_tutor():
    # The validator counted the English dictionary as a lost Spanish-tutor
    # line; it only knows English words, so it is off-purpose there.
    by_id = {row["id"]: row["text"] for row in _goals()}
    assert not {"seed:free_dictionary", "seed:datamuse"} & set(_live(by_id["spanish"]))


# --- the topic book's words -------------------------------------------------------

def test_a_goal_with_no_kind_of_bot_names_its_book_with_thing_words():
    by_id = {row["id"]: row["text"] for row in _goals()}
    words = wt.topic_words(by_id["bakery"])
    assert words == ["bakery", "recipe"]
    assert "taking" not in words


# --- over-holding ------------------------------------------------------------------

@pytest.mark.parametrize("goal", [
    "My mom and I are opening a small bakery. I want a bot that helps with recipes",
    "I want to build a wedding planner bot. My brother just got engaged",
    "We just moved and adopted a dog. I want a pet care bot",
    "I'm retiring next year, I want a travel planner bot",
    "a chess coach bot. I just joined a chess club and want to learn the openings",
])
def test_news_words_are_not_kept_private(goal):
    found = pw.find(goal)
    news = {"opening", "openings", "married", "getting", "engaged", "got", "moved",
            "adopted", "retiring"}
    # "Kept private" must list every word held: a relative's news ("my
    # brother just got engaged") is held and listed; the owner's own news
    # and the helper word "got" stay ordinary words.
    if "my brother just got engaged" in goal.lower():
        news -= {"engaged"}
        assert "engaged" in {h["word"] for h in found.held}
    assert not found.hard & news
    assert not {h["word"] for h in found.held} & news


# --- the sensitive subject's note ------------------------------------------------

@pytest.mark.parametrize("goal,kind", [
    ("a divorce planner bot", "private"),
    ("a grief journal bot", "private"),
    ("a prayer bot", "private"),
    ("an anxiety journal bot", "health"),
    ("a vegan recipe bot", "health"),
    ("a wedding planner bot", ""),
])
def test_the_subject_note_says_what_kind_of_private_it_is(goal, kind):
    assert pw.subject_note_kind(goal) == kind
    # The pinned note stays "health" for every sensitive subject, so the
    # wizard keeps warning until it shows the private line.
    assert ("health" in pw.find(goal).subject_notes) == bool(kind)


def test_the_private_subject_line_is_plain_and_listed():
    line = pw.PRIVATE_SUBJECT_NOTE
    assert "health" not in line and chr(0x2014) not in line
    with open(os.path.join(HERE, "fixtures", "ui_strings", "privacy_and_looks.txt"),
              encoding="utf-8") as fh:
        assert line in fh.read().splitlines()
