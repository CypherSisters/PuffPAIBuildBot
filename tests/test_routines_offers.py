"""Offers: "would you like a daily <topic> update?", built from what the build has.

Six different goals, no topic table: the topic words come from the owner's own
goal sentence, and whether an offer exists at all depends only on whether the
build has live info or a library. Chat answers are the real offer, not model
text, and a yes in chat records consent and schedules the routine.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os

import pytest

from puffpai import announcer, build_manifest, offers, routines
from puffpai.security import consent_gate
from puffpai import plain_words  # noqa: E402  (the one banned list)

G1 = ("I want a space bot that knows everything about space, shows me cool space "
      "stuff, and works with my smart telescope")
G2 = "I love dinosaurs, I want a bot that knows every dinosaur"
G3 = "Help me keep my 2014 Honda Civic running - maintenance, recalls, how-tos"
G4 = "A garden helper for my vegetable beds in Ohio"
G5 = "A knitting and crochet buddy with patterns and tips"
G6 = "A music bot that knows bands and albums"

#: On top of plain_words' one list: this area's internal names.
JARGON_EXTRA = ("routine:", "daily_update")


@pytest.fixture(autouse=True)
def lane(tmp_path, monkeypatch):
    led = consent_gate.ConsentLedger(state_path=str(tmp_path / "c.json"),
                                     audit_path=str(tmp_path / "c.jsonl"))
    monkeypatch.setattr(consent_gate, "_default_ledger", led)
    routines.set_feed_reader(lambda: None)
    routines.set_library_picker(lambda terms: None)
    yield led
    routines.set_feed_reader(None)
    routines.set_library_picker(None)


def _goal(goal: str, name: str = "HelperBot"):
    path = build_manifest.manifest_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"schema": "build_manifest/v2", "build": {"name": name},
                   "surfaces": {"goal": goal}}, fh)


def _has(live=True, library=False):
    routines.set_feed_reader(
        (lambda: [{"id": "x", "name": "Recalls", "items": [{"title": "t"}]}])
        if live else (lambda: None))
    routines.set_library_picker(
        (lambda terms: {"title": "A", "first_sentence": "A long enough sentence here.",
                        "credit": "C"}) if library else (lambda terms: None))


@pytest.mark.parametrize("goal,topic", [
    (G1, "space"), (G2, "dinosaur"), (G3, "2014 Honda Civic"), (G4, "garden"),
    (G5, "knitting and crochet"), (G6, "music")])
def test_topic_comes_from_the_owners_own_words(goal, topic):
    assert offers.topic_phrase(goal) == topic


@pytest.mark.parametrize("goal,expected", [
    (G2, "Would you like a daily dinosaur update at 8 am?"),
    (G3, "Would you like a daily 2014 Honda Civic update at 8 am?"),
    (G4, "Would you like a daily garden update at 8 am?"),
])
def test_offer_question_for_different_goals(goal, expected):
    _goal(goal, name="HondaBot")
    _has(live=True)
    offer = offers.current_offer()
    assert offer["question"] == expected
    assert "the latest from Recalls" in offer["detail"]
    assert plain_words.is_plain(offer["question"] + "\n" + offer["detail"],
                                also=JARGON_EXTRA)


def test_no_offer_when_the_build_has_nothing_to_say():
    _goal(G4)
    _has(live=False, library=False)
    assert offers.current_offer() is None


def test_a_library_alone_is_enough_for_an_offer():
    _goal(G2)
    _has(live=False, library=True)
    offer = offers.current_offer()
    assert offer and "one thing from your bot's library" in offer["detail"]


def test_accepting_records_the_yes_and_schedules_it(lane):
    _goal(G4)
    _has(live=True)
    result = offers.accept(offers.OFFER_ID, by="owner")
    assert result["ok"] is True
    assert lane.status("routine:daily_update") == "approved"
    row = routines.get_routine("daily_update")
    assert row["daily_at"] == "08:00" and routines.is_runnable(row)
    assert "every day at 8 am" in result["message"]
    assert offers.current_offer() is None          # one offer at a time, and done


def test_declining_is_remembered():
    _goal(G4)
    _has(live=True)
    offers.decline(offers.OFFER_ID)
    assert offers.current_offer() is None
    assert routines.get_routine("daily_update") is None


def test_chat_asks_then_yes_accepts(lane):
    _goal(G2)
    _has(live=False, library=True)
    reply = offers.chat_answer("can you send me daily updates?")
    assert reply.startswith("Would you like a daily dinosaur update at 8 am?")
    assert reply.endswith("Just say yes or no.")
    assert offers.chat_answer("yes please").startswith("Done.")
    assert lane.status("routine:daily_update") == "approved"


def test_chat_no_declines():
    _goal(G6)
    _has(live=True)
    offers.chat_answer("could you give me music updates every morning?")
    assert offers.chat_answer("no thanks").startswith("Okay, no daily updates")
    assert routines.get_routine("daily_update") is None


def test_a_bare_yes_with_no_question_asked_is_not_ours():
    _has(live=True)
    assert offers.chat_answer("yes") is None
    assert offers.is_routines_message("yes") is False


def test_asking_on_a_build_with_nothing_is_an_honest_no():
    _has(live=False, library=False)
    reply = offers.chat_answer("send me daily updates")
    assert "can't send daily updates yet" in reply
    assert routines.get_routine("daily_update") is None


def test_asking_after_a_no_offers_again():
    _goal(G4)
    _has(live=True)
    offers.decline()
    assert offers.chat_answer("I'd like daily garden news").startswith(
        "Would you like a daily garden update")


def test_stop_and_what_do_i_have():
    _goal(G4)
    _has(live=True)
    offers.accept()
    listing = offers.chat_answer("what routines do I have?")
    assert "Daily update: every day at 8 am, switched on" in listing
    assert offers.chat_answer("stop daily updates").startswith("Okay, I stopped")
    assert routines.is_runnable(routines.get_routine("daily_update")) is False
    assert offers.chat_answer("send me daily updates").startswith(
        "Okay, your daily update is back on")


def test_what_workflows_on_a_fresh_build_is_none_not_an_invented_list():
    reply = offers.chat_answer("what workflows do I have")
    assert reply.startswith("You have no routines yet")


def test_first_chat_followup_is_asked_once():
    _goal(G4)
    _has(live=True)
    first = offers.chat_followup()
    assert first and first.startswith("Would you like a daily garden update")
    assert offers.chat_followup() is None


def test_the_first_offer_waits_when_the_reply_asks_a_question():
    """One question per reply, so her "yes"
    answers the bot's own question (the Calendar), not the daily update."""
    _goal(G4)
    _has(live=True)
    asked = "I already have a Calendar app. Want it on your home page?"
    assert offers.chat_followup(asked) is None
    assert offers.chat_answer("yes") is None          # the yes is not the offer's
    assert offers.followup_due() is True
    later = offers.chat_followup("Done, the Calendar is on your home page.")
    assert later and later.startswith("Would you like a daily garden update")


