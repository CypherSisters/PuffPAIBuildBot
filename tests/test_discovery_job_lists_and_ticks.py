"""Discovery: job lists and ticks.

Goal-agnostic rules, each proven on made-up sentences and finds (no network,
no model):

* every job she asked for survives as a job with a readable heading: lists
  after a colon, after "we'd", after "a <bot> for", after "that shows", "I'd
  also like to ...", "X would really help"; "Dr." does not end a sentence; an
  example ("like ..."), a relative clause about a name and the end of a place
  name are not jobs; "names characters" keeps its own verb;
* every job part gets real search phrases (no grammar words, no verb forms),
  and the acceptance harness says "nothing integrable" only when every part
  was searched;
* a find the brain twice says was made for something else is left out; a
  strong trusted line the judge alone refused is ticked on a second "yes";
* a whole-plan app is filed under the whole-plan job;
* pre-ticks: never stale, another kind, a Home Assistant add-on without Home
  Assistant, or an outside AI service; at most one app per job;
* plan text: pictures by title and site, server apps say BuildBot cannot set
  them up yet, places named by what they share, the looking lines go forward;
* the build record agrees with the plan about books.
"""
from __future__ import annotations

import json
import re
import types
from pathlib import Path

import numpy as np
import pytest

from puffpai.catalogs import meaning, needs_rules
from puffpai.wizard import find_parts, needs, proposal, service, wording

ROOT = Path(__file__).resolve().parents[1]


def _names(goal: str) -> list[str]:
    return [needs_rules.job_name(i) for i in needs_rules.explicit_items(goal)]


# -- her list, every shape ----------------------------------------------------------------
def test_a_list_after_a_colon_is_read():
    got = _names("We need a homework helper bot: spelling lists, a times-table quiz, and "
                 "something to track reading minutes.")
    assert got == ["Spelling lists", "Times-table quiz", "Track reading minutes"]


def test_a_list_after_we_would_is_read():
    got = _names("We'd log every hike, rate each trail, and see a map of where we went.")
    assert got == ["Log every hike", "Rate each trail", "See a map of where we went"]


def test_a_list_after_a_bot_for_is_read_but_not_who_it_is_for():
    got = _names("I'd like a choir bot for our sheet music, who sings which part, a pitch "
                 "pipe, and concert dates.")
    assert "Who sings which part" in got and "Concert dates" in got and len(got) == 4
    assert _names("I want a chess bot for my grandson Theo.") == []


def test_what_a_screen_shows_is_one_job_each():
    got = _names("I want a screen in the hall that shows the weather, the bus times, and "
                 "whether the back gate is locked.")
    assert got == ["Shows the weather", "Shows the bus times",
                   "Shows whether the back gate is locked"]


def test_i_would_also_like_to_and_would_help_are_read():
    got = _names("I'd also like to see which plants need water. My husband travels, so "
                 "reminders would really help.")
    assert got == ["See which plants need water", "Reminders"]


def test_a_title_with_a_full_stop_does_not_end_the_sentence():
    got = _names("I need help keeping track of my blood pressure, my visits with Dr. Lee "
                 "in town, and what I eat each day.")
    assert got == ["Keeping track of my blood pressure",
                   "Keeping track of my visits with Dr. Lee in town",
                   "Keeping track of what I eat each day"]


def test_a_second_verb_in_the_list_keeps_its_own_place():
    got = _names("I want a game bot that rolls dice, deals cards, names villages, and keeps "
                 "score.")
    assert "Names villages" in got and not any("Deals names" in g for g in got)


def test_an_example_a_relative_clause_and_a_place_tail_are_not_jobs():
    assert _names("I want to sew costumes, like the dragon one my nephew wants.") == [
        "Sew costumes"]
    assert _names("I want to find out about my great-aunt Rosa Lindqvist, who sailed from "
                  "Norway and settled in Duluth, Minnesota.") == [
        "Find out about my great-aunt Rosa Lindqvist"]


def test_numbers_and_and_between_things_do_not_split():
    got = _names("I want it to pay off $4,500 of debt and track fuel and repairs.")
    assert got == ["Pay off $4,500 of debt", "Track fuel and repairs"]


def test_and_before_a_second_doing_word_splits():
    got = _names("I'd like help organizing my notes and figuring out where to look next.")
    assert got == ["Organizing my notes", "Figuring out where to look next"]


