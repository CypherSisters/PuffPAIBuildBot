"""People's names, health words and exact places never go out with "look online".

add_more.scrub kept names, health words and town names,
so "books about my son Jake Miller's leukemia at St Jude" offered to send
book, son, jake, miller, leukemia, jude. The owner saw the words before
saying yes, but the rule is that personal details never cause an outside
call. Now they are taken out of everything that can leave: the online search,
the Wikipedia book made from the words, and the "It sends only these words"
line. The search of the lists saved on this computer still uses them (it
sends nothing), and the owner still sees the exact final words first.
"""
from __future__ import annotations

import pytest

from add_more_support import AUTH_KEY, DEVICE_A, built
from puffpai import add_more, add_more_api, api_server, plain_words
from puffpai.security import consent_tickets, egress_doors, outside_switches

AUTH = {"Authorization": f"Bearer {AUTH_KEY}"}
DEV_A = {**AUTH, "X-Puff-Device-Id": DEVICE_A}

#: (the owner's words, words that must not be sent, words that still may be)
CASES = [
    ("books about my son Jake Miller's leukemia at St Jude",
     {"jake", "miller", "leukemia", "jude", "son"}, set()),
    ("stuff about my wife Maria Gonzalez divorce lawyer",
     {"maria", "gonzalez", "wife"}, {"lawyer"}),
    ("books on HIV for me", {"hiv"}, set()),
    ("I live at 42 Elm Street Springfield and want gardening books",
     {"springfield", "elm", "street"}, {"gardening"}),
    ("books about my son jake's asthma", {"jake", "asthma", "son"}, set()),
    ("a book for my kid in Springfield about trains", {"springfield", "kid"},
     {"train"}),
    ("our dog named Rex and his hip surgery", {"rex", "surgery"}, {"hip"}),
]


@pytest.mark.parametrize("words,never,still", CASES)
def test_personal_words_are_not_in_the_words_offered_to_send(words, never, still):
    private = add_more.private_words(words)
    sends = add_more.topic_words(add_more.without_words(add_more.scrub(words), private))
    assert not (set(sends) & never), sends
    assert still <= set(sends), sends


@pytest.mark.parametrize("words", [
    "add dinosaur fossils", "black holes", "books about Beethoven",
    "the history of Rome", "volcanoes in Iceland", "my daughter loves horses",
])
def test_ordinary_topics_are_still_sent_as_typed(words):
    private = add_more.private_words(words)
    sends = add_more.topic_words(add_more.without_words(add_more.scrub(words), private))
    for word in ("dinosaur", "fossil", "black", "hole", "beethoven", "rome",
                 "volcano", "iceland", "horse"):
        if word in add_more.topic_words(words):
            assert word in sends, (words, sends)


@pytest.fixture
def online(monkeypatch):
    """A bot that may go online, with the online search spied on."""
    from puffpai.knowledge import find_more
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    outside_switches.forget()
    consent_tickets.reset()
    add_more.reset()
    add_more_api._finds.clear()  # noqa: SLF001
    egress_doors.reset_cache()
    calls = []

    def fake_search(words, *, live=None, **kw):
        calls.append((words, live))
        return {"searched": "live", "candidates": []}

    monkeypatch.setattr(find_more, "search", fake_search)
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda f: None)
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    yield calls
    add_more.reset()
    consent_tickets.reset()


def test_the_line_shown_before_yes_names_only_safe_words(online):
    built(name="Rexy")
    words = "books about my son Jake Miller's leukemia at St Jude"
    session = add_more.find(words, via="terminal", client="terminal:t")
    line = add_more.online_line(session, "terminal")
    for word in ("jake", "miller", "leukemia", "jude", "son"):
        assert word not in line.lower()
    assert online == []                         # nothing left yet
    assert "could be about you" in " ".join(session["notes"])
    assert not plain_words.check(" ".join(session["notes"]))
    assert "keep_out" not in add_more.public(session, "browser")


def test_all_personal_words_means_nothing_is_offered_to_send(online):
    built(name="Rexy")
    session = add_more.find("my son Jake's leukemia", via="terminal",
                            client="terminal:t")
    assert session["sends"] == []
    assert session["online_possible"] is False
    assert add_more.online_line(session, "terminal") == ""
    assert add_more.public(session, "browser")["online"]["sends"] == []


def test_the_online_search_itself_gets_only_safe_words(online):
    built(name="Rexy")
    add_more.find("books about my wife Maria Gonzalez and gardening",
                  via="terminal", client="terminal:t", online=True)
    assert online, "the online search ran"
    sent = " ".join(w for w, _live in online).lower()
    assert "maria" not in sent and "gonzalez" not in sent
    assert "gardening" in sent


def test_the_chat_look_online_step_keeps_them_out_too(online, monkeypatch):
    built(name="Rexy")
    api_server.app.config.update(TESTING=True)
    client = api_server.app.test_client()
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: pytest.fail("the model was asked"))
    first = client.post("/chat/send", headers=DEV_A, json={
        "message": "add books about my son Jake Miller's zxqv blorf to my bot"}
    ).get_json()["response"]
    assert "jake" not in first.lower().split("sends only these words")[-1]
    client.post("/chat/send", headers=DEV_A, json={"message": "look online"})
    sent = " ".join(w for w, _live in online).lower()
    assert online and "jake" not in sent and "miller" not in sent


def test_the_book_made_from_the_words_is_named_and_made_without_them(online):
    built(name="Rexy")
    session = add_more.find("books about my son Jake Miller's volcanoes",
                            via="terminal", client="terminal:t", online=True)
    fetchable = [i for i in session["items"]
                 if add_more.can_apply(i["apply"]["kind"])
                 or str(i["apply"].get("ref") or "").startswith("wikipedia:")]
    assert fetchable, "the Wikipedia book made from the safe words is offered"
    for item in fetchable:
        text = (item["label"] + " " + str(item["apply"].get("ref") or "")).lower()
        assert "jake" not in text and "miller" not in text, item["label"]
    wiki = [i for i in fetchable
            if str(i["apply"].get("ref") or "").startswith("wikipedia:")]
    assert wiki and all("volcano" in (i["label"] + i["apply"]["ref"]).lower()
                        for i in wiki), [i["label"] for i in wiki]