def test_the_chat_stream_adds_the_offer_only_under_a_reply_without_a_question():
    from puffpai import api_server, routines_api
    _goal(G4)
    _has(live=True)

    def _stream(words):
        chunks = [api_server._routines_sse_chunk(words, "c1"), "data: [DONE]\n\n"]
        return "".join(api_server._routines_wrap_openai_stream(
            iter(chunks), "", routines_api.reply_suffix, "c1"))

    assert "daily garden update" not in _stream("Want it on your home page?")
    assert "daily garden update" in _stream("Done, it is on your home page.")


def test_proposal_items_have_the_shared_shape():
    for goal in (G1, G3, G5):
        items = offers.proposal_items(goal, [])
        assert len(items) == 1
        item = items[0]
        assert set(item) == {"id", "section", "label", "detail", "size_bytes",
                             "outside_host", "default_on", "needs_yes", "apply"}
        assert item["section"] == "updates"
        assert item["default_on"] is False and item["outside_host"] is None
        assert len(item["label"]) <= 80
        assert plain_words.is_plain(item["label"] + "\n" + item["detail"],
                                    also=JARGON_EXTRA)
    assert offers.proposal_items(G5, [])[0]["label"] == \
        "A daily knitting and crochet update at 8 am"
    assert offers.proposal_items(G3, ["Honda Civic"])[0]["label"] == \
        "A daily 2014 Honda Civic update at 8 am"


def test_switching_the_summary_line_on_is_the_yes(lane):
    item = offers.proposal_items(G4, [])[0]
    assert offers.apply_item(item["apply"], by="owner")["ok"] is True
    assert lane.status("routine:daily_update") == "approved"


@pytest.mark.parametrize("message", [
    "give me my daily briefing", "daily update", "morning update", "daily digest",
    "today's update", "morning briefing please", "what did i miss", "tell me the news"])
def test_asking_for_a_briefing_now_is_not_a_subscription(message):
    """These belong to the briefing route; the updates offer must not take them."""
    assert offers.is_routines_message(message) is False


@pytest.mark.parametrize("message", [
    "can you send me daily space updates?", "I want dinosaur news every morning",
    "keep me posted about recalls", "sign me up for daily garden updates",
    "every evening give me a knitting update"])
def test_many_wordings_of_asking_for_updates(message):
    assert offers.asks_for_updates(message) is True
