"""Best-match and find-step rules.

Goal-agnostic rules, each proven on made-up finds (no network, no model):

* the best match needs meaning relevance to the job's purpose, fits the
  job's kind (a picture only for a picture job), and is left empty rather
  than filled with a weak fit;
* jobs she asked for come first (searching, filing, best match), and a job
  the brain added is never the only source of addable finds;
* same-kind finds of one job are deduped, at most one is pre-ticked, and the
  one ticked is a form a group can see;
* the looking step says each place's line as it answers;
* the needs step: the brain's restatements of her items are hers, "most
  <kind> bots have this" jobs are counted alone, life news is kept private;
* plan wording: one note line per reason, plain "Similar projects" lines,
  sizes and host names read the same everywhere;
* the done screen fits a browser she is already in.
"""
from __future__ import annotations

import copy
import json
import types

import numpy as np
import pytest

from puffpai.catalogs import meaning, needs_rules
from puffpai.wizard import find_parts, needs, proposal
from tests.test_find_parts_plan_items import (GOAL, FakeWhere, _job, _needs, lanes,  # noqa: F401
                                      repo_find)


# -- the purpose question ---------------------------------------------------------------
def _row(fid, *, job, fit, state="might_help", judge="yes", part="code-app", source="github",
         what="A tool that does the job."):
    return {"id": fid, "name": fid.split(":")[-1], "what_it_is": what, "part": part,
            "source": source,
            "meaning": {"job": job, "fit": fit, "state": state, "judge": judge,
                        "why_dropped": None}}


def _answer(word):
    def ask(messages, format=None):
        return json.dumps({"answers": {"1": word(messages) if callable(word) else word}})
    return ask


JOBS = [{"id": "j1", "name": "Tracks vaccines and medicine", "source": "asked"},
        {"id": "j2", "name": "Medicine tracker", "source": "usual"}]


def test_purpose_no_leaves_out_a_moderate_line_and_keeps_a_strong_one():
    weak = _row("github:x/vaccine-slot-checker", job="j1", fit=0.39)
    strong = _row("github:x/pet-meds", job="j1", fit=0.62)
    meaning.purpose_check([weak, strong], JOBS, _answer("no"), kind="pet")
    assert weak["meaning"]["state"] == "dropped"
    assert weak["meaning"]["why_dropped"] == meaning.MADE_FOR_ELSE
    assert strong["meaning"]["state"] == "might_help"      # a strong line stays
    assert strong["meaning"]["purpose"] == "no"            # but is never the best match


def test_purpose_question_names_the_kind_of_bot_and_the_job():
    seen = []
    row = _row("github:x/pet-meds", job="j1", fit=0.62)
    meaning.purpose_check([row], JOBS, _answer(lambda m: seen.append(m) or "yes"), kind="pet")
    user = seen[0][1]["content"]
    assert '"pet bot"' in user and "Tracks vaccines and medicine" in user
    assert row["meaning"]["purpose"] == "yes"


def test_purpose_asks_nothing_after_its_stop_time_and_an_error_changes_nothing():
    row = _row("github:x/a", job="j1", fit=0.45)
    meaning.purpose_check([row], JOBS, _answer("no"), kind="pet", stop_at=0.0)
    assert "purpose" not in row["meaning"] and row["meaning"]["state"] == "might_help"

    def broken(messages, format=None):
        raise RuntimeError("brain gone")
    meaning.purpose_check([row], JOBS, broken, kind="pet")
    assert row["meaning"]["state"] == "might_help"


def test_best_candidates_are_strong_judged_and_under_her_jobs():
    rows = [_row("github:a/ok", job="j1", fit=0.60),
            _row("github:a/weak", job="j1", fit=0.45),
            _row("github:a/unjudged", job="j1", fit=0.70, judge=None),
            _row("github:a/usual", job="j2", fit=0.80),
            _row("github:a/silent", job="j1", fit=0.66, what="")]
    ids = [r["id"] for r in meaning.best_candidates(rows, JOBS)]
    assert ids == ["github:a/ok"]


