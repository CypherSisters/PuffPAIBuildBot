"""Rule-only needs: her own list and the bot's subject.

The goals are the 12 unrelated tuning goals (``tuning_goals.json`` when
present, else its own copy of the same file).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from puffpai.catalogs import needs_rules

FIX = Path(__file__).parent / "fixtures"


def _goals() -> dict[str, dict]:
    path = FIX / "tuning_goals.json"
    return {g["id"]: g for g in json.loads(path.read_text(encoding="utf-8"))["goals"]}


#: The things each goal lists, as words a recovered job must hold (53 in all).
LISTED = {
    "wedding": [["theme", "picture"], ["idea"], ["vendor"], ["budget"], ["wedding plan"]],
    "garden": [["tomato"], ["water"], ["plant each month"], ["kale"]],
    "fitness": [["runs", "lifts"], ["training plan"], ["weight", "sleep"], ["stretch"]],
    "dnd": [["initiative"], ["dice"], ["loot"], ["session notes"], ["spells"]],
    "bakery": [["recipes", "costing"], ["cake orders"], ["morning bake"], ["ingredients"],
               ["instagram"]],
    "car": [["oil"], ["registration"], ["recalls"], ["warning lights"]],
    "spanish": [["conversation"], ["vocabulary"], ["grammar"], ["phrases"]],
    "pet": [["vet visits"], ["vaccines"], ["feeding"], ["walk the dog"], ["toxic"]],
    "budget": [["spending"], ["monthly budgets"], ["summer camp"], ["credit card"], ["charts"]],
    "band": [["setlists"], ["which songs"], ["metronome"], ["gigs"], ["money"]],
    "space": [["pictures of planets"], ["space station"], ["quiz"]],
    "meals": [["weekly dinners"], ["save recipes"], ["grocery list"], ["fridge"]],
}


def test_the_listed_table_holds_53_items():
    assert sum(len(v) for v in LISTED.values()) == 53
    assert set(LISTED) == set(_goals())


def test_explicit_list_recovery_over_the_12_goals():
    goals = _goals()
    found, missed = 0, []
    for gid, wanted in LISTED.items():
        names = [j["name"].lower() for j in needs_rules.rule_needs(goals[gid]["text"])["jobs"]]
        for words in wanted:
            if any(all(w in n for w in words) for n in names):
                found += 1
            else:
                missed.append((gid, words))
    print(f"listed items recovered: {found} of 53; missed: {missed}")
    assert found >= 52, missed


def test_rule_jobs_keep_her_words():
    goal = _goals()["wedding"]["text"]
    names = [j["name"] for j in needs_rules.rule_needs(goal)["jobs"]]
    assert names == ["Track themes from pictures", "Come up with ideas",
                     "Find wedding vendors and materials to rent", "Plan the budget",
                     "Do a whole wedding plan"]


def test_rule_phrases_hold_no_personal_word():
    for gid, g in _goals().items():
        personal = {w.lower() for p in g["personal"] for w in p.split()}
        for job in needs_rules.rule_needs(g["text"])["jobs"]:
            for phrase in job["search"]:
                assert not set(phrase.split()) & personal, (gid, phrase)
                assert 1 <= len(phrase.split()) <= 4


@pytest.mark.parametrize("goal,subject", [
    ("I want to build a wedding planner bot. My brother just got engaged and there is a "
     "lot to do.", "wedding planner"),
    ("Minecraft server helper bot for my kids", "minecraft server"),
    ("a Home Assistant dashboard helper bot", "home assistant dashboard"),
    ("a 3D printer helper", "3d printer"),
    ("a vegan recipe bot", "vegan recipe"),
    ("a pregnancy tracker bot", "pregnancy tracker"),
    ("knitting patterns bot", "knitting patterns"),
    ("my sister's wedding bot", "wedding"),
    ("I want a Portland bakery bot. We sell bread in Portland.", "bakery"),
    ("a Portland bakery bot", "portland bakery"),
    ("I run a Dungeons & Dragons campaign. I want a bot that rolls dice.", ""),
])
def test_kind_words_follow_the_subject_rule(goal, subject):
    assert needs_rules.subject_of(goal) == subject


def test_home_town_is_never_part_of_the_subject():
    assert needs_rules.subject_of("a Portland bakery bot", home={"town": "Portland"}) == "bakery"


def test_kind_words_match_lane_l1_when_present():
    pw = pytest.importorskip("puffpai.catalogs.personal_words",
                             reason="personal_words is not in this build")
    for g in _goals().values():
        assert needs_rules.subject_of(g["text"]) == pw.subject_of(g["text"]), g["id"]


def test_subjects_of_the_12_goals():
    got = {gid: needs_rules.subject_of(g["text"]) for gid, g in _goals().items()}
    assert got["wedding"] == "wedding planner"
    assert got["spanish"] == "spanish tutor"
    assert got["garden"] == "garden"
    for gid, g in _goals().items():
        personal = {w.lower() for p in g["personal"] for w in p.split()}
        assert not set(got[gid].split()) & personal, gid


def test_private_words_of_the_wedding_goal():
    # Ordinary life words ("engaged", "got") are not
    # personal details; they stay out of search words but are not "Kept private".
    assert needs_rules.hard_words(_goals()["wedding"]["text"]) == ["brother"]
    jobs = needs_rules.rule_needs(_goals()["wedding"]["text"])["jobs"]
    assert not any("engaged" in p for j in jobs for p in j["search"])


CHESS = ("I want a chess coach bot for my grandson Theo. He is 8 and just joined the chess club "
         "at his school in Fresno. I want to play practice games against it on a real board, work "
         "through chess puzzles, learn the openings, and keep a record of his tournament games.")
STUDY = ("I'm in nursing school at Ohio State and my big pharmacology exam is on November 14. My "
         "roommate Priya and I want a study buddy bot with a pomodoro timer, flashcards for drug "
         "names, a to-do list for our assignments, and a way to track how many hours we studied "
         "each week.")


def test_a_verb_the_list_does_not_know_is_not_glued_to_the_last_verb():
    names = [j["name"] for j in needs_rules.rule_needs(CHESS)["jobs"]]
    assert names == ["Play practice games against it on a real board", "Work through chess puzzles",
                     "Learn the openings", "Keep a record of his tournament games"]


def test_ordinary_words_are_not_private():
    assert "opening" not in needs_rules.hard_words(CHESS)
    assert "openings" not in needs_rules.hard_words(CHESS)
    learn = [j for j in needs_rules.rule_needs(CHESS)["jobs"] if j["name"] == "Learn the openings"]
    assert learn and any("opening" in p for p in learn[0]["search"])


def test_small_joining_words_never_become_search_words():
    for job in needs_rules.rule_needs(CHESS)["jobs"]:
        for phrase in job["search"]:
            assert not set(phrase.split()) & {"through", "against", "it", "on"}, phrase


def test_a_bot_with_a_list_of_things():
    names = [j["name"] for j in needs_rules.rule_needs(STUDY)["jobs"]]
    assert names == ["Pomodoro timer", "Flashcards for drug names",
                     "To-do list for our assignments", "Track how many hours we studied each week"]


def test_helps_me_and_has_are_lead_ins_not_jobs():
    goal = ("I want a bot that helps me track initiative and hit points, roll dice. "
            "I want a bot that keeps our setlists, has a metronome and tuner, and schedules gigs.")
    names = [j["name"] for j in needs_rules.rule_needs(goal)["jobs"]]
    assert "Track initiative and hit points" in names
    assert "Metronome and tuner" in names
    assert not any(n.lower().startswith(("helps", "tracks has")) for n in names)


def test_an_item_with_only_held_words_is_still_counted():
    goal = ("I want a bot that helps with recipes and costing, taking custom cake orders, "
            "and posting to Instagram.")
    marks = needs_rules.personal_words(goal)
    marks["instagram"] = "held:brand"
    jobs = needs_rules.rule_needs(goal, personal=marks)["jobs"]
    assert [j["name"] for j in jobs][-1] == "Posting to Instagram"
    assert jobs[-1]["search"] == []


# -- with the real screen -----------------------------------
def _run_rules(goal: str) -> dict:
    from puffpai.wizard import needs
    return needs.run(goal, cfg={})          # no brain: the rules only, nothing sent


def _out(job: dict) -> list[str]:
    return [s["phrase"] for p in job["parts"] for s in p["search"] if s["state"] == "out"]


def test_every_listed_job_of_the_12_goals_is_still_searched_for():
    # Without this, "Learn the openings" keeps its name but loses every
    # search phrase ("opening" is held as personal), so it is never looked for.
    pytest.importorskip("puffpai.security.outbound_query",
                        reason="the outbound screen is not in this build")
    for gid, g in _goals().items():
        got = _run_rules(g["text"])
        private = {h["word"].lower() for h in got["held"]} | set(got["private_left_out"])
        for job in got["jobs"]:
            words = needs_rules.item_words(job["name"], {})
            if job["source"] == "asked" and [w for w in words if w.lower() not in private]:
                assert _out(job), (gid, job["name"])


def test_learn_the_openings_is_searched_for():
    pytest.importorskip("puffpai.security.outbound_query",
                        reason="the outbound screen is not in this build")
    got = _run_rules(CHESS)
    learn = [j for j in got["jobs"] if j["name"] == "Learn the openings"]
    assert learn and any("opening" in p for p in _out(learn[0]))
    assert "opening" not in got["private_left_out"]


def test_a_job_whose_phrases_all_hold_a_private_word_is_searched_by_the_rest():
    pytest.importorskip("puffpai.security.outbound_query",
                        reason="the outbound screen is not in this build")
    got = _run_rules(STUDY)
    cards = [j for j in got["jobs"] if j["name"] == "Flashcards for drug names"][0]
    out = _out(cards)
    assert out and all("flashcard" in p for p in out)
    held = {h["word"] for h in got["held"]}
    for phrase in out:
        assert not set(phrase.split()) & held, phrase
