"""Every line on the plan can be ticked.

The owner could not uncheck the ones they did not want to download. When
the needs step ran, every line of the plan has its tick box: a line that is
only a statement becomes a note. The extras (``need``, ``why``,
``might_help``, ``requires``, ``facts``) survive validation and the decision,
and a line that needs the locked box is off whenever the box is.

Run:
    pytest tests/test_plan_every_line_tickable.py
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wizard_needs_step import (  # noqa: E402
    GOALS, answer_questions, install_box_line, install_fakes, looked,
    pinned_machine, wait_for)

from puffpai.wizard import proposal  # noqa: E402

APP = "apps_code:github:someone/{word}-planner"
BOX = "box:locked:setup"


@pytest.fixture
def planned(monkeypatch, tmp_path):
    """The plan after the needs step, for a goal; returns (service, calls,
    kind)."""
    def make(goal_id):
        from puffpai.wizard import service
        goal, kind = GOALS[goal_id]
        pinned_machine(monkeypatch, tmp_path)
        calls = install_fakes(monkeypatch, kind)
        install_box_line(monkeypatch)
        answer_questions(goal)
        service.first_look()
        wait_for(service.needs)
        looked(service, {"look": "online"})
        return service, calls, kind
    return make


def _app_id(kind):
    return APP.format(word=kind.split()[0])


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_every_line_can_be_ticked(planned, goal_id):
    service, _calls, _kind = planned(goal_id)
    summary = service.summary()
    items = proposal.items_of(summary)
    assert items, "the plan has lines"
    assert all(proposal.toggleable(item) for item in items), [
        item["id"] for item in items if not proposal.toggleable(item)]
    assert proposal.numbered(summary) == items
    # The statements are still said, as notes.
    notes = [n for s in summary["sections"] for n in s.get("notes") or ()]
    assert any(n.startswith("Kept in a locked vault on this computer.")
               for n in notes)


def test_without_the_needs_step_the_statements_stay_as_before(monkeypatch,
                                                              tmp_path):
    from test_wizard_needs_step import no_engine
    from puffpai.wizard import service
    pinned_machine(monkeypatch, tmp_path)
    no_engine(monkeypatch)
    answer_questions(GOALS["minecraft"][0])
    summary = service.summary()
    assert summary["jobs"] == [] and summary["jobs_heading"] == ""
    assert any(not proposal.toggleable(item)
               for item in proposal.items_of(summary))


def test_the_extras_are_kept_and_cleaned():
    item = proposal.validate_item({
        "id": "apps_code:github:a/b", "section": "apps_code", "label": "B",
        "detail": "", "size_bytes": 10, "outside_host": "codeload.github.com",
        "default_on": False, "needs_yes": True,
        "apply": {"kind": "code_project", "ref": "github:a/b"},
        "need": "j5", "why": "helps with: the list", "might_help": True,
        "requires": ["box_setup", "Not A Kind", "box_setup"],
        "facts": {"stars": 2, "changed": "2026-06", "license": "MIT",
                  "hosts_all": ["codeload.github.com", "not a host"],
                  "size_bytes": -1, "box": False, "script": "<b>x</b>"}})
    assert item["need"] == "j5"
    assert item["why"] == "helps with: the list"
    assert item["might_help"] is True
    assert item["requires"] == ["box_setup"]
    assert item["facts"] == {"stars": 2, "changed": "2026-06",
                             "license": "MIT",
                             "hosts_all": ["codeload.github.com"],
                             "box": False}
    # A wrong shape is dropped, not guessed.
    bad = proposal.validate_item({
        "id": "apps_code:github:a/c", "section": "apps_code", "label": "C",
        "apply": {"kind": "code_project", "ref": "x"},
        "need": "J 5 <script>", "why": 7, "might_help": "yes",
        "requires": "box_setup", "facts": ["x"]})
    assert not set(proposal.EXTRA_KEYS) & set(bad)


@pytest.mark.parametrize("kind", proposal.R3_APPLY_KINDS)
def test_the_new_apply_kinds_are_accepted(kind):
    item = proposal.validate_item({
        "id": f"apps_code:test:{kind}", "section": "apps_code", "label": "X",
        "apply": {"kind": kind, "ref": "r"}})
    assert item["apply"]["kind"] == kind


@pytest.mark.parametrize("section", ["apps_code", "pictures", "box"])
def test_the_new_sections_follow_the_brain(section):
    assert proposal.SECTIONS.index(section) > proposal.SECTIONS.index(
        "thinking")
    assert proposal.section_order()[-1] == "box"


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_decide_keeps_the_extras(planned, goal_id):
    service, _calls, kind = planned(goal_id)
    summary = service.summary()
    state = proposal.decide(summary, changes={"on": {}})
    row = next(r for r in state["items"] if r["id"] == _app_id(kind))
    assert row["need"] == "j1" and row["why"] == "helps with: the plan"
    assert row["requires"] == ["box_setup"] and row["might_help"] is False
    assert row["facts"]["stars"] == 566 and "junk" not in row["facts"]
    # Only the tick is the owner's to send: an extra in the changes is not
    # a thing the plan offers to change.
    with pytest.raises(proposal.ProposalError):
        proposal.decide(summary, changes={"on": {"facts": True}})


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_the_box_comes_with_the_lines_that_need_it(planned, goal_id):
    service, calls, kind = planned(goal_id)
    app = _app_id(kind)
    summary = service.summary()
    on = {item["id"]: item["on"] for item in proposal.items_of(summary)}
    # The app is ticked, so the box it needs is ticked too, and drawn last.
    assert on[app] is True and on[BOX] is True
    assert summary["sections"][-1]["id"] == "box"

    # Untick the box: everything that needs it is off, and says why.
    off = service.summary({"on": {BOX: False}})
    items = {item["id"]: item for item in proposal.items_of(off)}
    assert items[BOX]["on"] is False and items[app]["on"] is False
    assert items[app]["off_line"].endswith(
        "Needs the locked box, so it is off too.")
    state = proposal.decide(off, changes={"on": {BOX: False}})
    assert state["on"][app] is False

    # Untick the only line that needs it: the box is not set up for nothing.
    alone = service.summary({"on": {app: False}})
    items = {item["id"]: item for item in proposal.items_of(alone)}
    assert items[BOX]["on"] is False
    # Ticking the line again brings the box back.
    back = proposal.decide(alone, changes={"on": {app: True}})
    assert back["on"][app] is True and back["on"][BOX] is True
    # None of this searched again.
    assert len(calls["plan_items"]) == 1


def test_requires_rule_directly():
    rows = [{"id": "a", "on": True, "apply": {"kind": "code_project"},
             "requires": ["box_setup"]},
            {"id": "b", "on": False, "apply": {"kind": "cli_tool"},
             "requires": ["box_setup"]},
            {"id": "box", "on": False, "apply": {"kind": "box_setup"}}]
    assert proposal.apply_requires(rows, {}) == set()
    assert [r["on"] for r in rows] == [True, False, True]
    assert proposal.apply_requires(rows, {"box": False}) == {"a"}
    assert [r["on"] for r in rows] == [False, False, False]
    # A line whose needed kind is not on the plan at all is left as it is.
    lone = [{"id": "c", "on": True, "apply": {"kind": "x"},
             "requires": ["box_setup"]}]
    assert proposal.apply_requires(lone, {}) == set() and lone[0]["on"]


# -- no word-matched library once the jobs are known ---------------------------

#: A library book the interview's word matching finds only because the goal
#: happens to contain the word "ideas" (a 257 MB TED
#: collection on a plan that was not about talks at all).
WORD_MATCHED_BOOK = {
    "id": "kiwix:test_ideas_talks", "label": "Talks about ideas",
    "selectable": True, "matched_terms": ["ideas"], "bytes": 257_000_000,
    "license": "CC BY-NC-ND", "commercial_use": False,
    "what_it_is": "A collection of talks about ideas.", "kiwix_book": True}


def _word_matched_sources(monkeypatch):
    real = proposal.previews

    def with_book(**kwargs):
        out, notes = real(**kwargs)
        out = dict(out)
        out["sources"] = {"items": [dict(WORD_MATCHED_BOOK)],
                          "selected": [WORD_MATCHED_BOOK["id"]]}
        return out, notes
    monkeypatch.setattr(proposal, "previews", with_book)


def _library_lines(summary):
    return [item for item in proposal.items_of(summary)
            if item["id"].startswith("knowledge:registry:")
            or item["id"] in ("knowledge:more", "knowledge:none_found")]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_no_word_matched_book_once_the_jobs_are_known(monkeypatch, tmp_path,
                                                      goal_id):
    from puffpai.wizard import service
    goal, kind = GOALS[goal_id]
    goal = f"{goal} It should also come up with ideas."
    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, kind)
    _word_matched_sources(monkeypatch)
    answer_questions(goal)
    service.first_look()
    wait_for(service.needs)
    looked(service, {"look": "online"})
    summary = service.summary()
    assert _library_lines(summary) == []
    assert not any("ideas" in item["label"].lower()
                   for item in proposal.items_of(summary))
    # "Looks good" therefore asks the library card for nothing.
    state = proposal.decide(summary, changes={"on": {}})
    assert proposal.approved_refs(state, "source_fetch") == []


def test_without_the_jobs_the_word_matched_book_is_still_listed(monkeypatch,
                                                                tmp_path):
    """The control: the same book reaches the plan when there is no needs
    step, so the test above is not passing on an empty preview."""
    from test_wizard_needs_step import no_engine
    from puffpai.wizard import service
    goal, _kind = GOALS["minecraft"]
    pinned_machine(monkeypatch, tmp_path)
    no_engine(monkeypatch)
    _word_matched_sources(monkeypatch)
    answer_questions(f"{goal} It should also come up with ideas.")
    summary = service.summary()
    ids = [item["id"] for item in _library_lines(summary)]
    assert f"knowledge:registry:{WORD_MATCHED_BOOK['id']}" in ids