# -- pictures only for a picture job ------------------------------------------------------
@pytest.mark.parametrize("name,does,want", [
    ("Track themes from pictures", "", True),
    ("Mood board", "collect colours and styles", True),
    ("Figure out what a warning light means", "identify meanings of warning lights", False),
    ("Toy and activity suggestions", "suggests toys", False),
    ("Chunky patterns that aren't too fiddly", "find chunky knitting patterns", False),
    ("Reminds my daughter to walk her", "", False),
])
def test_wants_pictures(name, does, want):
    assert meaning.wants_pictures({"name": name, "does": does}) is want


def test_a_stock_picture_is_not_eligible_for_a_job_a_picture_cannot_do():
    pic = {"id": "openverse:1", "part": "pictures", "source": "openverse"}
    jobs = [{"name": "Figure out what a warning light means"},
            {"name": "Track themes from pictures"}]
    assert meaning._eligible(pic, jobs) == [1]


# -- her jobs first when filing -------------------------------------------------------------
def test_a_find_that_fits_her_job_nearly_as_well_is_filed_under_hers(monkeypatch):
    monkeypatch.setattr(meaning, "helper_state", lambda: "ready")
    jobs = [{"id": "j1", "name": "Recipes and costing", "source": "asked", "ticked": True},
            {"id": "j2", "name": "Recipe database", "source": "usual", "ticked": True},
            {"id": "j3", "name": "Posting online", "source": "asked", "ticked": True}]
    row = {"id": "github:a/recipe-cost", "name": "recipe-cost", "what_it_is": "Recipe cost card",
           "part": "code-app", "source": "github", "integrable": False}
    # blurbs: usual 0.62, hers 0.59 (within the margin), the other 0.1
    vec = {"job": np.eye(3, dtype=np.float32)}
    cand = np.array([[0.59, 0.62, 0.10]], dtype=np.float32)
    monkeypatch.setattr(meaning, "embed", lambda texts: vec["job"])
    monkeypatch.setattr(meaning, "_candidate_vectors", lambda found: cand)
    out = meaning.score([row], jobs, kind="bakery", stage=1)
    assert out[0]["meaning"]["job"] == "j1"


# -- the best match on the plan -----------------------------------------------------------------
def _item(iid, need, *, section="apps_code", on=False):
    return {"id": iid, "need": need, "section": section, "default_on": on, "detail": "What it is."}


def _mrow(fit, *, judge="yes", purpose="yes"):
    return {"meaning": {"state": "might_help", "fit": fit, "judge": judge, "purpose": purpose}}


PLAN_JOBS = [{"id": "j1", "name": "Plan the budget", "source": "asked"},
             {"id": "j2", "name": "Track themes from pictures", "source": "asked"},
             {"id": "j3", "name": "Secure login", "source": "usual"},
             {"id": "j4", "name": "Come up with ideas", "source": "asked"}]


def test_best_match_needs_both_yeses_and_a_strong_fit():
    items = [_item("a", "j1"), _item("b", "j1"), _item("c", "j1")]
    rows = {"a": _mrow(0.70, purpose="no"), "b": _mrow(0.55), "c": _mrow(0.45)}
    find_parts._mark_best(items, rows, PLAN_JOBS)
    assert [i["id"] for i in items if i.get("best_match")] == ["b"]
    assert items[1]["detail"].startswith(find_parts.BEST_MATCH)


def test_no_best_match_rather_than_a_weak_one():
    # The top
    # fit of a job is marked; a weak one (under MIGHT_FIT) or one the brain
    # said no to never is.
    items = [_item("a", "j1"), _item("b", "j1")]
    rows = {"a": _mrow(0.38), "b": _mrow(0.60, judge="no", purpose="unsure")}
    find_parts._mark_best(items, rows, PLAN_JOBS)
    assert not any(i.get("best_match") for i in items)
    rows["b"] = _mrow(0.60, purpose="unsure")
    find_parts._mark_best(items, rows, PLAN_JOBS)
    assert [i["id"] for i in items if i.get("best_match")] == ["b"]


