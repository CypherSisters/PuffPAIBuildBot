"""GET/POST /wizard/needs (I-5).

The blueprint is registered on a small Flask app here: registering it on the
real backend is one line in ``api_server``. The engine is the fake
from :mod:`test_wizard_needs_step` (pinned signatures), so nothing reaches
the network.

Run:
    pytest tests/test_wizard_needs_api.py
"""
from __future__ import annotations

import logging
import os
import sys
import time

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_wizard_needs_step import (  # noqa: E402
    GOALS, answer_questions, install_fakes, no_engine, pinned_machine)


@pytest.fixture(autouse=True)
def _parked_needs_step(monkeypatch):
    """The needs step is parked behind "Finding things for your bot".
    These tests keep the parked step working,
    so they run the wizard as a build without the find step."""
    from puffpai.wizard import service
    monkeypatch.setattr(service, "finding_engine", lambda: None)


SHAPE = {"title", "lines", "jobs", "held", "private_left_out", "subject",
         "sends", "helper", "done", "phase", "not_reached"}


def _client():
    from puffpai.wizard import needs_api
    app = Flask(__name__)
    app.register_blueprint(needs_api.bp)
    app.config.update(TESTING=True)
    return app.test_client()


def _until(web, test, timeout=20.0):
    end = time.time() + timeout
    while True:
        reply = web.get("/wizard/needs")
        assert reply.status_code == 200, reply.json
        if test(reply.json):
            return reply.json
        if time.time() > end:
            raise AssertionError(reply.json)
        time.sleep(0.05)


@pytest.fixture
def ready(monkeypatch, tmp_path):
    def make(goal_id, internet=True):
        goal, kind = GOALS[goal_id]
        pinned_machine(monkeypatch, tmp_path, internet=internet)
        calls = install_fakes(monkeypatch, kind)
        # The internet check the wizard makes when it looks at this computer.
        answer_questions(goal)
        from puffpai.wizard import service
        service.first_look()
        return _client(), calls, goal, kind
    return make


def test_the_blueprint_is_the_pinned_name():
    from puffpai.wizard import needs_api
    assert needs_api.bp.name == "r3_needs"
    rules = {(r.rule, tuple(sorted(r.methods - {"HEAD", "OPTIONS"})))
             for r in _app_rules(needs_api.bp)}
    assert ("/wizard/needs", ("GET",)) in rules
    assert ("/wizard/needs", ("POST",)) in rules


