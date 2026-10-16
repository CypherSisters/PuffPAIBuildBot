"""The plan grouped by the owner's jobs.

"What it will do" comes first: one group per ticked job, holding every line
whose ``need`` is that job, whatever its section; the notes ``find_parts``
gave the job; and, for a job the search did not reach, a line saying so. The
sections follow with the lines that have no job, and the locked box is last.
Both faces draw the same thing: the terminal is checked here line by line.

Run:
    pytest tests/test_plan_groups_by_job.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wizard_needs_step import (  # noqa: E402
    GOALS, answer_questions, install_box_line, install_fakes, looked,
    pinned_machine, wait_for)

from puffpai.wizard import proposal, render_cli, wording  # noqa: E402


@pytest.fixture
def planned(monkeypatch, tmp_path):
    def make(goal_id, untick=()):
        from puffpai.wizard import service
        goal, kind = GOALS[goal_id]
        pinned_machine(monkeypatch, tmp_path)
        calls = install_fakes(monkeypatch, kind)
        install_box_line(monkeypatch)
        answer_questions(goal)
        service.first_look()
        view = wait_for(service.needs)
        ticked = [j["id"] for j in view["jobs"] if j["id"] not in untick]
        looked(service, {"ticked": ticked, "look": "online"})
        return service, calls, kind
    return make


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_one_group_per_ticked_job_first(planned, goal_id):
    service, _calls, kind = planned(goal_id)
    word = kind.split()[0]
    summary = service.summary()
    assert summary["jobs_heading"] == "What it will do"
    groups = summary["jobs"]
    assert [g["heading"] for g in groups] == [
        f"Keep the {word} plan", "Track the money", "Find helpers nearby",
        "Send reminders"]
    by_id = {g["id"]: g for g in groups}
    # Every line whose need is the job sits under it, whatever its section.
    assert [i["id"] for i in by_id["j1"]["items"]] == [
        f"apps_code:github:someone/{word}-planner"]
    assert [i["id"] for i in by_id["j2"]["items"]] == [
        "apps_code:github:other/money-sheet"]
    # ... and nowhere else.
    in_sections = [i["id"] for s in summary["sections"] for i in s["items"]]
    assert not {i["id"] for g in groups for i in g["items"]} & set(in_sections)
    # Lines with no job stay in their sections; the locked box is last.
    assert "pictures:commons:examples" in in_sections
    assert summary["sections"][-1]["id"] == "box"
    # Numbering reads down the page: the job lines first.
    numbered = [i["id"] for i in proposal.numbered(summary)]
    assert numbered[:2] == [i["id"] for g in groups for i in g["items"]]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_notes_and_not_reached_lines(planned, goal_id):
    service, _calls, _kind = planned(goal_id)
    by_id = {g["id"]: g for g in service.summary()["jobs"]}
    assert by_id["j1"]["notes"] == [
        "Also found, look at it yourself:",
        "Pinry (github.com/pinry/pinry, needs its own server)",
        "Some apps need their own server. BuildBot cannot run those yet."]
    # "Found nothing" is its own line, not one of the "Also found" finds.
    assert by_id["j2"]["notes"] == [wording.FOUND_NOTHING]
    assert by_id["j3"]["not_reached"] == (
        'Did not get to look for "Find helpers nearby" this time. You can '
        'look from the Add page.')
    assert by_id["j1"]["not_reached"] == "" and by_id["j3"]["items"] == []


def test_an_unticked_job_has_no_group(planned):
    service, calls, _kind = planned("minecraft", untick=("j4",))
    assert calls["plan_items"][0]["ticked"] == ["j1", "j2", "j3"]
    assert [g["id"] for g in service.summary()["jobs"]] == ["j1", "j2", "j3"]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_the_terminal_draws_the_same_groups(planned, goal_id):
    service, _calls, kind = planned(goal_id)
    word = kind.split()[0]
    summary = service.summary()
    text = "\n".join(render_cli.summary_lines(summary))
    order = ["What it will do", f"Keep the {word} plan", "1. Planner",
             "Also found, look at it yourself:", "Pinry",
             "Track the money", "Not included (you can add it): Money sheet",
             wording.FOUND_NOTHING.split(".")[0],
             "Find helpers nearby", 'Did not get to look for "Find helpers',
             "Send reminders", "Pictures", "Your private stuff",
             "Kept in a locked vault", "The locked box",
             "Set up the locked box"]
    at = -1
    flat = " ".join(text.split())
    for piece in order:
        found = flat.find(piece, at + 1)
        assert found > at, (piece, flat)
        at = found


def test_the_plan_is_never_searched_twice(planned):
    service, calls, _kind = planned("wedding")
    for changes in (None, {"on": {"pictures:commons:examples": True}},
                    {"on": {"box:locked:setup": False}}):
        service.summary(changes)
    service.approve({"on": {}})
    assert len(calls["plan_items"]) == 1


# -- "Similar projects also have (tick to add):" --------------------------------------

import copy  # noqa: E402
import types  # noqa: E402

from test_wizard_needs_step import plan_result  # noqa: E402

SHEET = "apps_code:github:other/money-sheet"


def _with_similar(monkeypatch, kind, calls):
    mod = types.ModuleType("puffpai.wizard.find_parts")

    def plan_items(needs, *, goal, online, allow_send, cfg, on_line=None):
        calls.setdefault("plan_items", []).append(True)
        out = plan_result(kind)
        out["similar"] = [
            {"name": "Guest list and replies", "seen_in": 3, "items": [SHEET]},
            {"name": "Seen only once", "seen_in": 1, "items": []},
            {"name": "", "seen_in": 5, "items": []}]
        return out
    mod.plan_items = plan_items
    mod.found_for = lambda item_id: None
    monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", mod)


@pytest.fixture
def similar_plan(monkeypatch, tmp_path):
    def make(goal_id):
        from puffpai.wizard import service
        goal, kind = GOALS[goal_id]
        pinned_machine(monkeypatch, tmp_path)
        calls = install_fakes(monkeypatch, kind)
        _with_similar(monkeypatch, kind, calls)
        answer_questions(goal)
        service.first_look()
        wait_for(service.needs)
        looked(service, {"look": "here"})
        return service, calls
    return make


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_the_similar_group_lists_only_lines_seen_twice(similar_plan, goal_id):
    service, _calls = similar_plan(goal_id)
    similar = service.summary()["similar"]
    assert similar["heading"] == "Similar projects also have (tick to add):"
    assert similar["rows"] == [{
        "name": "Guest list and replies", "seen_in": 3,
        "line": "Guest list and replies    seen in 3 similar projects"}]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_ticking_a_similar_line_adds_the_job_and_moves_its_finds(similar_plan,
                                                                 goal_id):
    service, calls = similar_plan(goal_id)
    before = service.summary()
    assert any(item["id"] == SHEET for group in before["jobs"]
               if group["id"] == "j2" for item in group["items"])
    view = service.needs_choose({"added": ["guest list and REPLIES"]})
    assert view["phase"] == "parts"
    after = service.summary()
    group = next(g for g in after["jobs"] if g["heading"] == "Guest list and replies")
    assert [item["id"] for item in group["items"]] == [SHEET]
    assert not any(item["id"] == SHEET for g in after["jobs"]
                   if g["id"] == "j2" for item in g["items"])
    assert after["similar"]["rows"] == []
    # Found once, drawn many times: nothing was searched again.
    assert len(calls["plan_items"]) == 1
    # The new job is one of the owner's, ticked, from the engine's choose.
    assert calls["choose"][-1]["added"] == ["Guest list and replies"]


@pytest.mark.parametrize("name", ["Seen only once", "Something else"])
def test_a_line_not_on_the_plan_is_refused(similar_plan, name):
    from puffpai.wizard import cards
    service, _calls = similar_plan("wedding")
    with pytest.raises(cards.CardError) as refused:
        service.needs_choose({"added": [name]})
    assert str(refused.value) == ("That is not one of the similar projects on "
                                  "the plan.")


def test_ticks_after_looking_are_still_refused(similar_plan):
    from puffpai.wizard import cards
    service, _calls = similar_plan("minecraft")
    with pytest.raises(cards.CardError):
        service.needs_choose({"ticked": ["j1"], "added": ["Guest list and replies"]})


def test_the_terminal_plan_adds_a_similar_line(similar_plan, monkeypatch):
    """"Change something" in the terminal: the line becomes a job and the
    plan she approves has it."""
    from puffpai import cli
    service, calls = similar_plan("wedding")
    run = service._saved(service._cfg(), service._args())
    monkeypatch.setattr(render_cli, "summary_screen", lambda summary, **kw: {
        "on": {}, "similar": ["Guest list and replies"]})
    ctx = types.SimpleNamespace(args=service._args(), cfg=service._cfg(),
                                shared=copy.deepcopy(run.shared),
                                _sections=run.sections, console=True,
                                non_interactive=True)
    out = cli._wizard_summary_step(ctx)
    names = [job["name"] for job in out["shared"]["needs"]["jobs"]]
    assert "Guest list and replies" in names
    assert out["shared"]["needs_parts"]["similar"] == [
        row for row in out["shared"]["needs_parts"]["similar"]
        if row.get("name") != "Guest list and replies"]
    assert len(calls["plan_items"]) == 1


def test_add_similar_without_the_engine_is_honest():
    shared = {"needs": {"schema": "needs/v1", "jobs": []},
              "needs_parts": {"similar": [{"name": "Guest list", "seen_in": 2}]}}
    with pytest.raises(proposal.SimilarRefused) as refused:
        proposal.add_similar(shared, "Guest list",
                             engine=types.SimpleNamespace())
    assert str(refused.value) == ("Adding that is not in this build yet. "
                                  "Nothing was changed.")


@pytest.mark.parametrize("kind", ["garden helper", "chess coach"])
def test_a_job_whose_finds_all_moved_says_where_they_went(kind):
    from test_wizard_needs_step import fake_needs_module, needs_result
    needs = needs_result(kind)
    parts = proposal.clean_parts({
        "items": [{"id": "apps_code:a", "need": "j2"},
                  {"id": "apps_code:b", "need": "j1"}],
        "notes": {"j1": ["Pinry (github.com/pinry/pinry, needs its own server)"]},
        "similar": [{"name": "Guest list", "seen_in": 2,
                     "items": ["apps_code:a", "apps_code:b"]}]})
    shared = {"needs": needs, "needs_parts": dict(parts, lines=[]),
              "internet": False}
    new = proposal.add_similar(shared, "Guest list",
                               engine=fake_needs_module(kind, {}))
    record = shared["needs_parts"]
    assert {item["need"] for item in record["items"]} == {new}
    assert record["notes"]["j2"] == [
        'What BuildBot found for this is now under "Guest list".']
    # A job that already had its own note keeps it.
    assert record["notes"]["j1"] == [
        "Pinry (github.com/pinry/pinry, needs its own server)"]