def test_best_match_on_every_job_with_a_fit_but_never_a_picture():
    # A single stock picture is never the best match, even for a picture job
    # (the marker once sat on an all-caps blog banner). A
    # job the brain added gets one too (every job with a fit).
    items = [_item("a", "j3"), _item("p", "j4", section="pictures"),
             _item("q", "j2", section="pictures")]
    rows = {"a": _mrow(0.8), "p": _mrow(0.8), "q": _mrow(0.7)}
    find_parts._mark_best(items, rows, PLAN_JOBS)
    assert [i["id"] for i in items if i.get("best_match")] == ["a"]


def test_best_match_never_where_something_is_ticked():
    items = [_item("t", "j1", on=True), _item("a", "j1")]
    find_parts._mark_best(items, {"t": _mrow(0.9), "a": _mrow(0.8)}, PLAN_JOBS)
    assert not items[1].get("best_match")


# -- a job the brain added is never the only source ---------------------------------------------------
def test_finds_only_under_brain_jobs_are_left_out():
    items = [_item("a", "j3"), _item("b", "j3"),
             {"id": "pictures:buildbot:palette-j2", "need": "j2", "section": "pictures"}]
    rows = {"a": _mrow(0.5), "b": _mrow(0.5)}
    assert find_parts._only_brain_jobs(items, rows, PLAN_JOBS) == 2
    assert [i["id"] for i in items] == ["pictures:buildbot:palette-j2"]
    assert rows["a"]["meaning"]["state"] == "dropped"


def test_brain_job_finds_stay_next_to_a_find_for_her_job():
    items = [_item("a", "j3"), _item("b", "j1")]
    assert find_parts._only_brain_jobs(items, {}, PLAN_JOBS) == 0
    assert len(items) == 2


# -- same kind of thing: shown twice at most, ticked once, a form she can see -----------------------
def test_same_kind_finds_are_deduped_and_ticked_once(monkeypatch):
    monkeypatch.setattr(meaning, "helper_state", lambda: "ready")
    items, rows = [], {}
    for n, (mode, on, fit) in enumerate([("cli_tool", True, 0.70), ("static_web", True, 0.60),
                                         ("static_web", True, 0.55), ("web_build", False, 0.65),
                                         ("static_web", False, 0.52)]):
        iid = f"apps_code:github:a/metronome{n}"
        items.append({"id": iid, "need": "j1", "section": "apps_code", "default_on": on,
                      "facts": {"mode": mode}})
        rows[iid] = {"id": iid, "name": f"metronome{n}", "what_it_is": "A metronome",
                     "meaning": {"state": "ticked" if on else "might_help", "fit": fit}}
    tuner = "apps_code:github:a/tuner"
    items.append({"id": tuner, "need": "j1", "section": "apps_code", "default_on": True,
                  "facts": {"mode": "static_web"}})
    rows[tuner] = {"id": tuner, "name": "tuner", "what_it_is": "A guitar tuner",
                   "meaning": {"state": "ticked", "fit": 0.6}}

    def embed(texts):
        return np.array([[0.0, 1.0] if "tuner" in t else [1.0, 0.0] for t in texts],
                        dtype=np.float32)
    monkeypatch.setattr(meaning, "embed", embed)
    monkeypatch.setattr(meaning, "candidate_text", lambda r: f"{r['name']} {r['what_it_is']}")
    gone = find_parts._same_kind(items, rows)
    metronomes = [i for i in items if "metronome" in i["id"]]
    assert gone == 3 and len(metronomes) == 2
    ticked = [i["id"] for i in metronomes if i["default_on"]]
    # one tick, a web page before the command-line tool that fit better
    assert ticked == ["apps_code:github:a/metronome1"]
    assert any(i["id"] == tuner and i["default_on"] for i in items)   # another kind keeps its tick