def _app_rules(bp):
    app = Flask(__name__)
    app.register_blueprint(bp)
    return [r for r in app.url_map.iter_rules() if r.rule == "/wizard/needs"]


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_get_and_post_answer_the_pinned_shape(ready, goal_id):
    web, calls, _goal, kind = ready(goal_id)
    first = web.get("/wizard/needs").json
    assert SHAPE <= set(first)
    view = _until(web, lambda v: v["done"])
    assert SHAPE <= set(view)
    assert view["phase"] == "needs"
    assert view["title"] == f"Finding out what a {kind} bot needs"
    assert [j["id"] for j in view["jobs"]] == ["j1", "j2", "j3", "j4"]
    assert all(j["ticked"] for j in view["jobs"])
    assert [j["chip"] for j in view["jobs"]] == [
        "you asked", "you asked", "you asked", f"most {kind} bots have this"]
    assert view["held"] == [{"word": "instagram", "why": "brand",
                             "allowed": False}]
    assert view["held_line"] == "Not searched unless you tick it:"
    assert view["private_left_out"] == ["sister", "kids"]
    assert view["subject"] == {"phrase": kind, "notes": []}
    assert view["sends"][0] == kind
    assert view["before_button"][0] == f"Searching with: {kind}"
    assert view["before_button"][1].startswith("and these words for your jobs: ")
    assert view["before_button"][2] == (
        "BuildBot looks on this computer first, then asks GitHub and "
        "Wikimedia Commons.")
    # What goes, where, and only on her press.
    assert view["before_button"][-1] == (
        "Only these words go to those sites, and only when you press Start "
        "looking. Nothing leaves your computer without your consent.")
    assert [b["label"] for b in view["buttons"]] == [
        "Start looking", "Only look on this computer"]
    assert len(calls["run"]) == 1, "one engine run per wizard run"

    # Untick one job, add one, allow the held word: the same shape back.
    posted = web.post("/wizard/needs", json={
        "ticked": ["j1", "j2", "j4"], "added": ["Keep a guest list"],
        "allowed_words": ["instagram"]})
    assert posted.status_code == 200, posted.json
    view = posted.json
    assert SHAPE <= set(view)
    assert [j["ticked"] for j in view["jobs"]] == [True, True, False, True, True]
    assert view["jobs"][-1]["chip"] == "you added"
    assert view["held"][0]["allowed"] is True

    # "Start looking": the parts phase, then the plan is /wizard/summary's.
    started = web.post("/wizard/needs", json={"look": "online"})
    assert started.status_code == 200 and started.json["phase"] == "parts"
    view = _until(web, lambda v: v["phase"] == "parts" and v["done"])
    assert view["title"] == "Looking for things your bot can use"
    assert f"GitHub: {kind} ... 2 fit" in view["lines"]
    assert view["lines"][-1] == ("Checking what fits your jobs ... 3 kept, "
                                 "7 left out")
    assert view["not_reached"] == ["j3"]
    assert calls["plan_items"][0]["allow_send"] is True
    # The list cannot change once looking has started.
    assert web.post("/wizard/needs", json={"ticked": ["j1"]}).status_code == 400


def test_look_here_sends_nothing(ready):
    web, calls, _goal, _kind = ready("minecraft")
    _until(web, lambda v: v["done"])
    assert web.post("/wizard/needs", json={"look": "here"}).status_code == 200
    _until(web, lambda v: v["phase"] == "parts" and v["done"])
    assert calls["plan_items"] == [{"online": True, "allow_send": False,
                                    "ticked": ["j1", "j2", "j3", "j4"]}]


def test_looking_starts_again_after_the_backend_restarts(ready):
    """The owner pressed Start looking and the backend stopped before it
    finished: the next GET looks again with her own answer instead of
    showing "Looking" for ever."""
    from puffpai.config import get_config
    from puffpai.wizard import service
    web, calls, _goal, _kind = ready("wedding")
    _until(web, lambda v: v["done"])

    def pressed(run):
        run.shared["needs"] = run.shared["needs_draft"]
        run.shared["needs_look"] = "here"
    assert service._update_run(get_config(), service._args(), pressed)
    service._NEEDS_LIVE.clear()          # what a restart forgets
    view = _until(web, lambda v: v["phase"] == "parts" and v["done"])
    assert view["lines"][-1].startswith("Checking what fits your jobs")
    assert calls["plan_items"] == [{"online": True, "allow_send": False,
                                    "ticked": ["j1", "j2", "j3", "j4"]}]
    assert service.flow()["next"] == "summary"


def test_an_offline_build_only_looks_here(ready):
    web, calls, _goal, _kind = ready("wedding", internet=False)
    view = _until(web, lambda v: v["done"])
    assert view["sends"] == [] and "sends" not in calls
    assert view["before_button"] == [
        "This build does not use the internet, so BuildBot looks only on "
        "this computer."]
    assert view["buttons"] == [{"value": "here", "label": "Start looking"}]
    # Even a request for online looking sends nothing from an offline build.
    web.post("/wizard/needs", json={"look": "online"})
    _until(web, lambda v: v["phase"] == "parts" and v["done"])
    assert calls["plan_items"][0]["allow_send"] is False


