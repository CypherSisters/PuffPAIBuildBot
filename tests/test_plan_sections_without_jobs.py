"""What the plan keeps when the jobs do
not speak for a section, the looking lines said once, and unticked finds that
still say what they are.

Milestone S found three things on the merged build:

* a needs step with no jobs (the brain did not answer and the goal lists
  nothing) dropped every live card and book from the plan;
* "Checking what fits your jobs" was printed twice, "Looking on <place> is
  not in this build yet" once per job, and "Stopped looking to keep this
  quick." for a job the owner had unticked;
* an unticked find showed only "Not included (you can add it)", hiding what
  it is, its stars and licence, and how it opens.

The fakes follow the pinned signatures (design section 9, I-4) and every
test runs on more than one unrelated goal. Nothing here reaches the network.

Run:
    pytest tests/test_plan_sections_without_jobs.py
"""
from __future__ import annotations

import copy
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_plan_every_line_tickable import (  # noqa: E402
    WORD_MATCHED_BOOK, _library_lines, _word_matched_sources)
from test_wizard_needs_step import (  # noqa: E402
    GOALS, answer_questions, fake_needs_module, install_fakes, looked,
    needs_result, pinned_machine, plan_result, wait_for)

from puffpai.wizard import proposal  # noqa: E402

LIVE = {"id": "live_info:test:site", "section": "live_info",
        "label": "A live website", "detail": "Shows things right now.",
        "size_bytes": None, "outside_host": "live.example.org",
        "default_on": False, "needs_yes": True,
        "apply": {"kind": "live_feed", "ref": "test:site"}}


def _no_jobs_engine(monkeypatch, kind, calls):
    """The engine answers with no jobs at all (no brain, nothing listed)."""
    mod = fake_needs_module(kind, calls)
    real_run = mod.run

    def run(goal, *, cfg, on_line=None, on_job=None, deadline_s=60.0):
        result = real_run(goal, cfg=cfg, deadline_s=deadline_s)
        result["jobs"] = []
        result["prompt"] = "rules"
        return result
    mod.run = run
    monkeypatch.setitem(sys.modules, "puffpai.wizard.needs", mod)


def _with_live_provider(monkeypatch):
    """The older live-info provider, answering one website."""
    asked = []

    def live(sctx):
        asked.append(True)
        return [dict(LIVE)]
    builtin = tuple((name, live if name == "factory:live_info" else fn)
                    for name, fn in proposal._BUILTIN)
    monkeypatch.setattr(proposal, "_BUILTIN", builtin)
    return asked


def _plan(monkeypatch, tmp_path, goal_id, *, jobs=True, parts=None):
    from puffpai.wizard import service
    goal, kind = GOALS[goal_id]
    pinned_machine(monkeypatch, tmp_path)
    calls = install_fakes(monkeypatch, kind)
    if not jobs:
        _no_jobs_engine(monkeypatch, kind, calls)
    if parts is not None:
        mod = types.ModuleType("puffpai.wizard.find_parts")
        mod.plan_items = lambda needs, **kw: copy.deepcopy(parts)
        mod.found_for = lambda item_id: None
        monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", mod)
    _word_matched_sources(monkeypatch)
    asked = _with_live_provider(monkeypatch)
    answer_questions(f"{goal} It should also come up with ideas.")
    service.first_look()
    wait_for(service.needs)
    looked(service, {"look": "here"})
    return service.summary(), asked


# -- the plan fallback ----------------------------------------------------------

@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_with_no_jobs_the_plan_keeps_its_books_and_offers_no_live_card(
        monkeypatch, tmp_path, goal_id):
    empty = {"items": [], "notes": {}, "not_reached": [], "similar": [],
             "sends": [], "left_out": 0}
    summary, asked = _plan(monkeypatch, tmp_path, goal_id, jobs=False,
                           parts=empty)
    ids = [item["id"] for item in proposal.items_of(summary)]
    # Live cards are ingredients of a job in the build plan:
    # the summary has no live-info provider any more.
    assert asked == []
    assert LIVE["id"] not in ids
    assert f"knowledge:registry:{WORD_MATCHED_BOOK['id']}" in [
        item["id"] for item in _library_lines(summary)]
    assert summary["jobs"] == []


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_with_jobs_no_website_is_offered_on_a_shared_word(monkeypatch,
                                                          tmp_path, goal_id):
    summary, asked = _plan(monkeypatch, tmp_path, goal_id)
    ids = [item["id"] for item in proposal.items_of(summary)]
    assert asked == [], "a website was looked at because of a word"
    assert LIVE["id"] not in ids
    assert _library_lines(summary) == []


def _sctx(shared, goal="g"):
    return proposal.SummaryContext(goal=goal, terms=[], name="Bot",
                                   mode="standard", shared=shared,
                                   previews={}, args=None, cfg=None,
                                   internet=True)