def test_same_kind_does_nothing_without_the_reading_helper(monkeypatch):
    monkeypatch.setattr(meaning, "helper_state", lambda: "missing")
    items = [_item("a", "j1", on=True), _item("b", "j1", on=True)]
    assert find_parts._same_kind(items, {"a": {}, "b": {}}) == 0


# -- notes: one line per reason ---------------------------------------------------------------------------
def test_notes_of_one_job_collapse_to_one_line_per_reason(monkeypatch):
    rows = [repo_find("a", f"wedding-planner-{n}", "A wedding planner", licence=None,
                      mode="static_web") for n in range(3)]
    monkeypatch.setattr(meaning, "note_reason", lambda f: meaning.NOTE_NO_LICENCE)
    lines = find_parts._notes(rows)
    assert len(lines) == 1
    assert lines[0].endswith("(no licence, so BuildBot cannot use them)")
    assert "github.com/a/wedding-planner-0, github.com/a/wedding-planner-1 and " in lines[0]


def test_a_single_note_keeps_its_own_line(monkeypatch):
    row = repo_find("a", "planner", "A planner")
    monkeypatch.setattr(meaning, "note_reason", lambda f: meaning.NOTE_SERVER)
    assert find_parts._notes([row]) == [find_parts._note(row)]


def test_not_read_note_is_plain():
    assert meaning.NOTE_NOT_READ == "BuildBot ran out of time to check it"


# -- "Similar projects also have": a plain job or nothing -----------------------------------------------------
@pytest.mark.parametrize("lines,want", [
    (["Manage your guest list and send invitations"], "Manage your guest list and send invitations"),
    (["seating", "Organize guests onto tables"], "Organize guests onto tables"),
    (["Vendor Management: Organize and communicate with vendors"],
     "Organize and communicate with vendors"),
    (["Automatically track new songs"], "Track new songs"),
    (["Notes from a meeting"], ""),
    (["Sync Providers"], ""),
    (["A very long line that says far too many things for one plain job"], ""),
    (["GUEST LIST"], ""),
    (["guest list manage send"], ""),
    (["The artist name"], ""),
    (["T Tap tempo"], ""),
])
def test_similar_names_are_the_authors_own_short_line(lines, want):
    assert find_parts._similar_name(lines) == want


def test_similar_leaves_out_lines_about_how_it_is_made():
    assert find_parts._DEV_WORDS.search("Open source event ticketing")
    assert find_parts._DEV_WORDS.search("Self-hosted guest manager")


# -- sizes and host names ----------------------------------------------------------------------------------------
def test_sizes_read_like_the_plan_total():
    from puffpai.wizard import cards
    assert find_parts._size_words(560128) == f"About {cards.format_bytes(560128)}."
    assert find_parts._size_words(560128) == "About 547 kB."


def test_the_plan_names_github_not_its_download_host():
    assert proposal.host_name("codeload.github.com") == "GitHub, where the apps come from"
    assert proposal.host_name("codeberg.org") == "Codeberg, where the apps come from"
    assert proposal.host_name("example.org") == "example.org"


# -- the looking step: each place's line as it answers -----------------------------------------------------------
def test_each_place_says_its_line_while_searching(lanes, monkeypatch):
    monkeypatch.setattr(find_parts, "SEARCH_WORKERS", 1)
    budget_job = _job("j1", "Plan the budget", "Plans what each part costs.", "asked",
                      ["wedding budget"])
    app = repo_find("acme", "wedding-budget", "A wedding budget planner.")
    lanes(FakeWhere(online={"j1": [app]}))
    said: list[str] = []
    find_parts.plan_items(_needs([budget_job]), goal=GOAL, online=True, allow_send=True, cfg={},
                          on_line=said.append)
    assert said[0].startswith("On this computer:")
    assert "GitHub: 1 found" in said
    start = said.index(find_parts.LINE_CHECKING_START)
    assert said.index("GitHub: 1 found") < start
    assert said[-1].startswith("Checking what fits your jobs ... ") and said[-1].endswith("left out")


