"""Add more: "look online" is real, and a miss is not a dead end.

Before the fix, after "add pterosaurs to my bot" found nothing, "yes look online too" went
to the model, which said "I can look online ... now. Here's what I found" and
nothing was fetched or logged. Now:

* nothing found on this computer asks "Look online too?" as a real question,
  and "look online" / "yes" runs the online search;
* a "look online" with nothing waiting is answered plainly, never by the model;
* the honesty guard replaces a model sentence that says it is looking online.

Also, when the online search finds nothing else, the Wikipedia book made from
the owner's words (the wizard's own "Wikipedia articles about ...") is offered,
named with the words as typed, and the owner's "yes" orders it. A place that
did not answer is said, not reported as "nothing found".
"""
from __future__ import annotations

import pytest

from add_more_support import AUTH_KEY, DEVICE_A, built, guard_network
from puffpai import add_more, add_more_api, api_server, honesty, plain_words
from puffpai.security import consent_tickets, egress_doors, outside_switches

AUTH = {"Authorization": f"Bearer {AUTH_KEY}"}
DEV_A = {**AUTH, "X-Puff-Device-Id": DEVICE_A}
TERMINAL = "terminal:test-0000000000"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    outside_switches.forget()
    consent_tickets.reset()
    add_more.reset()
    add_more_api._finds.clear()  # noqa: SLF001
    egress_doors.reset_cache()
    api_server.app.config.update(TESTING=True)
    asked_model = []

    def model(*a, **k):
        asked_model.append(a[0] if a else "")
        return {"response": "I can look online for more about it now. "
                            "Here's what I found: made up.",
                "sources": [], "suggestions": []}

    monkeypatch.setattr(api_server, "_query_ollama_direct", model)
    c = api_server.app.test_client()
    c.asked_model = asked_model
    yield c
    add_more.reset()
    consent_tickets.reset()
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


@pytest.fixture
def empty_online(monkeypatch):
    """The online search answers, with nothing, and says one place failed."""
    from puffpai.knowledge import find_more
    calls = []

    def fake_search(words, *, live=None, **kw):
        calls.append((words, live))
        return {"searched": "live", "candidates": [],
                "note": "Project Gutenberg did not answer, so it was not searched."}

    monkeypatch.setattr(find_more, "search", fake_search)
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda f: None)
    return calls


def _say(client, message):
    return client.post("/chat/send", headers=DEV_A,
                       json={"message": message}).get_json()["response"]


def test_nothing_found_asks_to_look_online_and_yes_really_looks(client, empty_online):
    from puffpai.knowledge import find_more
    built(name="Rexy")
    first = _say(client, "add zxqv blorf to my bot")
    assert "I couldn't find anything I can add for \"zxqv blorf\"" in first
    assert "Look online too? It sends only these words: zxqv, blorf" in first
    assert empty_online == []  # nothing left the house yet

    second = _say(client, "yes look online too")
    assert empty_online == [("zxqv blorf", True)]
    assert client.asked_model == []  # the model never narrated a lookup
    # the online search found nothing, so the book made from the words is it
    assert "Wikipedia articles about zxqv blorf" in second
    assert "Project Gutenberg did not answer" in second
    assert plain_words.check(second) == []

    third = _say(client, "yes")
    assert "being fetched and learned in the background" in third
    assert "wikipedia:en:zxqv-blorf" in find_more.pending_orders()
    assert client.asked_model == []


def test_a_bare_yes_to_look_online_too_is_a_yes(client, empty_online):
    built(name="Rexy")
    _say(client, "add zxqv blorf to you")
    _say(client, "yes")
    assert empty_online == [("zxqv blorf", True)]


@pytest.mark.parametrize("message", ["look online", "yes look online too",
                                     "ok, search online please"])
def test_look_online_with_nothing_asked_is_answered_plainly(client, message):
    built(name="Rexy")
    got = _say(client, message)
    assert got == add_more.NOTHING_TO_LOOK_UP
    assert client.asked_model == []
    assert plain_words.check(got) == []


def test_an_offline_bot_is_never_offered_the_online_step(monkeypatch):
    from add_more_support import built_offline
    built_offline()
    guard_network(monkeypatch)
    session = add_more.find("zxqv blorf", via="chat", client=TERMINAL)
    assert not session["online_possible"]
    assert add_more.online_line(session, "chat") == ""


def test_nothing_found_uses_the_words_as_typed_and_says_who_did_not_answer(
        monkeypatch, empty_online):
    built()
    session = add_more.find("black holes", via="chat", client=TERMINAL,
                            online=True)
    session["items"] = []  # as if no book were found at all
    lines = add_more.nothing_found_lines(session, "chat")
    assert lines[0] == "I couldn't find anything I can add for \"black holes\"."
    assert "Project Gutenberg did not answer, so it was not searched." in lines


def test_the_honesty_guard_replaces_a_pretend_lookup():
    for text in ("I can look online for more information about pterosaurs now. ",
                 "Here’s what I found online: pterosaurs flew.",
                 "I looked it up online and they flew."):
        fixed, count = honesty.scrub_claims(text)
        assert count == 1, text
    kept, count = honesty.scrub_claims("You can look online for more.")
    assert count == 0


def test_a_chat_with_no_device_is_told_where_to_look_online(monkeypatch):
    """A caller with no paired device gets a new name every message, so a
    "look online too?" asked of it could never be answered: say where instead."""
    built(name="Rexy")
    client = "anon:" + "0" * 32
    first = add_more.chat_turn("add zxqv blorf to you", surface="chat",
                               client=client)
    assert "Look online too?" not in first
    assert first.endswith(add_more.only_paired_line())
    again = add_more.chat_turn("look online", surface="chat", client=client)
    assert again == add_more.only_paired_line()
    assert plain_words.check(again) == []