def test_from_your_words_counts_her_list_with_no_brain(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    lines: list[str] = []
    got = needs.run("We need a homework helper bot: spelling lists, a times-table quiz, and "
                    "something to track reading minutes.", cfg={}, on_line=lines.append)
    assert "From your words: 3 things" in lines
    assert [j["source"] for j in got["jobs"]] == ["asked"] * 3


# -- real search phrases for every part ------------------------------------------------------
@pytest.mark.parametrize("item,want", [
    ("Organizing what I already know", []),
    ("Figuring out where to look for ship and census records", ["ship", "census", "record"]),
    ("Who has learned which songs", ["song"]),
    ("Tell us what we can see on clear nights", ["clear", "night"]),
    ("Keeping track of what I should be eating each week", []),
    ("Plan the baking schedule", ["baking", "schedule"]),
])
def test_search_words_are_things_not_grammar(item, want):
    assert needs_rules.item_words(item, {}) == want


def test_an_empty_part_borrows_its_jobs_phrases_then_the_kind():
    jobs = [{"name": "Find out more about my grandfather", "parts": [
                {"kind": "data-template", "search": ["family tree template"]},
                {"kind": "code-app", "search": []}]},
            {"name": "Organizing what I already know", "parts": [
                {"kind": "code-app", "search": []}]},
            {"name": "Reminders", "parts": [{"kind": "code-app", "search": ["reminder"]}]},
            {"name": "Odd one", "parts": []}]
    needs._fill_parts(jobs, "genealogy", {})
    assert jobs[0]["parts"][1]["search"] == ["family tree"]
    assert jobs[1]["parts"][0]["search"] == ["genealogy"]
    assert jobs[2]["parts"][0]["search"] == ["genealogy reminder"]
    assert jobs[3]["parts"] == [{"kind": "code-app", "search": ["genealogy odd"]}]


def test_a_job_with_only_a_book_part_that_is_not_about_learning_is_searched_as_apps():
    out = needs._finish([{"name": "Driving tips", "does": "", "source": "usual",
                          "parts": [{"kind": "knowledge", "search": ["safe driving"]}]}],
                        "car maintenance")
    assert out[0]["parts"] == [{"kind": "code-app", "search": ["safe driving"]}]


def _harness():
    import importlib.util
    spec = importlib.util.spec_from_file_location("r3_acceptance_t",
                                                  ROOT / "scripts" / "measure_goals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nothing_integrable_only_when_every_part_was_searched():
    h = _harness()
    r = {"needs": {"jobs": [
            {"id": "j1", "ticked": True, "parts": [
                {"kind": "code-app", "search": [{"phrase": "family tree", "state": "out"}]}]},
            {"id": "j2", "ticked": True, "parts": [{"kind": "code-app", "search": []}]}]},
         "plan": {"items": [], "not_reached": []}, "evidence": {"finds": []}, "bar": {}}
    proof = h.failure_proof(r)
    assert proof["verdict"].startswith("not proven: 1 job parts were never searched")
    assert proof["proven"] is False and proof["unsearched_parts"] == ["j2:code-app"]
    r["needs"]["jobs"][1]["parts"][0]["search"] = [{"phrase": "genealogy", "state": "out"}]
    assert h.failure_proof(r)["verdict"].startswith("nothing integrable found")
    r["plan"]["not_reached"] = ["j2"]
    assert h.failure_proof(r)["unsearched_parts"] == ["j2:code-app"]


# -- made for something else, said twice; a lone "no" on a strong line ---------------------------
def _row(fid, *, job="j1", fit, state="might_help", judge="yes", what="A tool for the job.",
         trust=None, part="code-app", source="github"):
    return {"id": fid, "name": fid.split("/")[-1], "what_it_is": what, "part": part,
            "source": source, "mode_guess": "static_web", "integrable": True,
            "repo": {"license_spdx": "MIT"}, "licence": "MIT",
            "trust": trust or {"floor_ok": True, "pretick_ok": True},
            "meaning": {"job": job, "fit": fit, "state": state, "judge": judge,
                        "why_dropped": None}}


def _ask(word):
    def ask(messages, format=None):
        return json.dumps({"answers": {"1": word}})
    return ask


JOBS = [{"id": "j1", "name": "Reminds me to water", "source": "asked"}]


def test_two_answers_that_it_was_made_for_something_else_leave_a_strong_line_out():
    row = _row("github:x/garden", fit=0.58, judge="no", what="")
    meaning.purpose_check([row], JOBS, _ask("no"), kind="garden")
    assert row["meaning"]["state"] == "dropped"
    assert row["meaning"]["why_dropped"] == meaning.MADE_FOR_ELSE


def test_a_purpose_no_unticks_a_tick():
    row = _row("github:x/notes", fit=0.62, state="ticked")
    meaning.purpose_check([row], JOBS, _ask("no"), kind="garden")
    assert row["meaning"]["state"] == "might_help"


def test_a_lone_judge_no_on_a_strong_trusted_card_is_ticked_on_a_second_yes():
    card = _row("api_seeds:recalls", fit=0.68, judge="no", part="live-info",
                source="api_seeds", what="Safety recalls for a car, by make, model and year.")
    meaning.purpose_check([card], [{"id": "j1", "name": "Look up any recalls",
                                    "source": "asked"}], _ask("yes"), kind="car maintenance")
    assert card["meaning"]["state"] == "ticked" and card["meaning"]["second_opinion"]


def test_no_second_opinion_without_trust_or_strength():
    weak = _row("api_seeds:a", fit=0.52, judge="no", part="live-info", source="api_seeds")
    untrusted = _row("api_seeds:b", fit=0.70, judge="no", part="live-info", source="api_seeds",
                     trust={"floor_ok": True, "pretick_ok": False})
    meaning.purpose_check([weak, untrusted], JOBS, _ask("yes"), kind="garden")
    assert weak["meaning"]["state"] == "might_help"
    assert untrusted["meaning"]["state"] == "might_help"


def test_a_find_with_no_description_is_judged_by_its_readme():
    row = {"name": "garden", "what_it_is": "",
           "details": {"feature_lines": ["A FAIR AI/ML model publishing framework.",
                                         "Publish models with a DOI."]}}
    said = meaning.what_it_does(row)
    assert "model publishing framework" in said
    seen: list = []
    meaning.judge([dict(row, id="github:g/garden", part="code-app", source="github",
                        meaning={"job": "j1", "fit": 0.45, "state": "might_help"})],
                  JOBS, lambda m, format=None: (seen.append(m), "{}")[1], kind="garden")
    assert "publishing framework" in seen[0][1]["content"]


# -- a whole-plan app serves the whole-plan job ---------------------------------------------------
WEDDING = [{"id": "j1", "name": "Track themes from pictures"},
           {"id": "j2", "name": "Plan the budget"},
           {"id": "j3", "name": "Find vendors"},
           {"id": "j4", "name": "Do a whole wedding plan"}]


def test_a_broad_find_is_filed_under_the_whole_plan_job():
    fits = np.array([0.59, 0.53, 0.44, 0.56])
    assert meaning._whole_job(WEDDING, [0, 1, 2, 3], fits) == 3


def test_a_focused_find_stays_where_it_fits():
    fits = np.array([0.15, 0.62, 0.18, 0.17])
    assert meaning._whole_job(WEDDING, [0, 1, 2, 3], fits) is None
    assert meaning._whole_job(WEDDING[:3], [0, 1, 2], np.array([0.59, 0.55, 0.54])) is None


def test_the_purpose_question_never_moves_a_whole_plan_app():
    row = _row("github:x/wedding-planner", job="j4", fit=0.60)
    row["meaning"].update(whole=True, best_job="j1", best_jobs=["j1", "j4"])
    asked: list = []

    def ask(messages, format=None):
        asked.append(messages[1]["content"])
        return json.dumps({"answers": {"1": "yes"}})
    meaning.purpose_check([row], [dict(j, source="asked") for j in WEDDING], ask,
                          kind="wedding planner")
    assert row["meaning"]["job"] == "j4"
    assert all("whole wedding plan" in q for q in asked)


# -- pre-ticks she can trust ---------------------------------------------------------------------
@pytest.mark.parametrize("row,kind,ha,want", [
    ({"name": "notes", "what_it_is": "DEPRECATED) Organized daily notes"}, "game master",
     False, "stale"),
    ({"name": "x", "what_it_is": "Tracks things", "repo": {"archived": True}}, "garden", False,
     "stale"),
    ({"name": "printease", "what_it_is": "Easy control of your printer."}, "3d printer",
     False, "other kind"),
    ({"name": "slicer", "what_it_is": "A web slicer for your 3D printer."}, "3d printer",
     False, ""),
    ({"name": "dash", "what_it_is": "A dashboard for Home Assistant."}, "home", False,
     "home assistant"),
    ({"name": "dash", "what_it_is": "A dashboard for Home Assistant."}, "home", True, ""),
    ({"name": "coach", "what_it_is": "A fitness log with a DeepSeek-powered coach."}, "fitness",
     False, "outside ai"),
    ({"name": "myfin", "what_it_is": "Personal finance budget app you host yourself."},
     "family budget", False, ""),
])
def test_tick_doubt(row, kind, ha, want):
    assert find_parts.tick_doubt(row, kind, ha=ha) == want


def test_guard_unticks_doubted_finds_and_keeps_one_app_per_job(monkeypatch):
    monkeypatch.setattr(find_parts, "_ha_in_use", lambda: False)
    items = [{"id": "a", "need": "j1", "section": "apps_code", "default_on": True},
             {"id": "b", "need": "j1", "section": "apps_code", "default_on": True},
             {"id": "c", "need": "j2", "section": "apps_code", "default_on": True}]
    rows = {"a": {"name": "a", "what_it_is": "Wall tablet dashboard", "meaning": {"fit": 0.55}},
            "b": {"name": "b", "what_it_is": "Kitchen screen dashboard", "meaning": {"fit": 0.60}},
            "c": {"name": "c", "what_it_is": "Dashboard for Home Assistant",
                  "meaning": {"fit": 0.70}}}
    find_parts._guard_ticks(items, rows, "home")
    assert [i["id"] for i in items if i["default_on"]] == ["b"]
    assert items[2]["doubt"] == "home assistant"


def test_a_doubted_find_is_never_the_best_match(monkeypatch):
    monkeypatch.setattr(find_parts, "_ha_in_use", lambda: False)
    items = [{"id": "a", "need": "j1", "section": "apps_code", "default_on": False,
              "detail": "x"}]
    rows = {"a": {"name": "printease", "what_it_is": "Easy control of your printer.",
                  "meaning": {"state": "might_help", "fit": 0.7, "judge": "yes",
                              "purpose": "yes"}}}
    find_parts._mark_best(items, rows, [{"id": "j1", "name": "Print parts", "source": "asked"}],
                          "3d printer")
    assert not items[0].get("best_match")


# -- plan text -------------------------------------------------------------------------------
def test_pictures_are_named_by_title_and_site_never_an_address():
    job = {"id": "j1", "name": "Track themes"}
    item = find_parts._picture_item({"id": "openverse:1", "name": "garden wedding decor",
                                     "url": "https://www.flickr.com/photos/1594@N08/2662404",
                                     "source": "openverse", "part": "pictures"}, job)
    assert item["label"] == "garden wedding decor (a picture on Flickr)"
    bare = find_parts._picture_item({"id": "openverse:2", "name": "www.flickr.com/photos/1/22",
                                     "url": "https://www.flickr.com/photos/1/22",
                                     "source": "openverse", "part": "pictures"}, job)
    assert bare["label"] == "A picture on Flickr"


def test_a_server_app_says_buildbot_cannot_set_it_up_yet():
    assert "not something BuildBot can set up yet" in meaning.NOTE_SERVER
    assert "not something BuildBot can set up yet" in find_parts.NOTE_REASONS_MANY[
        meaning.NOTE_SERVER]


def test_places_are_named_by_what_they_share():
    said = find_parts.plain_places(["GitHub", "Codeberg", "Wikimedia Commons", "Openverse",
                                    "Hugging Face datasets", "npm"])
    assert said == ("sites where people share free apps and code (GitHub, Codeberg and npm), "
                    "free pictures (Wikimedia Commons and Openverse) and free data "
                    "(Hugging Face)")
    assert "datasets" not in said


def test_the_footer_says_what_comes_from_each_place():
    assert proposal.host_names(["codeload.github.com", "api.github.com",
                                "upload.wikimedia.org"]) == (
        "GitHub, where the apps come from; Wikimedia Commons, where the pictures come from")


def test_the_looking_lines_go_forward():
    got = service._latest_lines([
        "On this computer: 144 found", "GitHub: 12 found", "Codeberg: 3 found",
        "GitHub: 40 found", "Checking what fits your jobs ...",
        "Checking what fits your jobs ... 7 kept, 452 left out"])
    assert got == ["On this computer: 144 found", "GitHub: 40 found", "Codeberg: 3 found",
                   "Checking what fits your jobs ... 7 kept, 452 left out"]


def test_the_plan_loading_line_is_not_checking_this_computer():
    text = (ROOT / "dashboard" / "app" / "onboarding" / "Conversation.tsx").read_text(
        encoding="utf-8")
    block = text[text.index("if (!summary) {"):][:200]
    assert "Putting your plan together..." in block
    assert "Checking this computer" not in block


def test_the_age_question_says_it_is_optional_first():
    for mode in (wording.STANDARD, wording.SIMPLE):
        said = wording.say("age_question", mode)
        assert said.startswith("How old are you? Only needed if you are under 13")
        assert said.endswith("can leave it empty.") and len(said) <= 120


# -- the build record agrees with the plan about books ------------------------------------------
def test_the_record_adds_no_book_when_no_job_is_about_learning():
    from puffpai.wizard.packages.sources import skill as sources
    shared = {"needs": {"schema": "needs/v1", "jobs": [
        {"id": "j1", "name": "Plan the budget", "ticked": True, "learn": False}]}}
    assert sources._jobs_want_no_books(types.SimpleNamespace(shared=shared))
    shared["needs"]["jobs"].append({"id": "j2", "name": "Learn the dances", "ticked": True,
                                    "learn": True})
    assert not sources._jobs_want_no_books(types.SimpleNamespace(shared=shared))
    assert not sources._jobs_want_no_books(types.SimpleNamespace(shared={}))
    assert re.search(r"No book is added", sources.NO_LEARN_JOB)