def test_place_names_are_plain():
    assert find_parts.place_name("github") == "GitHub"
    assert find_parts.place_name("commons") == "Wikimedia Commons"
    assert find_parts.place_name("something-new") == "Other places"


def test_pictures_are_not_searched_for_a_job_a_picture_cannot_do(lanes):
    job = _job("j1", "Figure out what a warning light means", "", "asked", ["warning light"],
               ("code-app", "pictures"))
    where = lanes(FakeWhere())
    find_parts.plan_items(_needs([job]), goal=GOAL, online=True, allow_send=True, cfg={})
    kinds = [c[2] for c in where.calls if c[0] == "online" and c[1] == "j1"]
    assert "pictures" not in kinds and "code-app" in kinds


# -- the needs step -----------------------------------------------------------------------------------------------------
def _model(asked, also, jobs):
    return {"kind": "dungeons and dragons campaign", "asked": asked, "also_needed": also,
            "jobs": [{"name": n, "does": d, "parts": [{"kind": "code-app", "search": [p]}]}
                     for n, d, p in jobs]}


def _rule(name):
    return {"name": name, "does": "", "source": "asked",
            "parts": [{"kind": "code-app", "search": [name.lower()]}]}


def test_a_restatement_of_her_item_joins_it_and_is_never_a_usual_job(monkeypatch):
    def sims(a, b):
        # simple word overlap is enough to tell restatements from new jobs here
        def w(t):
            words = {x.strip(":").rstrip("s") for x in t.lower().split()}
            return words - {"dungeon", "and", "dragon", "campaign", "the"}
        return [[len(w(x) & w(y)) / max(1, len(w(x) | w(y))) for y in b] for x in a]
    monkeypatch.setattr(needs, "_sims", sims)
    monkeypatch.setattr(needs, "FOLD", 0.25)
    rule = [_rule("Comes up with NPCs and loot"), _rule("Rolls dice")]
    model = _model(["comes up with npcs and loot", "rolls dice", "tracks the weather"],
                   ["player character sheets", "session scheduler"],
                   [("NPC loot generator", "makes npcs and loot", "npc generator"),
                    ("Dice roller", "rolls dice", "dice roller"),
                    ("Weather tracker", "tracks the weather", "weather tracker"),
                    ("Player character sheets", "keeps character sheets", "character sheet"),
                    ("Session scheduler", "plans sessions", "session planner")])
    out = needs._merge(rule, model, {})
    by_name = {j["name"]: j["source"] for j in out}
    assert "NPC loot generator" not in by_name                   # joined her item
    assert by_name["Weather tracker"] == "asked"                 # hers, in the brain's words
    assert by_name["Player character sheets"] == "usual"
    # The brain's own job in plain doing words.
    assert by_name["Schedule sessions"] == "usual"
    npc = next(j for j in out if j["name"] == "Comes up with NPCs and loot")
    phrases = [p for part in npc["parts"] for p in part["search"]]
    assert "npc generator" in phrases


def test_restatements_need_both_lists():
    assert needs._restatements({"asked": ["a b"], "also_needed": []}, ["x"], "k") == set()


def test_life_news_is_listed_as_kept_private():
    goal = "I want to build a wedding planner bot. My brother just got engaged."
    private = needs._personal(goal, None)["private"]
    assert "brother" in private and "engaged" in private
    assert "got" not in private


