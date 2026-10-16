"""The build foundation over 8 different bots (matrix).

One pipeline for any goal: a scripted brain picks jobs from the fixed lists,
a fake catalog holds the right parts and decoys, and the same rules route
every job. Per goal: the expected route per job, exactly one page per job,
books and text-only skills never set a route unless the job is learn or ask,
decoys end up under "Not used", one setup list, one yes record.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))

from fakes import (ScriptedBrain, api_skill, app, book, cli_skill, concept_embed,  # noqa: E402
                   guide, job, live, mcp)

from puffpai.build import pipeline, schema as S, yes as yes_mod  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

DEVICE = "device:matrix12345678"


@pytest.fixture(autouse=True)
def _tool_servers_can_run(monkeypatch):
    """These bots' tool servers are runnable here (the mcp package is there):
    whether this test computer has it is not what the matrix tests."""
    from puffpai.build import classify
    monkeypatch.setattr(classify, "_mcp_sdk", lambda: True)

GOALS = {
    "MusicBot": {
        "goal": "a music bot that plays radio, makes songs and teaches me music theory",
        "jobs": [job("Listen to music", "Play radio stations and music streams.", "listen",
                     "sound", ["none"], ["music", "radio"]),
                 job("Make songs", "Create new songs from a short description.", "make",
                     "sound", ["none"], ["song", "music"]),
                 job("Learn music theory", "Explain chords, scales and harmony.", "learn",
                     "text", ["none"], ["theory", "chord", "music"])],
        "catalog": [
            live("seed:radio_browser", "Radio Browser",
                 "A free list of internet radio stations and music streams to listen to.",
                 "de1.api.radio-browser.info"),
            cli_skill("clawhub:song-maker", "Song Maker",
                      "Generate songs and music audio from a description with a local model.",
                      ["ffmpeg"], judge_no=["Listen to music", "Learn music theory"]),
            book("book:openstax:music-theory", "Music Theory for Beginners",
                 "A free book that explains chords, scales and harmony."),
            guide("skills.sh:x/brand/playlist-cover-design", "playlist-cover-design",
                  "Design rules for album and playlist cover art in a brand style.",
                  judge_no=["*"]),
        ],
        "routes": {"Listen to music": "C", "Make songs": "B", "Learn music theory": "B"},
        "picks": {"Listen to music": "seed:radio_browser", "Make songs": "clawhub:song-maker",
                  "Learn music theory": "book:openstax:music-theory"},
        "views": {"Listen to music": "player"},
        "decoys": ["skills.sh:x/brand/playlist-cover-design"],
        # package: "Install the Song Maker skill" (f-sec: every install make
        # does is a row on the list).
        "setup": {"package", "program", "switch"},
    },
    "StargazerBot": {
        "goal": "a space bot with earth pictures, rocket launches, the sky tonight near me "
                "and space facts",
        "jobs": [job("Show Earth pictures", "Show new photos of Earth from space.", "show",
                     "pictures", ["none"], ["earth", "photo"]),
                 job("Follow rocket launches", "List upcoming rocket launches.", "show",
                     "text", ["time"], ["rocket", "launch"]),
                 job("See the sky tonight", "Show what is in the sky tonight near me.", "show",
                     "places", ["place", "time"], ["sky", "star"]),
                 job("Learn space facts", "Answer questions about space.", "ask", "text",
                     ["none"], ["space", "fact"])],
        "catalog": [
            live("seed:nasa_epic", "NASA EPIC Earth photos",
                 "Daily photos of Earth taken from space.", "epic.gsfc.nasa.gov",
                 judge_no=["Follow rocket launches", "See the sky tonight"]),
            live("seed:ll2", "Launch Library 2", "Upcoming rocket launches and space events.",
                 "ll.thespacedevs.com", judge_no=["Show Earth pictures"]),
            live("seed:visible_planets", "Visible Planets",
                 "Which planets and stars are in the sky tonight from a place near you.",
                 "api.visibleplanets.dev", needs=["location"],
                 judge_no=["Show Earth pictures", "Follow rocket launches"]),
            book("book:openstax:astronomy", "Astronomy",
                 "A free textbook about space, stars and planets."),
            api_skill("clawhub:zhangj85/horoscope-daily-china", "Daily Horoscope Creator",
                      "Create 12 constellation fortune images with TianAPI.",
                      "apis.tianapi.com", judge_no=["*"]),
            guide("skills.sh:luncosim/space-engineering-skills/constellation-design",
                  "constellation-design",
                  "Design and analyze satellite constellations and orbital shells.",
                  judge_no=["*"]),
            guide("skills.sh:compass-brand/constellation/bmad-compass-trigger-mapping",
                  "bmad-compass-trigger-mapping",
                  "Brand trigger mapping for the constellation design system.", judge_no=["*"]),
            guide("skills.sh:phazurlabs/sumi/navigation-pattern-encyclopedia",
                  "navigation-pattern-encyclopedia",
                  "An encyclopedia of navigation patterns for app interfaces.", judge_no=["*"]),
        ],
        "routes": {"Show Earth pictures": "C", "Follow rocket launches": "C",
                   "See the sky tonight": "C", "Learn space facts": "B"},
        "picks": {"Show Earth pictures": "seed:nasa_epic", "Follow rocket launches": "seed:ll2",
                  "See the sky tonight": "seed:visible_planets",
                  "Learn space facts": "book:openstax:astronomy"},
        "views": {"Show Earth pictures": "gallery", "Follow rocket launches": "list",
                  "See the sky tonight": "map"},
        "decoys": ["clawhub:zhangj85/horoscope-daily-china",
                   "skills.sh:luncosim/space-engineering-skills/constellation-design",
                   "skills.sh:compass-brand/constellation/bmad-compass-trigger-mapping",
                   "skills.sh:phazurlabs/sumi/navigation-pattern-encyclopedia"],
        "setup": {"setting", "switch"},
    },
    "PianoBot": {
        "goal": "a piano bot with lessons that can show me the keys for a chord",
        "jobs": [job("Take piano lessons", "Teach piano step by step with lessons.", "learn",
                     "text", ["none"], ["piano", "lesson"]),
                 job("Show keys for a chord", "Show which piano keys make a chord.", "show",
                     "pictures", ["none"], ["piano", "chord", "key"])],
        "catalog": [
            app("github:x/piano-trainer", "Piano Trainer",
                "Browser app with piano lessons that teach you to play step by step.",
                judge_no=["Show keys for a chord"]),
            mcp("mcp:io.example/piano-chords", "Piano Chords MCP",
                "Draws piano keyboard diagrams for any chord.", "https://chords.example.org/mcp",
                [{"name": "chord_diagram",
                  "description": "Return an image of the piano keys for a chord."}],
                judge_no=["Take piano lessons"]),
            guide("clawhub:pianobar-marketing", "Piano bar marketing",
                  "Marketing copy for piano bars and lounges.", judge_no=["*"]),
        ],
        "routes": {"Take piano lessons": "A", "Show keys for a chord": "B"},
        "picks": {"Take piano lessons": "github:x/piano-trainer",
                  "Show keys for a chord": "mcp:io.example/piano-chords"},
        "views": {"Take piano lessons": "app"},
        "decoys": ["clawhub:pianobar-marketing"],
        "setup": {"address"},
    },
    "WeddingBot": {
        "goal": "a wedding bot for the guest list, venues near me and reminders",
        "jobs": [job("Keep the guest list", "Keep track of guests and their answers.", "do",
                     "action", ["none"], ["guest", "wedding"]),
                 job("Find venues near me", "Show wedding venues on a map near me.", "show",
                     "places", ["place"], ["venue", "wedding"]),
                 job("Send reminders", "Remind me of wedding tasks on time.", "remind",
                     "action", ["time"], ["wedding", "task"])],
        "catalog": [
            app("github:x/rsvp-manager", "RSVP Manager",
                "Self-hosted app to keep a wedding guest list and track answers.",
                kind="server_app", judge_no=["Find venues near me", "Send reminders"]),
            live("seed:venues", "Venue Finder",
                 "Wedding venues and event places near a location, on a map.",
                 "api.venues.example", needs=["location"]),
            guide("skills.sh:x/fashion/wedding-dress-design", "wedding-dress-design",
                  "Sketch and design wedding dresses.", judge_no=["*"]),
        ],
        "routes": {"Keep the guest list": "A", "Find venues near me": "C", "Send reminders": "B"},
        "picks": {"Keep the guest list": "github:x/rsvp-manager",
                  "Find venues near me": "seed:venues", "Send reminders": "builtin:routine"},
        "views": {"Find venues near me": "map"},
        "decoys": ["skills.sh:x/fashion/wedding-dress-design"],
        "setup": {"setting", "switch"},
    },
    "GardenBot": {
        "goal": "a garden bot with the weather, watering reminders and plant identification",
        "jobs": [job("Check the weather", "Show the weather forecast for my garden over the week.",
                     "show", "numbers_over_time", ["place"], ["weather", "garden"]),
                 job("Remind me to water", "Remind me to water the plants.", "remind", "action",
                     ["time"], ["watering", "plant"]),
                 job("Identify plants from a photo", "Tell what plant is in a photo.", "do",
                     "text", ["none"], ["plant", "photo"])],
        "catalog": [
            live("seed:open_meteo", "Open-Meteo",
                 "Free weather forecast by the hour and day for any place.", "api.open-meteo.com",
                 needs=["location"]),
            book("book:gutenberg:garden", "The Garden Book",
                 "An old book about growing plants and flowers."),
            app("github:x/plant-filter", "Plant Photo Filters",
                "Add green plant themed filters to your photos for social media.",
                judge_no=["*"]),
        ],
        "routes": {"Check the weather": "C", "Remind me to water": "B",
                   "Identify plants from a photo": "D"},
        "picks": {"Check the weather": "seed:open_meteo", "Remind me to water": "builtin:routine"},
        "views": {"Check the weather": "chart"},
        "decoys": ["github:x/plant-filter"],
        "setup": {"setting", "switch"},
    },
    "RecipeBot": {
        "goal": "a recipe bot that finds recipes and keeps my shopping list",
        "jobs": [job("Find recipes", "Show recipes for a dish or ingredient.", "show", "text",
                     ["none"], ["recipe", "dish"]),
                 job("Keep a shopping list", "Keep the list of things to buy at the store.", "do",
                     "action", ["none"], ["shopping", "grocery"])],
        "catalog": [
            live("seed:themealdb", "TheMealDB",
                 "Free recipes for meals, searchable by dish or ingredient.", "www.themealdb.com"),
            app("github:x/grocery-list", "Grocery List",
                "A simple shopping list app for the things to buy at the store.",
                judge_no=["Find recipes"]),
            book("book:wikibooks:cookbook", "Cookbook",
                 "A free cookbook with recipes and cooking guides."),
            guide("skills.sh:x/ui/recipe-card-design", "recipe-card-design",
                  "Visual design rules for recipe cards in a brand style.", judge_no=["*"]),
        ],
        "routes": {"Find recipes": "C", "Keep a shopping list": "A"},
        "picks": {"Find recipes": "seed:themealdb", "Keep a shopping list": "github:x/grocery-list"},
        "views": {"Find recipes": "list"},
        "decoys": ["skills.sh:x/ui/recipe-card-design"],
        "setup": {"switch"},
        "ingredient": {"Find recipes": "book:wikibooks:cookbook"},
    },
    "HomeBudgetBot": {
        "goal": "a home budget bot that tracks our spending and teaches budgeting",
        "jobs": [job("Track spending over time", "Show my spending by month as a chart.", "show",
                     "numbers_over_time", ["account_key"], ["spending", "budget"]),
                 job("Learn budgeting basics", "Explain how to make a household budget.", "learn",
                     "text", ["none"], ["budget", "money"])],
        "catalog": [
            live("seed:bank_feed", "Open Banking Transactions",
                 "Your bank account transactions and spending history by month.", "api.bank.example",
                 needs=["key"], auth="key", judge_no=["Learn budgeting basics"]),
            book("book:openstax:personal-finance", "Principles of Personal Finance",
                 "A free textbook that teaches the basics of money, budgets and spending."),
            app("github:x/crypto-trader", "Crypto Trader",
                "Automatic crypto coin trading bot for exchanges.", kind="server_app",
                judge_no=["*"]),
        ],
        "routes": {"Track spending over time": "C", "Learn budgeting basics": "B"},
        "picks": {"Track spending over time": "seed:bank_feed",
                  "Learn budgeting basics": "book:openstax:personal-finance"},
        "views": {"Track spending over time": "chart"},
        "decoys": ["github:x/crypto-trader"],
        "setup": {"key", "switch"},
        "one_key": True,
    },
    "StudyBot": {
        "goal": "a study bot with flashcards, questions about my notes and study reminders",
        "jobs": [job("Practice with flashcards", "Learn with flashcards and quizzes.", "learn",
                     "text", ["none"], ["flashcard", "quiz"]),
                 job("Ask about my notes", "Answer questions from my study notes.", "ask", "text",
                     ["none"], ["note", "study"]),
                 job("Study reminders", "Remind me when to study.", "remind", "action", ["time"],
                     ["study"])],
        "catalog": [
            app("github:x/flashcards", "Flashcards",
                "Spaced repetition flashcards app to learn and quiz yourself.",
                judge_no=["Ask about my notes", "Study reminders"]),
            book("book:notes", "Your study notes",
                 "The study notes you learn from, as a book your bot reads to answer questions."),
            guide("skills.sh:x/design/flashcard-visual-template", "flashcard-visual-template",
                  "Visual design template for printed flashcards.", judge_no=["*"]),
        ],
        "routes": {"Practice with flashcards": "A", "Ask about my notes": "B",
                   "Study reminders": "B"},
        "picks": {"Practice with flashcards": "github:x/flashcards",
                  "Ask about my notes": "book:notes", "Study reminders": "builtin:routine"},
        "views": {"Practice with flashcards": "app"},
        "decoys": ["skills.sh:x/design/flashcard-visual-template"],
        "setup": set(),
    },
}


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    consent_tickets.reset()
    from puffpai import deps
    monkeypatch.setattr(deps, "has_program", lambda name: False)
    monkeypatch.setattr(deps, "has_package", lambda kind, name: False)
    from puffpai.build import setup as setup_mod
    monkeypatch.setattr(setup_mod, "_home_set", lambda: False)
    yield tmp_path
    consent_tickets.reset()


def run_goal(name: str, *, use_brain: bool = True, check=None) -> tuple[dict, ScriptedBrain]:
    g = GOALS[name]
    brain = ScriptedBrain({g["goal"]: g["jobs"]}, g["catalog"])
    ctx = {"brain": brain if use_brain else None, "use_brain": use_brain,
           "embed": concept_embed, "catalog": lambda jobs, goal: [dict(c) for c in g["catalog"]],
           "check": check}
    return pipeline.plan(g["goal"], ctx), brain


@pytest.mark.parametrize("name", list(GOALS))
def test_matrix_routes_pages_decoys_setup(state, name):
    g = GOALS[name]
    build, brain = run_goal(name)
    by_name = {j["name"]: j for j in build["jobs"]}
    assert set(by_name) == set(g["routes"]), "every job the brain gave is a job"
    sims = build.get("sims")
    for jname, route in g["routes"].items():
        j = by_name[jname]
        assert j["route"] == route, (name, jname, j["pick"], sims.get(j["id"]))
        if jname in g["picks"]:
            assert j["pick"]["ref"] == g["picks"][jname], (name, jname, j["pick"])
        if jname in g.get("views", {}):
            assert j["view"] == g["views"][jname]
        if route == "D":
            assert j["status"] == S.NOTHING_FOUND and j["reason"] == "Nothing found yet"
    # exactly one page per job
    pages = [j["page"] for j in build["jobs"]]
    assert len(pages) == len(set(pages)) == len(build["jobs"])
    assert all(p == f"job-{j['id']}" for p, j in zip(pages, build["jobs"]))
    # books and text-only skills never set a route unless the job is learn or ask
    for j in build["jobs"]:
        if j["pick"]["class"] in (S.TEXT, S.GUIDE):
            assert j["action"] in ("learn", "ask"), (name, j["name"])
    # decoys dropped, never an ingredient
    dropped = {d["ref"] for d in build["dropped"]}
    used = {r for j in build["jobs"] for r in j["ingredients"]}
    for ref in g["decoys"]:
        assert ref in dropped and ref not in used, (name, ref)
    assert all(d["why"] for d in build["dropped"])
    # one setup list: unique rows, every row tied to a job, the kinds expected
    ids = [r["id"] for r in build["setup"]]
    assert len(ids) == len(set(ids))
    assert all(r["job_ids"] and r["kind"] in S.SETUP_KINDS for r in build["setup"])
    assert {r["kind"] for r in build["setup"]} == g["setup"], build["setup"]
    if g.get("one_key"):
        assert sum(1 for r in build["setup"] if r["kind"] == "key") == 1
    for jname, ref in g.get("ingredient", {}).items():
        assert ref in by_name[jname]["ingredients"] and by_name[jname]["pick"]["ref"] != ref
    # one brain call to interpret, the judge once per job at most (one short
    # question per job, never one call per pair)
    assert brain.calls.count("interpret") == 1
    assert 1 <= brain.calls.count("judge") <= len(build["jobs"])
    # saved, and statuses from the setup list
    assert S.load_build()["jobs"] == build["jobs"]
    for j in build["jobs"]:
        assert j["status"] in (S.NOTHING_FOUND, S.NEEDS_SETUP, S.WAITING_YES)


@pytest.mark.parametrize("name", list(GOALS))
def test_matrix_one_yes_record(state, name, monkeypatch):
    build, _brain = run_goal(name)
    minted: list = []
    real_mint = consent_tickets.mint

    def mint(action, target, sentence, device, **kw):
        minted.append((action, target))
        return real_mint(action, target, sentence, device, **kw)
    monkeypatch.setattr(consent_tickets, "mint", mint)
    audits: list = []
    monkeypatch.setattr(yes_mod, "_audit", lambda result, details: audits.append(details))
    after = yes_mod.give(DEVICE)
    assert len(minted) == 1 and minted[0][0] == "add_on" and minted[0][1].startswith("build:")
    assert len(audits) == 1
    assert after["yes"]["given"] and yes_mod.covers(after)
    # a second tap on the same list records nothing new
    yes_mod.give(DEVICE)
    assert len(minted) == 1


def test_no_brain_still_routes_by_rules(state):
    """No brain: the word table and the sim rules decide (decoys that share
    no subject word or meaning with a job are still dropped)."""
    build, _ = run_goal("WeddingBot", use_brain=False)
    assert build["interpreted_by"] == "words"
    assert build["jobs"], "the goal itself is split into jobs"
    assert all(j["page"] == f"job-{j['id']}" for j in build["jobs"])


def test_retries_then_word_table(state):
    g = GOALS["PianoBot"]
    brain = ScriptedBrain({g["goal"]: g["jobs"]}, g["catalog"], bad_first=2)
    ctx = {"brain": brain, "embed": concept_embed,
           "catalog": lambda jobs, goal: [dict(c) for c in g["catalog"]], "check": None}
    build = pipeline.plan(g["goal"], ctx)
    assert brain.calls.count("interpret") == 3     # first try + 2 retries
    assert build["interpreted_by"] == "brain"
    assert [j["route"] for j in build["jobs"]] == ["A", "B"]


def test_three_bad_answers_fall_back_to_the_word_table(state):
    g = GOALS["PianoBot"]
    brain = ScriptedBrain({g["goal"]: g["jobs"]}, g["catalog"], bad_first=5)
    ctx = {"brain": brain, "embed": concept_embed,
           "catalog": lambda jobs, goal: [dict(c) for c in g["catalog"]], "check": None}
    build = pipeline.plan(g["goal"], ctx)
    assert brain.calls.count("interpret") == 3
    assert build["interpreted_by"] == "words"
    for j in build["jobs"]:
        assert not S.job_problems(j)


def test_a_part_that_fails_its_check_is_left_out_and_the_job_decided_again(state):
    def check(c):
        return {"ok": c["ref"] != "github:x/piano-trainer", "reason": "it reaches too far"}
    build, _ = run_goal("PianoBot", check=check)
    lessons = next(j for j in build["jobs"] if j["name"] == "Take piano lessons")
    assert lessons["pick"]["ref"] != "github:x/piano-trainer"
    gone = {d["ref"]: d["why"] for d in build["dropped"]}
    assert gone["github:x/piano-trainer"].startswith("Drawbridge said no")


def test_find_more_adds_parts_with_its_own_yes(state, monkeypatch):
    build, _ = run_goal("GardenBot")
    yes_mod.give(DEVICE)
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    extra = [app("github:x/plant-id", "Plant ID", "Identify a plant from a photo and tell its "
                 "name.")]
    brain = ScriptedBrain({}, extra)
    got = pipeline.plan_more(plant["id"], {"brain": brain, "embed": concept_embed,
                                           "catalog": lambda jobs, goal: extra, "check": None})
    plant = S.find_job(got, plant["id"])
    assert plant["route"] == "A" and plant["pick"]["ref"] == "github:x/plant-id"
    assert plant["status"] == S.WAITING_YES
    assert not yes_mod.covers(got), "the new part needs its own yes"
    others = [j for j in got["jobs"] if j["id"] != plant["id"]]
    assert all(j["route"] in ("B", "C") for j in others)
    assert len(got.get("yes_history") or []) == 1


# ── a pasted project address (Find more with a URL) ────────────────────────

PASTED_LIB = {"ref": "code:her/plantlib", "full_name": "her/plantlib", "name": "plantlib",
              "url": "https://github.com/her/plantlib", "what": "Plant names from photos.",
              "stars": 3, "language": "Python", "kind": "the project you gave"}


def test_a_pasted_library_becomes_the_jobs_code_and_its_yes_covers_a_make(state):
    from puffpai.build import setup as setup_mod
    run_goal("GardenBot")
    yes_mod.give(DEVICE)
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    got = pipeline.plan_more(plant["id"], {
        "brain": ScriptedBrain({}, []), "embed": concept_embed, "check": None,
        "url": "https://github.com/her/plantlib", "from_url": lambda url: dict(PASTED_LIB)})
    plant = S.find_job(got, plant["id"])
    assert plant["code"][0]["ref"] == "code:her/plantlib"
    assert plant["route"] == "A" and plant["pick"]["ref"] == "code:her/plantlib"
    assert plant["status"] == S.WAITING_YES
    row = next(r for r in got["setup"] if r.get("make") == setup_mod.MAKE_CODE)
    assert plant["id"] in row["job_ids"] and row["refs"] == ["code:her/plantlib"]
    assert row["label"].startswith("If no ready-made app runs, Toolsmith makes one from "
                                   "her/plantlib")
    assert not yes_mod.covers(got), "the changed list needs her one yes"
    yes_mod.give(DEVICE)
    from puffpai.build import make
    assert make.not_listed(S.load_build(), PASTED_LIB, setup_mod.MAKE_CODE) == ""


def test_a_pasted_library_wins_over_a_ready_made_tool(state):
    """Her own pasted project is used even when a tool fits the job (route B):
    the job gets the MAKE row for it."""
    from puffpai.build import setup as setup_mod
    run_goal("MusicBot")
    songs = next(j for j in S.load_build()["jobs"] if j["name"] == "Make songs")
    assert songs["route"] == "B"
    lib = dict(PASTED_LIB, ref="code:her/songlib", full_name="her/songlib",
               url="https://github.com/her/songlib")
    got = pipeline.plan_more(songs["id"], {
        "brain": ScriptedBrain({}, []), "embed": concept_embed, "check": None,
        "url": lib["url"], "from_url": lambda url: dict(lib)})
    songs = S.find_job(got, songs["id"])
    assert songs["route"] == "A" and songs["pick"]["ref"] == "code:her/songlib"
    row = next(r for r in got["setup"] if r.get("make") == setup_mod.MAKE_CODE)
    assert songs["id"] in row["job_ids"] and row["refs"] == ["code:her/songlib"]


def test_a_pasted_app_still_becomes_the_pick(state):
    run_goal("GardenBot")
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    pasted = app("github:her/plant-id", "Plant ID", "Identify a plant from a photo.")
    got = pipeline.plan_more(plant["id"], {
        "brain": ScriptedBrain({}, [pasted]), "embed": concept_embed, "check": None,
        "url": "https://github.com/her/plant-id", "from_url": lambda url: dict(pasted)})
    plant = S.find_job(got, plant["id"])
    assert plant["route"] == "A" and plant["pick"]["ref"] == "github:her/plant-id"
    assert not plant.get("code")


def test_pasting_an_app_the_job_already_tried_tries_it_again_first(state):
    """Live 2026-10-14: re-pasting superdesk on a job whose tried list held it
    said "Added to the list", the job stayed not yet and the Yes said "Nothing
    is waiting to be built". Pasting is "try this": off the tried list (by
    owner/repo, any ref), and the job waits for her Yes."""
    run_goal("GardenBot")
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    pasted = app("github:her/plant-id", "Plant ID", "Identify a plant from a photo.")
    ctx = {"brain": ScriptedBrain({}, [pasted]), "embed": concept_embed, "check": None,
           "url": "https://github.com/her/plant-id", "from_url": lambda url: dict(pasted)}
    pipeline.plan_more(plant["id"], ctx)
    yes_mod.give(DEVICE)

    def gave_up(b):
        j = S.find_job(b, plant["id"])
        j["status"], j["reason"] = S.NOT_YET, "it did not start"
        j["tried"] = [{"ref": "github:her/plant-id", "name": "Plant ID", "why": "no page"},
                      {"ref": "awesome-selfhosted:plant-id", "name": "Plant ID", "why": "x"},
                      {"ref": "github:o/other", "name": "other", "why": "no page"}]
        b["parts"]["awesome-selfhosted:plant-id"] = {
            "ref": "awesome-selfhosted:plant-id", "url": "https://github.com/her/plant-id"}
    S.update_build(gave_up)
    assert plant["id"] not in pipeline.waiting_jobs()
    got = pipeline.plan_more(plant["id"], ctx)
    job = S.find_job(got, plant["id"])
    assert [t["ref"] for t in job["tried"]] == ["github:o/other"]
    assert job["status"] == S.WAITING_YES
    assert plant["id"] in pipeline.waiting_jobs()
    from puffpai.build import make
    assert make.pasted_app(got["parts"]["github:her/plant-id"], job)


# ── a code project added from the Add page (POST /build/project) ─────────────

ISS = {"ref": "code:branjames117/one-small-step", "full_name": "branjames117/one-small-step",
       "name": "one-small-step", "url": "https://github.com/branjames117/one-small-step",
       "what": "ISS tracker and NASA API web app.", "stars": 4, "language": "JavaScript",
       "kind": "the project you gave"}


def _project_ctx(cand: dict) -> dict:
    return {"brain": ScriptedBrain({}, []), "embed": concept_embed, "check": None,
            "from_url": lambda url: dict(cand)}


def test_a_project_no_job_fits_becomes_one_new_job_with_it_first(state):
    """Live 2026-10-14: one-small-step (no licence) had no way in from the
    Add page. Added, it is ONE new job named after it, her project first in
    its code, waiting for her one Yes."""
    from puffpai.build import setup as setup_mod
    run_goal("GardenBot")
    yes_mod.give(DEVICE)
    before = [j["id"] for j in S.load_build()["jobs"]]
    assert pipeline.fits_project(S.load_build(), ISS["name"], ISS["what"]) is None
    got, jid, new = pipeline.plan_project(ISS["url"], ISS["name"], ISS["what"],
                                          ctx=_project_ctx(ISS))
    assert new and jid not in before and [j["id"] for j in got["jobs"]][:-1] == before
    job = S.find_job(got, jid)
    assert job["name"] == "one-small-step: ISS tracker and NASA API web app"
    assert job["code"][0]["ref"] == ISS["ref"]
    assert job["code"][0]["kind"] == "the project you gave"
    assert job["route"] == "A" and job["pick"]["ref"] == ISS["ref"]
    assert job["status"] == S.WAITING_YES and not yes_mod.covers(got)
    row = next(r for r in got["setup"] if r.get("make") == setup_mod.MAKE_CODE)
    assert jid in row["job_ids"]
    assert S.find_job(S.load_build(), jid) is not None


def test_a_job_added_while_find_more_runs_is_kept(state):
    """Find more for one job reads build.json, works, then writes it: a job
    the Add page made meanwhile stays (the bot page lists every job)."""
    run_goal("GardenBot")
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    extra = S.new_job("one-small-step: ISS tracker", "ISS tracker",
                      taken=[j["id"] for j in S.load_build()["jobs"]])

    def from_url(url):
        S.update_build(lambda b: b["jobs"].append(dict(extra)))   # added meanwhile
        return dict(PASTED_LIB)
    ctx = {"brain": ScriptedBrain({}, []), "embed": concept_embed, "check": None,
           "from_url": from_url, "url": PASTED_LIB["url"]}
    got = pipeline.plan_more(plant["id"], ctx)
    assert S.find_job(got, extra["id"]) is not None
    assert S.find_job(S.load_build(), extra["id"]) is not None
    assert S.find_job(S.load_build(), plant["id"])["code"][0]["ref"] == PASTED_LIB["ref"]


def test_a_project_that_fits_a_job_goes_on_that_job(state):
    run_goal("GardenBot")
    build = S.load_build()
    plant = next(j for j in build["jobs"] if j["route"] == "D")
    assert pipeline.fits_project(build, "x", "y", cap=plant["id"])["id"] == plant["id"]
    words = " ".join([plant["name"], plant.get("does") or ""])
    assert pipeline.fits_project(build, "plantapp", words)["id"] == plant["id"]
    got, jid, new = pipeline.plan_project(PASTED_LIB["url"], "plantlib", words, plant["id"],
                                          ctx=_project_ctx(PASTED_LIB))
    assert jid == plant["id"] and not new and len(got["jobs"]) == len(build["jobs"])
    job = S.find_job(got, jid)
    assert job["code"][0]["ref"] == PASTED_LIB["ref"] and job["status"] == S.WAITING_YES


def test_a_project_that_cannot_be_read_leaves_no_new_job(state):
    run_goal("GardenBot")
    before = [j["id"] for j in S.load_build()["jobs"]]
    ctx = {"brain": ScriptedBrain({}, []), "embed": concept_embed, "check": None,
           "from_url": lambda url: None}
    with pytest.raises(ValueError):
        pipeline.plan_project("https://example.org/x", "x", "y", ctx=ctx)
    assert [j["id"] for j in S.load_build()["jobs"]] == before


def test_a_long_project_line_makes_a_short_job_name():
    name = pipeline.project_job_name("tracker", "A very long line " * 10)
    assert len(name) <= 80 and name.startswith("tracker: A very long line")
    assert pipeline.project_job_name("tracker", "") == "tracker"


def test_a_bad_address_gives_one_plain_line(state):
    run_goal("GardenBot")
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    with pytest.raises(ValueError) as err:
        pipeline.plan_more(plant["id"], {"brain": ScriptedBrain({}, []), "embed": concept_embed,
                                         "check": None, "url": "https://example.org/x"})
    msg = str(err.value)
    assert msg.startswith("BuildBot could not read that project.")
    assert "\n" not in msg and "—" not in msg
