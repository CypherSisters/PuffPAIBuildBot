"""Chat answers from the bot's own live cards, and never claims the owner's gear.

Two findings:

* The car bot said there were no recalls while its own recalls card held two.
  Chat never read the cards. Now a question that fits a card carries that
  card's latest data into the model's turn, and a card with no data says so.
* "show me my telescope" read a stranger's forum post back as the owner's own
  telescope. A "my <thing>" question about a goal thing that is not connected
  is answered from the build record, and library passages are labelled as
  public material that is not about the owner.

Fake transport only; no model is called.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json

import pytest

from livedata_support import (FakeResponse, fake_network, feed_manifest, install,
                              set_posture)
from puffpai import build_abilities, feeds
from puffpai.security import egress_doors

RECALLS = {"results": [{"Component": "TRANSMISSION CONTROL MODULE",
                        "Summary": "may shift unexpectedly"},
                       {"Component": "TIRE BEAD", "Summary": "may separate"}]}
RECALL_CARD = {"kind": "list", "title": "Recalls for your car",
               "items": "results", "item_title": "Component",
               "item_detail": "Summary"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    yield
    import shutil
    from pathlib import Path
    from puffpai import app_registry
    shutil.rmtree(Path(app_registry.user_apps_dir(), "nhtsa"), ignore_errors=True)
    app_registry.rescan()
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def _recalls_app(monkeypatch):
    set_posture("enumerated")
    manifest = feed_manifest("nhtsa", card=RECALL_CARD, image_host=None)
    manifest["name"] = "NHTSA recalls"
    install(manifest)
    feeds.set_allowed("nhtsa", True)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(RECALLS).encode()))
    return net


def test_a_question_about_a_card_carries_its_data(monkeypatch):
    _recalls_app(monkeypatch)
    text = feeds.chat_context("show me the NHTSA recalls")
    assert "TRANSMISSION CONTROL MODULE" in text and "TIRE BEAD" in text
    assert "NHTSA recalls" in text and "fetched" in text


def test_an_unrelated_question_carries_no_card(monkeypatch):
    _recalls_app(monkeypatch)
    assert feeds.chat_context("how do I change a tyre") == ""


def test_a_card_with_no_data_says_so(monkeypatch):
    set_posture("enumerated")
    manifest = feed_manifest("nhtsa", card=RECALL_CARD, image_host=None)
    manifest["name"] = "NHTSA recalls"
    install(manifest)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, b"{}"))
    text = feeds.chat_context("any recalls on my car?")
    assert "has no data right now" in text
    assert net.calls == []  # the owner never said yes, so nothing was fetched


def test_the_model_turn_gets_the_card_and_the_library_label(monkeypatch):
    from puffpai import api_server
    _recalls_app(monkeypatch)
    monkeypatch.setattr(api_server, "_get_rag_context", lambda q, n_results=12, owner_docs=True: {
        "context_text": "You have an Orion SpaceProbe 76mm.", "sources": []})
    text, _sources = api_server._grounding_context("show me the NHTSA recalls")
    assert "Your own live cards" in text and "TIRE BEAD" in text
    assert "NOT about the owner" in text
    assert text.index("Your own live cards") < text.index("Orion")


TELESCOPE_ROWS = [build_abilities.row("goal:smart_telescope",
                                      "Work with your smart telescope",
                                      build_abilities.NOT_IN_THIS_BUILD)]


@pytest.mark.parametrize("message", [
    "show me my telescope", "is my smart telescope on?",
    "point my telescopes at mars"])
def test_my_thing_questions_find_the_unconnected_row(message):
    assert build_abilities.asked_goal_thing(message, TELESCOPE_ROWS) is not None


@pytest.mark.parametrize("message", [
    "what is the best telescope for kids", "how does a telescope work",
    "show me my dinosaurs"])
def test_other_questions_do_not(message):
    assert build_abilities.asked_goal_thing(message, TELESCOPE_ROWS) is None


def test_a_connected_thing_is_left_to_the_bot():
    rows = [dict(TELESCOPE_ROWS[0], status=build_abilities.AVAILABLE)]
    assert build_abilities.asked_goal_thing("show me my telescope", rows) is None


def test_my_telescope_is_answered_from_the_build_record(monkeypatch):
    from puffpai import api_server, build_manifest
    monkeypatch.setattr(build_manifest, "read_manifest", lambda *a, **k: {"x": 1})
    monkeypatch.setattr(build_abilities, "abilities",
                        lambda *a, **k: list(TELESCOPE_ROWS))
    answer = api_server._maybe_unconnected_thing_answer("show me my telescope")
    assert answer == ("I can't work with your smart telescope in this build yet. "
                      "It is not connected yet; it can be added later.")