@pytest.mark.parametrize("name,subject,want", [
    ("Play practice games with him", "chess coach", False),
    ("Remind me to stretch", "home fitness coach", False),
    ("Study reminders", "study", False),
    ("Pomodoro timer", "study", False),
    ("Keep track of his tournament results", "chess coach", False),
    ("Explain the different openings", "chess coach", True),
    ("Drill vocabulary with flashcards", "spanish tutor", True),
    ("Practice conversation", "spanish tutor", True),
])
def test_a_job_that_does_something_is_not_a_learn_job(name, subject, want):
    assert needs_rules.is_learn(name, "", subject) is want


def test_the_brain_is_asked_for_everyday_features_of_this_kind():
    from puffpai.wizard import needs_llm
    assert '"also_needed": 3 to 5 jobs' in needs_llm.SYSTEM_PROMPT_V5
    assert "everyday features popular apps of this kind offer" in needs_llm.SYSTEM_PROMPT_V5


# -- the done screen ----------------------------------------------------------------------------------------------------
class _Ctx:
    def __init__(self, *, remote, goal="I want to build a wedding planner bot.", name="WeddingBot"):
        self.remote = remote
        self.shared = {"identity": {"name": name}, "brain_ready": {"ready": True},
                       "interview": {"goal": goal}}

    def own(self):
        return {}

    def read(self, section):
        return {}

    def face(self):
        return None


@pytest.fixture
def quiet_handoff(monkeypatch):
    from puffpai import cli
    from puffpai.wizard import factory
    from puffpai.wizard.packages.handoff import skill
    monkeypatch.setattr(cli, "enter_step", lambda *a, **k: None)
    monkeypatch.setattr(cli, "installed_launchers",
                        lambda: {"dashboard": True, "backend": "x", "root": "C:\\bb",
                                 "dashboard_url": "http://127.0.0.1:3700"})
    monkeypatch.setattr(skill, "not_connected", lambda ctx: [])
    monkeypatch.setattr(factory, "added_lines", lambda shared: ["Added A.", "Added A.", "Added B."])
    monkeypatch.setattr(factory, "learning_lines", lambda shared: [])
    return skill


def test_in_the_browser_the_done_screen_does_not_ask_her_to_start_or_pair(quiet_handoff):
    card = quiet_handoff.propose(_Ctx(remote=True))["card"]
    labels = [f["label"] for f in card["fields"]]
    assert card["title"] == quiet_handoff.TITLE_HERE
    assert "Let your first device in" not in labels
    assert "Open your bot next time" in labels
    added = [f["current"] for f in card["fields"] if f["label"] == "Added for you"]
    assert added == ["Added A.", "Added B."]


def test_in_the_terminal_the_done_screen_still_says_how_to_start(quiet_handoff):
    card = quiet_handoff.propose(_Ctx(remote=False))["card"]
    labels = [f["label"] for f in card["fields"]]
    assert card["title"] == "How to start it"
    assert "Let your first device in" in labels and "Open your bot" in labels


@pytest.mark.parametrize("topic,want", [("wedding", "weddings"), ("knitting", "knitting"),
                                        ("gardening", "gardening"), ("space", "space"),
                                        ("chess", "chess"), ("dinosaurs", "dinosaurs"),
                                        ("comets and meteors", "comets and meteors")])
def test_ask_me_anything_about_reads_right(quiet_handoff, topic, want):
    assert quiet_handoff._about(topic) == want


def test_the_proof_line_has_no_code_marks(quiet_handoff):
    from puffpai import prove
    ctx = _Ctx(remote=True)
    ctx.shared["prove_record"] = {"checks": [{"result": prove.PASS}] * 5}
    line = quiet_handoff._proof_line(ctx)
    assert "`" not in line and "puffpai prove" in line and "5 passed" in line