@pytest.mark.parametrize("kind", ["wedding planner", "chess coach"])
def test_a_learn_job_keeps_the_books_but_not_the_word_matched_websites(
        monkeypatch, kind):
    needs = needs_result(kind)
    needs["jobs"][0]["learn"] = True
    monkeypatch.setattr(proposal, "needs_active", lambda shared: True)
    monkeypatch.setattr(proposal, "parts_for",
                        lambda shared, **kw: proposal.clean_parts({}))
    skipped = proposal.skipped_with_needs(_sctx({"needs": needs}))
    # Word-matched gadget lines step aside too ("if you have
    # Home Assistant" is never matched on a bare word on a plan with jobs).
    assert skipped == {"factory:devices"}


@pytest.mark.parametrize("kind", ["wedding planner", "chess coach"])
def test_find_parts_speaks_for_a_section_it_found_lines_for(monkeypatch, kind):
    needs = needs_result(kind)
    for job in needs["jobs"]:
        job["ticked"] = False          # no jobs: the older lists would stay
    found = proposal.clean_parts({"items": [dict(LIVE, id="devices:fp:x", section="devices")]})
    monkeypatch.setattr(proposal, "needs_active", lambda shared: True)
    monkeypatch.setattr(proposal, "parts_for", lambda shared, **kw: found)
    skipped = proposal.skipped_with_needs(_sctx({"needs": needs}))
    assert skipped == {"factory:devices"}


def test_without_the_needs_step_nothing_is_skipped(monkeypatch):
    monkeypatch.setattr(proposal, "needs_active", lambda shared: False)
    assert proposal.skipped_with_needs(_sctx({})) == frozenset()


# -- the looking lines, each said once -----------------------------------------

NOT_BUILT = "Looking on Codeberg is not in this build yet, so nothing was sent there."
CHECKED = "Checking what fits your jobs ... 1 kept, 4 left out"


def _noisy_parts(kind, *, not_reached):
    mod = types.ModuleType("puffpai.wizard.find_parts")

    def plan_items(needs, *, goal, online, allow_send, cfg, on_line=None):
        for _job in needs["jobs"]:
            on_line(NOT_BUILT)
        on_line(CHECKED)
        out = plan_result(kind)
        out["not_reached"] = list(not_reached)
        return out
    mod.plan_items = plan_items
    mod.found_for = lambda item_id: None
    return mod


def _looking_lines(monkeypatch, tmp_path, goal_id, *, untick, not_reached):
    from puffpai.wizard import service
    goal, kind = GOALS[goal_id]
    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, kind)
    monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts",
                        _noisy_parts(kind, not_reached=not_reached))
    answer_questions(goal)
    service.first_look()
    draft = wait_for(service.needs)
    ticked = [job["id"] for job in draft["jobs"] if job["id"] not in untick]
    return looked(service, {"ticked": ticked, "look": "here"})["lines"]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_each_looking_line_is_said_once(monkeypatch, tmp_path, goal_id):
    lines = _looking_lines(monkeypatch, tmp_path, goal_id, untick=(),
                           not_reached=())
    assert lines.count(NOT_BUILT) == 1, lines
    checking = [line for line in lines if line.startswith("Checking what fits")]
    assert checking == [CHECKED], lines
    assert "Stopped looking to keep this quick." not in lines


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_an_unticked_job_is_never_called_stopped(monkeypatch, tmp_path,
                                                 goal_id):
    lines = _looking_lines(monkeypatch, tmp_path, goal_id, untick=("j3",),
                           not_reached=("j3",))
    assert "Stopped looking to keep this quick." not in lines


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_a_kept_job_not_reached_is_said_once(monkeypatch, tmp_path, goal_id):
    lines = _looking_lines(monkeypatch, tmp_path, goal_id, untick=(),
                           not_reached=("j3",))
    assert lines.count("Stopped looking to keep this quick.") == 1, lines


def test_the_tail_adds_the_checking_line_when_the_search_did_not():
    from puffpai.wizard import service
    record = proposal.clean_parts(plan_result("garden helper"))
    tail = service._parts_tail(record, "standard", ticked={"j1"}, said=[])
    assert tail == ["Checking what fits your jobs ... 3 kept, 7 left out"]


# -- an unticked find still says what it is --------------------------------------

@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_an_unticked_find_shows_its_detail_in_both_faces(monkeypatch,
                                                         tmp_path, goal_id):
    from puffpai.wizard import render_cli, service
    goal, kind = GOALS[goal_id]
    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, kind)
    answer_questions(goal)
    service.first_look()
    wait_for(service.needs)
    looked(service, {"look": "here"})
    summary = service.summary()
    sheet = next(item for item in proposal.items_of(summary)
                 if item["id"] == "apps_code:github:other/money-sheet")
    assert sheet["on"] is False
    assert proposal.detail_when_off(sheet)
    text = " ".join(" ".join(render_cli.summary_lines(summary)).split())
    assert "Not included (you can add it): Money sheet" in text
    assert "Not included (you can add it): Money sheet" in sheet["off_line"]
    at = text.index("Not included (you can add it): Money sheet")
    assert "A list your bot can read." in text[at:at + 300]


def test_an_older_unticked_line_keeps_its_one_line():
    older = {"id": "outside_services:web", "detail": "Uses the internet.",
             "section": "outside_services"}
    assert not proposal.detail_when_off(older)
    assert not proposal.detail_when_off({"need": "j1", "detail": ""})
    assert proposal.detail_when_off({"facts": {"stars": 3}, "detail": "x"})