def test_with_nowhere_outside_to_ask_nothing_is_shown_as_sent(monkeypatch,
                                                             tmp_path):
    """No where-to-look registry in this build: no "Searching with" line,
    because nothing would be searched outside."""
    goal, kind = GOALS["minecraft"]
    pinned_machine(monkeypatch, tmp_path)
    calls = install_fakes(monkeypatch, kind, where=False)
    answer_questions(goal)
    from puffpai.wizard import service
    service.first_look()
    web = _client()
    view = _until(web, lambda v: v["done"])
    assert view["sends"] == [] and "sends" not in calls
    assert view["before_button"] == ["BuildBot looks only on this computer."]
    assert view["buttons"] == [{"value": "here", "label": "Start looking"}]


@pytest.mark.parametrize("body,status", [
    ({"look": "everywhere"}, 400),
    ({"ticked": "j1"}, 400),
    ({"ticked": ["j99"]}, 400),
    ({"added": ["x" * 500]}, 400),
    ({"allowed_words": ["sister"]}, 400),
    ([1, 2], 400),
])
def test_bad_answers_are_refused(ready, body, status):
    web, calls, _goal, _kind = ready("wedding")
    _until(web, lambda v: v["done"])
    reply = web.post("/wizard/needs", json=body)
    assert reply.status_code == status
    assert reply.json["success"] is False and reply.json["message"]
    assert "plan_items" not in calls


def test_no_goal_text_in_any_log_line(ready, caplog):
    web, _calls, goal, _kind = ready("wedding")
    caplog.set_level(logging.DEBUG)
    _until(web, lambda v: v["done"])
    web.post("/wizard/needs", json={"added": ["Plan the seating"]})
    web.post("/wizard/needs", json={"look": "online"})
    _until(web, lambda v: v["phase"] == "parts" and v["done"])
    words = {w.strip(".,'").lower() for w in goal.split()} - {
        "i", "to", "a", "be", "the", "and", "is", "my", "want", "need", "able",
        "build", "bot", "i'll", "plan"}
    for record in caplog.records:
        message = record.getMessage().lower()
        for word in words:
            assert f" {word} " not in f" {message} ", (word, message)
        assert "seating" not in message
    # The run record's step log carries no goal words either.
    from puffpai.wizard import runner as wizard_runner, service
    from puffpai.config import get_config
    run = wizard_runner.Runner(args=service._args(), cfg=get_config(),
                               non_interactive=True)
    assert run.load_state()
    notes = " ".join(str(row.get("note") or "") for row in run.log).lower()
    assert not any(word in notes.split() for word in ("brother", "engaged",
                                                      "vendors", "seating"))


def test_without_the_engine_the_route_says_not_in_this_build(monkeypatch,
                                                             tmp_path):
    pinned_machine(monkeypatch, tmp_path)
    no_engine(monkeypatch)
    answer_questions(GOALS["minecraft"][0])
    web = _client()
    reply = web.get("/wizard/needs")
    assert reply.status_code == 404
    assert reply.json["message"] == ("Finding out what your bot needs is not "
                                      "in this build yet.")
    assert web.post("/wizard/needs", json={"look": "online"}).status_code == 404


def test_before_the_goal_the_route_asks_for_it(monkeypatch, tmp_path):
    pinned_machine(monkeypatch, tmp_path)
    install_fakes(monkeypatch, "anything")
    reply = _client().get("/wizard/needs")
    assert reply.status_code == 400
    assert reply.json["message"] == "Say what kind of bot you want first."


# -- the reading helper offer ----------------------------------------------------

import types  # noqa: E402

OFFER = ("BuildBot's reading helper is not on this computer. It lets BuildBot "
         "tell which finds really fit your jobs. Get it now? (83 MB, from the "
         "same place as when BuildBot was installed)")