def test_the_wizard_proof_is_the_record_the_home_page_reads(tmp_path, monkeypatch):
    from puffpai import prove
    from puffpai.wizard.packages.prove import skill
    cfg = types.SimpleNamespace(CONFIG_DIR=str(tmp_path))
    record = {"schema": prove.SCHEMA, "verdict": "pass", "checks": [{"result": prove.PASS}],
              "gaps": []}
    monkeypatch.setattr(prove, "static_prove", lambda: copy.deepcopy(record))
    monkeypatch.setattr(prove, "state_path", lambda cfg=None: str(tmp_path / "wizard_run.json"))
    out = skill.part_check(None)
    assert out["shared"]["prove_record"]["verdict"] == "pass"
    assert prove.read_record(cfg)["verdict"] == "pass"


def test_the_browser_name_screen_does_not_say_press_enter():
    from puffpai.wizard import wording
    line = wording.say("name_help_page", wording.STANDARD, suggestion="WeddingBot")
    assert "Press Enter" not in line and "WeddingBot" in line


def test_a_licence_with_terms_of_its_own_is_not_called_no_licence():
    row = repo_find("a", "budget", "A budget app", licence="LicenseRef-custom")
    assert meaning.note_reason(row) == meaning.NOTE_CUSTOM_LICENCE
    bare = repo_find("a", "budget2", "A budget app", licence=None)
    assert meaning.note_reason(bare) == meaning.NOTE_NO_LICENCE


def test_purpose_files_a_find_under_the_job_it_fits_best_when_the_brain_agrees():
    jobs = [{"id": "j1", "name": "Track themes from pictures", "source": "asked"},
            {"id": "j5", "name": "Do a whole wedding plan", "source": "asked"},
            {"id": "j6", "name": "Timeline", "source": "usual"}]
    row = _row("github:x/wedding-planner", job="j1", fit=0.6)
    row["meaning"]["best_job"] = "j5"
    asked = []

    def ask(messages, format=None):
        asked.append(messages[1]["content"])
        return json.dumps({"answers": {"1": "yes" if "whole wedding plan" in asked[-1] else "no"}})
    meaning.purpose_check([row], jobs, ask, kind="wedding planner")
    assert row["meaning"]["job"] == "j5" and row["meaning"]["purpose"] == "yes"
    assert row["meaning"]["filed_as"] == "j1" and len(asked) == 1


def test_purpose_never_moves_a_find_to_a_job_the_brain_added():
    jobs = [{"id": "j1", "name": "Plan the budget", "source": "asked"},
            {"id": "j6", "name": "Timeline", "source": "usual"}]
    row = _row("github:x/timeline", job="j1", fit=0.6)
    row["meaning"]["best_job"] = "j6"
    meaning.purpose_check([row], jobs, _answer("yes"), kind="wedding planner")
    assert row["meaning"]["job"] == "j1"


def test_names_on_the_plan_carry_no_picture_symbols():
    assert find_parts._clean("AI Wedding Planner 💍✨", 60) == "AI Wedding Planner"


def test_a_best_match_candidate_may_try_her_next_job_once():
    jobs = [{"id": "j1", "name": "Track themes from pictures", "source": "asked"},
            {"id": "j5", "name": "Do a whole wedding plan", "source": "asked"}]
    row = _row("github:x/wedding-planner", job="j1", fit=0.6)
    row["meaning"].update(best_job="j1", best_jobs=["j1", "j5"])
    asked = []

    def ask(messages, format=None):
        asked.append(messages[1]["content"])
        return json.dumps({"answers": {"1": "yes" if "whole wedding plan" in asked[-1] else "no"}})
    meaning.purpose_check([row], jobs, ask, kind="wedding planner")
    assert row["meaning"]["job"] == "j5" and len(asked) == 2


def test_a_moderate_line_is_asked_once():
    row = _row("github:x/thing", job="j1", fit=0.42)
    row["meaning"].update(best_job="j1", best_jobs=["j1", "j2"])
    asked = []

    def ask(messages, format=None):
        asked.append(1)
        return json.dumps({"answers": {"1": "no"}})
    meaning.purpose_check([row], JOBS, ask, kind="pet")
    assert len(asked) == 1 and row["meaning"]["state"] == "dropped"
