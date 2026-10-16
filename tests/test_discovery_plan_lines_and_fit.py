"""Discovery: plan lines and fit.

Goal-agnostic rules, each proven on made-up finds (no network, no model):

* plan lines: how an app opens is never cut off; the best-match marker is on
  the top fit of every job that has one; a pre-tick only under a job she
  asked for; a find for Home Assistant (not set up) or with no page of its
  own is left out; one line per picture; a strong find about the bot itself
  is kept under a job the brain added; a server app is the first note;
* meaning: a project made to try something out, one nothing describes, or
  one written in another language is not a fit; an old book needs the
  brain's yes;
* the plan's other lines: the weather forecast and a look are pre-ticked only
  when the goal is about them; a daily update is offered only when a job asks
  for something regular and is never named after her personal words; with
  jobs the word-matched library list keeps only topic packs;
* the needs step: parallel doing words keep their own verb, filler words are
  never searched, gadgets she never named are never searched, the brain's
  jobs read as plain doing words;
* names come from the project, never from a README section heading;
* the looking step names every place asked, and its last line stays on
  screen; the age question says when it matters;
* the harness: a dry run dials nothing, and ``one_preticked`` reads what the
  owner sees.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import types

import pytest

from puffpai.catalogs import meaning, needs_rules
from puffpai.wizard import find_parts, needs, proposal
from tests.test_find_parts_plan_items import (GOAL, FakeWhere, _job, _needs, lanes,  # noqa: F401
                                      repo_find)

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _mrow(fit, *, judge="yes", purpose="yes", state="might_help", what="Does the job.",
          **extra):
    return dict({"name": "x", "what_it_is": what,
                 "meaning": {"state": state, "fit": fit, "judge": judge, "purpose": purpose}},
                **extra)


def _item(iid, need, *, section="apps_code", on=False, detail="What it is."):
    return {"id": iid, "need": need, "section": section, "default_on": on, "detail": detail,
            "label": iid}


JOBS = [{"id": "j1", "name": "Plan the budget", "source": "asked"},
        {"id": "j2", "name": "Keep the guest list", "source": "asked"},
        {"id": "j3", "name": "Send reminders", "source": "usual"}]


# -- plan lines ---------------------------------------------------------------------------
def test_how_an_app_opens_is_never_cut_off():
    long = "An elegant, self-contained single-file wedding planning dashboard " * 6
    found = repo_find("someone", "wedding-planner", long, stars=2)
    found["meaning"] = {"state": "might_help", "fit": 0.6, "job": "j1"}
    item = find_parts._item(found, JOBS[0])
    assert item["detail"].endswith(find_parts.MODE_STATIC)
    marked = f"{find_parts.BEST_MATCH} {item['detail']}"
    assert len(marked) <= proposal.MAX_DETAIL
    assert proposal.fit_detail(marked) == marked


def test_the_best_match_is_on_the_top_fit_of_every_job():
    items = [_item("a", "j1"), _item("b", "j1"), _item("c", "j2"), _item("d", "j3"),
             _item("e", "j2", section="pictures")]
    rows = {"a": _mrow(0.48, judge=None, purpose=None), "b": _mrow(0.45),
            "c": _mrow(0.70, judge="no", purpose=None), "d": _mrow(0.52, purpose="unsure"),
            "e": _mrow(0.9)}
    find_parts._mark_best(items, rows, JOBS)
    # A brain's yes beats a busier fit; a "no" is never a best match; a job
    # the brain added gets one too; a single picture never.
    assert [i["id"] for i in items if i.get("best_match")] == ["b", "d"]
    assert all(not i["default_on"] for i in items)


def test_a_pre_tick_lands_only_under_a_job_she_asked_for():
    items = [_item("a", "j3", on=True), _item("b", "j1", on=True)]
    rows = {"a": _mrow(0.8), "b": _mrow(0.7)}
    find_parts._guard_ticks(items, rows, "wedding planner", JOBS)
    assert [i["id"] for i in items if i["default_on"]] == ["b"]
    assert items[0]["doubt"] == "not a job she asked for"


def test_a_find_she_could_never_use_here_is_left_out(monkeypatch):
    monkeypatch.setattr(find_parts, "_ha_in_use", lambda: False)
    card = repo_find("someone", "astroweather-card", "Astro weather card for Home Assistant",
                     mode="web_build")
    card["details"]["package_json"] = {"deps": ["rollup", "lit"], "scripts": ["build"]}
    lib = repo_find("someone", "chart-widget", "A chart widget", mode="web_build")
    lib["details"]["package_json"] = {"deps": ["rollup"], "scripts": ["build"]}
    app = repo_find("someone", "star-app", "A star chart app", mode="web_build")
    app["details"]["package_json"] = {"deps": ["vite"], "scripts": ["build"]}
    app["details"]["entry_html"] = "index.html"
    ng = repo_find("someone", "sky-ng", "A sky planner", mode="web_build")
    ng["details"]["package_json"] = {"deps": ["@angular/core"], "scripts": ["build"]}
    rows = {"c": card, "l": lib, "a": app, "n": ng}
    for r in rows.values():
        r["meaning"] = {"state": "might_help", "fit": 0.6}
    items = [_item(k, "j1") for k in rows]
    assert find_parts._cannot_be_used(items, rows) == 2
    assert [i["id"] for i in items] == ["a", "n"]
    assert card["meaning"]["why_dropped"] == "made for Home Assistant"
    assert lib["meaning"]["why_dropped"] == "no page of its own to open"
    # A 2018 app whose build needs node-sass never builds in the box.
    old = repo_find("someone", "meal-planner", "A meal planner", mode="web_build")
    old["details"]["package_json"] = {"deps": ["react-scripts", "node-sass"],
                                      "scripts": ["build"]}
    old["meaning"] = {"state": "might_help", "fit": 0.6}
    items = [_item("o", "j1")]
    assert find_parts._cannot_be_used(items, {"o": old}) == 1
    assert old["meaning"]["why_dropped"] == "its build needs a part that no longer works"
    # With Home Assistant set up here, its card is a find like any other.
    monkeypatch.setattr(find_parts, "_ha_in_use", lambda: True)
    card["details"]["entry_html"] = "index.html"
    items = [_item("c", "j1")]
    assert find_parts._cannot_be_used(items, {"c": card}) == 0


def test_the_same_picture_twice_is_one_line():
    pic = {"section": "pictures", "label": "Event Decorations (a picture on Flickr)",
           "detail": "A picture by TConway25. Free to use with credit (CC BY 2.0)."}
    other = dict(pic, detail="A picture by Someone Else. Free to use with credit.")
    items = [dict(pic, id="pictures:a"), dict(pic, id="pictures:b"), dict(other, id="pictures:c")]
    assert find_parts._same_pictures(items) == 1
    assert [i["id"] for i in items] == ["pictures:a", "pictures:c"]


def test_a_strong_find_about_the_bot_stays_under_a_job_the_brain_added():
    jobs = [{"id": "j1", "name": "Knit a blanket", "source": "asked"},
            {"id": "j2", "name": "Track projects", "source": "usual"}]
    knit = _mrow(0.76, name="knitting-app", what="Count rows for your knitting projects.")
    other = _mrow(0.62, name="passlock", what="A password library.")
    items = [_item("k", "j2"), _item("p", "j2")]
    rows = {"k": knit, "p": other}
    assert find_parts._only_brain_jobs(items, rows, jobs, "knitting") == 1
    assert [i["id"] for i in items] == ["k"]


def test_a_server_app_is_the_first_note_of_a_job(monkeypatch):
    def note(fid, mode, stars, fit):
        f = repo_find("a", fid, "An app.", stars=stars, mode=mode,
                      licence=None if mode != "server_app" else "MIT")
        f["meaning"] = {"state": "note", "job": "j1", "fit": fit}
        f["integrable"] = mode != "server_app"
        return f
    rows = [note("nolic1", "static_web", 1, 0.7), note("nolic2", "static_web", 1, 0.6),
            note("server", "server_app", 1, 0.5)]
    meaning._caps(rows)
    kept = [f["id"] for f in rows if f["meaning"]["state"] == "note"]
    assert kept == ["github:a/server"]


def test_a_project_with_no_licence_is_a_note_only_when_people_use_it():
    def note(name, stars, fit=0.47):
        f = repo_find("a", name, "A wedding planner.", stars=stars, licence=None)
        f["meaning"] = {"state": "note", "job": "j1", "fit": fit}
        return f
    rows = [note("used", 50), note("unused", 0)]
    meaning._caps(rows)
    assert [f["meaning"]["state"] for f in rows] == ["note", "dropped"]
    alone = [note("unused", 1)]
    meaning._caps(alone)
    assert alone[0]["meaning"]["why_dropped"] == meaning.UNUSED_NO_LICENCE
    strong = [note("unused-but-strong", 1, fit=0.62)]
    meaning._caps(strong)
    assert strong[0]["meaning"]["state"] == "note"


# -- meaning: what is not a fit ------------------------------------------------------------
@pytest.mark.parametrize("name,what,kind,out", [
    ("coach-testing-ground", "Testing ground for the coach engine", "chess coach", True),
    ("chess-vision-studio", "A research perception engine for chess boards", "chess coach",
     True),
    ("Chess-Opening-Study", "An agentic AI experiment with openings", "chess coach", True),
    ("lab-log", "A science experiment log for kids", "science experiment helper", False),
    ("ChessPuzzle", "Solve chess puzzles in your browser", "chess coach", False),
])
def test_a_project_made_to_try_something_out_is_not_a_fit(name, what, kind, out):
    assert meaning.tried_out({"name": name, "what_it_is": what}, kind) is out


def test_a_project_in_another_language_is_not_a_fit():
    jobs = [{"name": "Practicing interview questions", "does": "Asks you the questions."}]
    assert meaning.other_language({"name": "interview-coach-skill",
                                   "what_it_is": "面试教练技能，模拟面试问题"}, jobs)
    assert meaning.other_language({"name": "entrevista",
                                   "what_it_is": "Una herramienta para practicar la entrevista "
                                                 "de trabajo con el bot"}, jobs)
    assert not meaning.other_language({"name": "interview-prep",
                                       "what_it_is": "Practice the questions for your next "
                                                     "interview"}, jobs)
    spanish = [{"name": "Practicar la conversación", "does": "Habla con el tutor en español."}]
    assert not meaning.other_language({"name": "charla", "what_it_is": "Una app para "
                                       "practicar la conversación con el tutor"}, spanish)


def test_an_old_book_needs_the_brains_yes():
    def book(judge):
        return {"id": "gutenberg:1", "source": "gutenberg", "part": "knowledge",
                "tier": "gutenberg",
                "meaning": {"state": "might_help", "fit": 0.6, "judge": judge}}
    rows = [book("unsure"), book(None), book("yes")]
    meaning.settle(rows, judged=True)
    assert [r["meaning"]["state"] for r in rows] == ["dropped", "dropped", "might_help"]
    assert rows[0]["meaning"]["why_dropped"] == meaning.BOOK_NOT_YES
    rows = [book(None)]
    meaning.settle(rows, judged=False)        # no brain answered: the fit decides
    assert rows[0]["meaning"]["state"] == "might_help"


# -- the plan's other lines --------------------------------------------------------------
@pytest.mark.parametrize("goal,fits", [
    ("I set up a Minecraft server and need help restarting it when it freezes", False),
    ("I want help practicing interview questions because I freeze up", False),
    ("A vegetable garden helper: remind me when to water and what to plant", True),
])
def test_the_forecast_is_ticked_only_when_the_goal_is_about_the_weather(goal, fits):
    assert proposal.weather_fits(goal) is fits


def test_a_look_matched_on_a_word_the_bot_is_not_about_starts_off(monkeypatch):
    from puffpai.looks import proposals as looks
    item = {"id": "looks:theme:tweakcn:candyland", "section": "looks", "default_on": True,
            "detail": "From a free look list. It matches your words 'sugar'. You can change it.",
            "label": "Candyland", "apply": {"kind": "theme", "ref": "tweakcn:candyland"}}
    monkeypatch.setattr(looks, "proposal_items", lambda goal, terms, ask=None: [dict(item)])

    def sctx(kind):
        return types.SimpleNamespace(goal="g", terms=[], shared={"needs": {
            "schema": "needs/v1", "kind": kind, "jobs": []}})
    assert looks.wizard_items(sctx("pregnancy log"))[0]["default_on"] is False
    assert looks.wizard_items(sctx("candy sugar shop"))[0]["default_on"] is True
    # No needs step: the word match decides, as before.
    no_needs = types.SimpleNamespace(goal="g", terms=[], shared={})
    assert looks.wizard_items(no_needs)[0]["default_on"] is True


@pytest.mark.parametrize("goal", [
    "My grandson Jamie turns 11 on March 14, and I set up a Minecraft server.",
    "I'm 34 and 19 weeks pregnant with our first baby.",
    "My son Eli is 10 and got a telescope for Christmas.",
    "I got laid off from my supervisor job after 14 years.",
    "My sister and I are starting a small home bakery.",
])
def test_a_daily_update_is_never_named_after_her_personal_words(goal):
    from puffpai import offers
    assert offers.topic_phrase(goal) is None
    label = offers.proposal_items(goal, [])[0]["label"]
    assert label == "A daily update at 8 am"


def test_a_refused_topic_never_falls_back_to_one_loose_word():
    from puffpai import offers
    goal = "My grandson Jamie turns 11 on March 14, and I set up a Minecraft server."
    assert offers.topic_phrase(goal, ["turn", "minecraft"]) is None
    assert offers.topic_phrase(
        "Help me build a family budget bot. My partner and I have two kids and I want to "
        "track spending from our bank exports, set monthly budgets by category, and see "
        "charts of where the money goes.") is None       # never "set monthly budgets"


def test_a_daily_update_keeps_a_plain_subject():
    from puffpai import offers
    assert offers.topic_phrase("I want to build a wedding planner bot.") == "wedding planner"
    assert offers.topic_phrase("Help me keep my 2014 Honda Civic running") == "2014 Honda Civic"


def test_a_daily_update_is_offered_only_when_a_job_asks_for_something_regular():
    from puffpai.wizard import factory

    def sctx(*names):
        jobs = [{"id": f"j{n}", "name": name, "ticked": True} for n, name in enumerate(names)]
        return types.SimpleNamespace(goal="A wedding planner bot", terms=[],
                                     shared={"needs": {"schema": "needs/v1", "jobs": jobs}})
    assert factory.update_items(sctx("Plan the budget", "Keep the guest list")) == []
    got = factory.update_items(sctx("Plan the budget", "Send reminders"))
    assert [i["id"] for i in got] == ["updates:daily_update"]
    # No needs step: offered as before.
    assert factory.update_items(types.SimpleNamespace(goal="A wedding planner bot", terms=[],
                                                      shared={}))


def test_with_jobs_the_word_matched_library_keeps_only_topic_packs(monkeypatch):
    from puffpai.catalogs import knowledge_resolver as kr
    from puffpai.wizard import factory
    lines = [{"id": f"knowledge:{ref}", "section": "knowledge", "label": ref,
              "apply": {"kind": "knowledge.add", "ref": ref}, "default_on": False}
             for ref in ("kiwix:wikipedia_en_all:mini", "gutenberg:42", "kiwix_space_stackexchange")]
    monkeypatch.setattr(kr, "proposal_items", lambda goal, terms: [dict(i) for i in lines])
    monkeypatch.setattr(kr, "find", lambda *a, **k: {"candidates": [
        {"id": "kiwix:wikipedia_en_all:mini", "general": True}]})
    monkeypatch.setattr(kr, "candidate_by_id", lambda ref: {"id": ref})
    monkeypatch.setattr(kr, "tier_of", lambda c: "gutenberg" if str(c["id"]).startswith(
        "gutenberg") else "kiwix")
    args = types.SimpleNamespace(skip_knowledge=False)
    with_jobs = types.SimpleNamespace(goal="space", args=args, internet=True, previews={},
                                      shared={"needs": {"schema": "needs/v1", "jobs": [
                                          {"id": "j1", "name": "Learn about space",
                                           "ticked": True, "learn": True}]}})
    assert [i["apply"]["ref"] for i in factory.knowledge_items(with_jobs)] == [
        "kiwix_space_stackexchange"]
    no_jobs = types.SimpleNamespace(goal="space", args=args, internet=True, previews={},
                                    shared={})
    assert len(factory.knowledge_items(no_jobs)) == 3


def test_the_sources_list_keeps_only_topic_packs_with_jobs():
    assert proposal._topic_pack({"id": "kiwix_space_stackexchange", "matched_terms": ["space"]})
    assert not proposal._topic_pack({"id": "kiwix:wikipedia_en_all:mini"})
    assert not proposal._topic_pack({"id": "gutenberg:42", "matched_terms": ["job"]})


# -- the needs step ------------------------------------------------------------------------
def test_parallel_doing_words_keep_their_own_verb():
    goal = ("I need help adding mods safely, restarting it when it freezes, and stopping "
            "the kids from wrecking each other's builds.")
    names = [needs_rules.job_name(i) for i in needs_rules.explicit_items(goal)]
    assert names == ["Adding mods safely", "Restarting it when it freezes",
                     "Stopping the kids from wrecking each other's builds"]


def test_a_products_name_is_not_the_kind_of_bot():
    goal = ("My husband Dev put Home Assistant on a little box, and now I'm stuck fixing "
            "the dashboard.")
    assert needs_rules.subject_of(goal) == ""
    assert needs_rules.subject_of("Build me a Home Assistant helper bot") == "home assistant"
    assert needs_rules.subject_of("I want a chess coach bot for my grandson") == "chess coach"


def test_filler_and_doing_words_are_never_search_words():
    personal = needs_rules.personal_words("x")
    words = needs_rules.item_words("tells us what's worth pointing it at from our backyard",
                                   personal)
    assert "worth" not in words and "pointing" not in words
    assert needs_rules.item_words("plus help working out how much yarn to buy",
                                  personal) == ["yarn"]
    assert needs_rules.item_words("stopping the kids from wrecking builds", personal)[:1] != [
        "stopping"]


def test_a_gadget_she_never_named_is_never_searched():
    jobs = [{"parts": [{"kind": "code-app", "search": ["smartwatch notifications", "smart plug timer",
                                                       "study reminder app", "power strip"]}]},
            {"parts": [{"kind": "code-app", "search": ["kitchen tablet dashboard"]}]}]
    needs._off_goal_gadgets(jobs, "a dashboard for the kitchen tablet")
    assert jobs[0]["parts"][0]["search"] == ["study reminder app"]
    assert jobs[1]["parts"][0]["search"] == ["kitchen tablet dashboard"]


@pytest.mark.parametrize("name,plain", [
    ("Price comparator", "Compare prices"), ("Reminder sender", "Send reminders"),
    ("Reminder system", "Send reminders"), ("To-do list creator", "Make to-do lists"),
    ("Decoration suggestion", "Suggest decorations"), ("Sales data analyzer",
                                                       "Look at sales data"),
    ("Track progress", "Track progress"), ("Pomodoro timer", "Pomodoro timer"),
])
def test_the_brains_jobs_read_as_plain_doing_words(name, plain):
    assert needs_rules.plain_job_name(name) == plain


def test_the_brain_is_asked_for_plain_job_names_and_no_gadgets():
    from puffpai.wizard import needs_llm
    text = needs_llm.SYSTEM_PROMPT_V5
    assert "never a tool name" in text and "Keep the guest list" in text
    assert "Never name a gadget" in text
    also = text.split('"also_needed"', 1)[1].split('"jobs"', 1)[0]
    assert "for most often" in also
    # No example job in that sentence: a 7B brain copied "guest list" into a
    # family budget bot.
    assert "guest list" not in also


def test_her_own_items_keep_her_words_and_the_brains_read_plainly(monkeypatch):
    monkeypatch.setattr(needs, "_sims", lambda a, b: [[0.0] * len(b) for _ in a])
    rule = [{"name": "Plan the budget", "does": "", "source": "asked",
             "parts": [{"kind": "code-app", "search": ["wedding budget"]}]}]
    model = {"kind": "wedding planner", "asked": ["plan the budget"],
             "also_needed": ["price comparator"],
             "jobs": [{"name": "Price comparator", "does": "compares prices",
                       "parts": [{"kind": "code-app", "search": ["price comparison"]}]}]}
    out = needs._merge(rule, model, {})
    assert [(j["name"], j["source"]) for j in out] == [("Plan the budget", "asked"),
                                                       ("Compare prices", "usual")]


# -- names ---------------------------------------------------------------------------------
def test_a_readme_section_heading_is_never_the_projects_name():
    from puffpai.catalogs.searchers import github
    assert github.friendly_from("", "# About The Project\n\nRecipes.", "mealie") == "mealie"
    assert github.friendly_from("", "# Table of Contents\n", "mealie") == "mealie"
    assert github.friendly_from("", "# Mealie\n\nRecipes.", "mealie") == "Mealie"


# -- the looking step and the age question ----------------------------------------------
def test_a_place_asked_that_found_nothing_has_its_own_line(lanes, monkeypatch):
    monkeypatch.setattr(find_parts, "SEARCH_WORKERS", 1)
    where = FakeWhere(online={"j1": [repo_find("acme", "wedding-budget", "A wedding budget.")]})
    where.mod.route = lambda kind: [
        {"id": "github_repo_search", "label": "GitHub", "role": "live", "enabled": True},
        {"id": "codeberg_repo_search", "label": "Codeberg", "role": "live", "enabled": True}]
    lanes(where)
    said: list[str] = []
    job = _job("j1", "Plan the budget", "Plans what each part costs.", "asked",
               ["wedding budget"])
    find_parts.plan_items(_needs([job]), goal=GOAL, online=True, allow_send=True, cfg={},
                          on_line=said.append)
    assert "GitHub: 1 found" in said and "Codeberg: nothing found" in said
    assert said.index("Codeberg: nothing found") < said.index(find_parts.LINE_CHECKING_START)


def test_the_last_looking_line_stays_on_screen_before_the_plan():
    text = (ROOT / "dashboard" / "app" / "onboarding" / "NeedsStep.tsx").read_text(
        encoding="utf-8")
    done = text[text.index('reply.data.phase === "parts" && reply.data.done'):][:600]
    assert "LAST_LINES_MS" in done and done.index("LAST_LINES_MS") < done.index("onDone()")


def test_the_age_question_says_when_it_matters():
    from puffpai.wizard import cards, wording
    for mode in (wording.STANDARD, wording.SIMPLE):
        said = wording.say("age_question", mode)
        assert "under 13" in said and "leave it empty" in said
        assert len(said) <= cards.MAX_LABEL


# -- the harness ---------------------------------------------------------------------------
def _harness():
    spec = importlib.util.spec_from_file_location("r3_acceptance_r2",
                                                  ROOT / "scripts" / "measure_goals.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_dry_run_dials_nothing(monkeypatch):
    from puffpai import source_fetch
    harness = _harness()
    dialled = []
    monkeypatch.setattr(source_fetch, "_default_transport",
                        lambda url, headers, timeout: dialled.append(url))
    cap = harness.Capture(refuse=True).install()
    try:
        with pytest.raises(OSError):
            source_fetch._default_transport("https://github.com/a/b.git/info/refs", {}, 5)
    finally:
        cap.uninstall()
    assert dialled == []
    rows = cap.take()
    assert rows[0]["refused"] is True
    assert harness._requests_words(rows) == "0 requests (1 refused)"


def test_the_dry_flag_says_what_it_does():
    text = (ROOT / "scripts" / "measure_goals.py").read_text(encoding="utf-8")
    assert "every outside request is refused" in text
    assert "Capture(refuse=not online)" in text


def test_one_preticked_reads_what_the_owner_sees(monkeypatch):
    harness = _harness()
    monkeypatch.setattr(find_parts, "found_for", lambda i: {
        "trust": {"pretick_ok": False}, "meaning": {"fit": 0.7}})
    items = [{"id": "a", "apply": {"kind": "code_project"}, "default_on": False},
             {"id": "b", "apply": {"kind": "code_project"}, "default_on": False}]
    base = {"plan": {"items": items}, "timings": {"search_s": 50.0}, "personal_leaks": []}
    bar = harness.bar_for(base)
    assert bar["one_preticked"] is False and bar["checks"]["pretick_where_trust_allows"]
    items[0]["default_on"] = True
    assert harness.bar_for(base)["one_preticked"] is True


def test_a_whole_plan_app_is_asked_about_the_whole_plan_job():
    names = {"j5": "Do a whole wedding plan", "j6": "Create guest list"}
    whole = {"meaning": {"state": "might_help", "job": "j5", "best_job": "j6", "whole": True}}
    other = {"meaning": {"state": "might_help", "job": "j5", "best_job": "j6"}}
    assert meaning._judge_jobs(whole, names) == "Do a whole wedding plan"
    assert meaning._judge_jobs(other, names) == "Create guest list"


def test_a_name_that_already_says_what_it_does_is_kept():
    assert needs_rules.plain_job_name("Send reminders") == "Send reminders"
    assert needs_rules.plain_job_name("Track plant growth") == "Track plant growth"


def test_a_readme_line_is_never_a_notes_name():
    f = repo_find("alextselegidis", "easyappointments", "Book appointments online with the "
                  "calendar.", licence="LicenseRef-x")
    f["friendly"] = "Clone the repository"
    assert find_parts._note(f).startswith("easyappointments (")
    f["friendly"] = "Easy Appointments"
    assert find_parts._note(f).startswith("Easy Appointments (")


def test_a_piece_of_a_sentence_is_not_the_bots_subject():
    goal = ("My husband Dev put Home Assistant on a little box in our house, and now I'm the "
            "one stuck fixing the dashboard.")
    # With a name at the start of a sentence read as a name, the
    # subject is the whole thing she named, never a piece like "put home".
    assert needs._personal(goal, None)["subject"] in ("home assistant", "")
    assert needs._personal("I set up a Minecraft server bot for my grandson.",
                           None)["subject"] in ("minecraft server", "")
    assert needs._personal("I want to build a wedding planner bot.", None)["subject"] == \
        "wedding planner"


def test_a_jobs_first_code_search_asks_for_open_licences_only(monkeypatch):
    from puffpai.catalogs.searchers import github
    from tests.test_searchers_query_shapes import fake_outbound, gh_row
    oq = fake_outbound(monkeypatch)
    urls = [u for u, _p in github.queries({"id": "j3"}, [oq.Sendable("chess puzzle", "j3")],
                                          kind="code-app", row=gh_row())]
    assert "+license:mit+license:apache-2.0+license:gpl-3.0" in urls[0]
    # The later shapes still find server apps and projects she can only look at.
    assert all("license:" not in u for u in urls[1:])


def test_a_project_made_for_speakers_of_another_language_is_not_a_fit():
    jobs = [{"name": "Practicing interview questions", "does": "Asks you the questions."}]
    skill = {"name": "interview-coach-skill", "what_it_is": "Chinese interview-coach skill for "
             "AI-assisted job search, structured practice and answer critique."}
    assert meaning.other_language(skill, jobs, "job search coach")
    tutor = {"name": "spanish-tutor", "what_it_is": "Spanish conversation practice."}
    assert not meaning.other_language(tutor, [{"name": "Practice conversation"}],
                                      "spanish tutor")
    assert meaning.other_language({"name": "french-language-coach", "what_it_is": "Coach."},
                                  [{"name": "Practice conversation"}], "spanish tutor")
    feature = {"name": "chess-trainer", "what_it_is": "Train tactics. Available in English, "
               "Spanish and French."}
    assert not meaning.other_language(feature, [{"name": "Give tactics puzzles"}], "chess coach")


def test_a_held_phrase_is_never_cut_down_to_one_word():
    goal = "I got laid off from my supervisor job and I want help practicing interview prep."
    rows = needs._screen(["job interview prep"], goal=goal, home=None, allowed=[], hint="j1")
    assert all(len(r["phrase"].split()) >= 2 for r in rows if r["state"] == "out")