def _helper_missing(monkeypatch, *, fetch=True, state="ready"):
    """The engine says the helper is missing; its get_helper redeems the
    ticket it was handed, the way the real download must."""
    from puffpai.security import consent_tickets
    mod = sys.modules["puffpai.wizard.needs"]
    real_run = mod.run
    got: list = []

    def run(goal, **kwargs):
        result = real_run(goal, **kwargs)
        result["helper"] = "missing"
        return result
    mod.run = run
    if fetch:
        def get_helper(*, ticket, device_id, progress):
            ok, _ = consent_tickets.redeem(ticket, device_id, "add_on",
                                           "reading_helper")
            got.append((ok, device_id))
            progress("Getting the reading helper ... done")
            return {"state": state, "line": "The reading helper is ready."}
        mod.get_helper = get_helper
    else:
        del mod.get_helper
    return got


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_a_missing_helper_is_offered_once_online(ready, monkeypatch, goal_id):
    web, _calls, _goal, _kind = ready(goal_id)
    _helper_missing(monkeypatch)
    view = _until(web, lambda v: v["done"])
    assert view["helper"] == "missing"
    assert view["helper_offer"] == OFFER
    assert [b["label"] for b in view["helper_buttons"]] == ["Get it", "Not now"]


def test_offline_there_is_no_offer(ready, monkeypatch):
    web, _calls, _goal, _kind = ready("wedding", internet=False)
    _helper_missing(monkeypatch)
    view = _until(web, lambda v: v["done"])
    assert view["helper_offer"] == "" and view["helper_buttons"] == []


def test_not_now_is_remembered(ready, monkeypatch):
    web, _calls, _goal, _kind = ready("minecraft")
    _helper_missing(monkeypatch)
    _until(web, lambda v: v["done"])
    reply = web.post("/wizard/needs", json={"get_helper": False})
    assert reply.status_code == 200, reply.json
    assert reply.json["helper"] == "declined"
    assert reply.json["helper_offer"] == ""


@pytest.mark.parametrize("goal_id", sorted(GOALS))
def test_get_it_mints_a_ticket_for_the_device_asking(ready, monkeypatch, goal_id):
    from puffpai.wizard import service
    web, _calls, _goal, _kind = ready(goal_id)
    got = _helper_missing(monkeypatch)
    _until(web, lambda v: v["done"])
    view = service.needs_choose({"get_helper": True},
                                device_id="device:abcdefgh1234")
    assert got == [(True, "device:abcdefgh1234")]
    assert view["helper_line"] == "The reading helper is ready."
    assert view["helper_offer"] == "" and view["helper"] == "ready"


def test_get_it_without_a_paired_device_is_refused(ready, monkeypatch):
    web, _calls, _goal, _kind = ready("wedding")
    got = _helper_missing(monkeypatch)
    _until(web, lambda v: v["done"])
    reply = web.post("/wizard/needs", json={"get_helper": True})
    assert reply.status_code == 400
    assert reply.json["message"] == ("The reading helper could not be got right "
                                     "now. Nothing else changed.")
    assert got == []


def test_without_get_helper_in_this_build_the_line_is_honest(ready, monkeypatch):
    from puffpai.wizard import service
    web, _calls, _goal, _kind = ready("minecraft")
    _helper_missing(monkeypatch, fetch=False)
    _until(web, lambda v: v["done"])
    view = service.needs_choose({"get_helper": True},
                                device_id="device:abcdefgh1234")
    assert view["helper_line"] == ("Getting the reading helper is not in this "
                                   "build yet, so nothing was downloaded.")


def test_the_terminal_offers_it_and_gets_it_on_yes(monkeypatch):
    from puffpai.wizard import render_cli
    words = {"helper_offer": OFFER}
    printed: list[str] = []
    assert render_cli.helper_screen(words, ask=lambda _p: "yes",
                                    out=printed.append) is True
    assert render_cli.helper_screen(words, ask=lambda _p: "",
                                    out=printed.append) is False
    assert render_cli.helper_screen({"helper_offer": ""},
                                    ask=lambda _p: "yes") is None
    assert "Get it now?" in " ".join(printed)
