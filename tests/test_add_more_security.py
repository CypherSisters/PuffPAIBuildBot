"""add_more security: privacy, consent tickets, ids only, Drawbridge, truth.

* Three private sentences cause ZERO outbound calls and no
  proposal, on the real chat route.
* A yes is a consent ticket: bound to the device, to exactly the lines shown,
  single use. A replayed ticket is refused. Voice and unpaired callers never
  say yes. No PIN anywhere.
* A request carries item ids from its own session; an address in the body is
  ignored and an id from anywhere else is refused.
* Drawbridge's block is never installed; the draft is thrown away.
* After adding, the real state is read back: a card whose website the bot may
  not use is "switched off", never "Added".
"""
from __future__ import annotations

import pytest

from add_more_support import (AUTH_KEY, DEVICE_A, DEVICE_B, PRIVATE_SENTENCES,
                              built, built_offline, guard_network,
                              item_by_host, item_numbered, nhtsa_network)
from puffpai import (add_more, add_more_api, api_server, feed_generator, feeds,
                     plain_words)
from puffpai.security import consent_tickets, egress_doors, outside_switches

AUTH = {"Authorization": f"Bearer {AUTH_KEY}"}
DEV_A = {**AUTH, "X-Puff-Device-Id": DEVICE_A}
DEV_B = {**AUTH, "X-Puff-Device-Id": DEVICE_B}
ANON = {**AUTH, "X-Puff-Anonymous": "1"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    outside_switches.forget()
    consent_tickets.reset()
    add_more.reset()
    add_more_api._finds.clear()  # noqa: SLF001 - the per-minute search count
    egress_doors.reset_cache()
    api_server.app.config.update(TESTING=True)
    yield api_server.app.test_client()
    add_more.reset()
    consent_tickets.reset()
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def _find(client, words, headers=DEV_A):
    got = client.post("/add/find", headers=headers, json={"words": words})
    assert got.status_code == 200, got.get_json()
    return got.get_json()


# -- privacy -------------------------------------------------------------------

@pytest.mark.parametrize("sentence", PRIVATE_SENTENCES)
def test_private_sentences_send_nothing_and_propose_nothing(client, monkeypatch,
                                                            sentence):
    built()
    seen = guard_network(monkeypatch)
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: {"response": "Okay.", "sources": [],
                                         "suggestions": []})
    got = client.post("/chat/send", headers=DEV_A, json={"message": sentence})
    assert got.status_code == 200
    assert seen == []
    assert add_more._sessions == {}  # noqa: SLF001 - no search was even made
    assert "Here is what I found" not in got.get_json()["response"]


def test_a_chat_add_request_searches_only_the_saved_lists(client, monkeypatch):
    built()
    seen = guard_network(monkeypatch)
    got = client.post("/chat/send", headers=DEV_A,
                      json={"message": "add dinosaur fossils to Rexy"})
    text = got.get_json()["response"]
    assert seen == []
    assert "Here is what I found" in text and "saved on this computer" in text
    assert "It sends only these words: dinosaur, fossil" in text
    assert plain_words.check(text) == []


# -- ids only, sessions bound to their device ------------------------------------

def test_an_address_in_the_body_is_ignored_and_foreign_ids_are_refused(client,
                                                                       monkeypatch):
    built()
    guard_network(monkeypatch)
    found = _find(client, "knitting and crochet")
    sid = found["session"]
    update = item_numbered(found, "updates")
    bad = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                      json={"ids": ["knowledge:kiwix:evil_book"]})
    assert bad.status_code == 400
    ok = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                     json={"ids": [update["id"]],
                           "url": "https://evil.example/steal"}).get_json()
    assert "evil" not in ok["sentence"]
    done = client.post(f"/add/{sid}/approve", headers=DEV_A,
                       json={"ids": [update["id"]], "ticket": ok["ticket"],
                             "url": "https://evil.example/steal",
                             "candidate": {"host": "evil.example"}}).get_json()
    assert done["results"][0]["status"] == "added"


def test_another_device_cannot_see_or_use_a_session(client, monkeypatch):
    built()
    guard_network(monkeypatch)
    found = _find(client, "knitting and crochet")
    sid = found["session"]
    update = item_numbered(found, "updates")
    assert client.get(f"/add/{sid}", headers=DEV_B).status_code == 404
    ticket = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                         json={"ids": [update["id"]]}).get_json()["ticket"]
    stolen = client.post(f"/add/{sid}/approve", headers=DEV_B,
                         json={"ids": [update["id"]], "ticket": ticket})
    assert stolen.status_code == 404
    from puffpai import routines
    assert routines.get_routine("daily_update") is None


def test_a_yes_is_one_ticket_for_exactly_the_lines_shown(client, monkeypatch):
    built()
    guard_network(monkeypatch)
    found = _find(client, "knitting and crochet")
    sid = found["session"]
    update = item_numbered(found, "updates")
    book = item_numbered(found, "knowledge")
    # no ticket: nothing
    none = client.post(f"/add/{sid}/approve", headers=DEV_A,
                       json={"ids": [update["id"]]})
    assert none.status_code == 403
    assert "the yes did not come with this request" in none.get_json()["message"]
    # a ticket for one line cannot add two
    ticket = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                         json={"ids": [update["id"]]}).get_json()["ticket"]
    wider = client.post(f"/add/{sid}/approve", headers=DEV_A,
                        json={"ids": [update["id"], book["id"]], "ticket": ticket})
    assert wider.status_code == 403
    assert "was for something else" in wider.get_json()["message"]
    # a used ticket is refused when replayed
    ticket = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                         json={"ids": [update["id"]]}).get_json()["ticket"]
    first = client.post(f"/add/{sid}/approve", headers=DEV_A,
                        json={"ids": [update["id"]], "ticket": ticket})
    assert first.status_code == 200
    replay = client.post(f"/add/{sid}/approve", headers=DEV_A,
                         json={"ids": [update["id"]], "ticket": ticket})
    assert replay.status_code == 403
    assert "already used" in replay.get_json()["message"]
    assert replay.get_json()["message"].startswith("Nothing was added")


def test_an_unpaired_caller_cannot_search_or_say_yes(client, monkeypatch):
    built()
    guard_network(monkeypatch)
    assert client.post("/add/find", headers=ANON,
                       json={"words": "dinosaurs"}).status_code == 403
    found = _find(client, "dinosaurs")
    sid = found["session"]
    line = item_numbered(found, "updates")
    got = client.post(f"/add/{sid}/confirm", headers=AUTH,
                      json={"ids": [line["id"]]})
    assert got.status_code == 403  # a direct caller with the key is not paired
    assert client.post("/add/find", json={"words": "x"}).status_code == 401


def test_voice_and_the_keyless_routes_never_search_or_say_yes(client, monkeypatch):
    built()
    seen = guard_network(monkeypatch)
    said = api_server._process_voice_query("add dinosaur fossils to you")
    assert "not by voice" in said and "Add to Rexy" in said
    got = client.post("/v1/chat/completions",
                      json={"messages": [{"role": "user",
                                          "content": "add dinosaur fossils to you"}]})
    body = got.get_json()
    assert "not by voice" in body["choices"][0]["message"]["content"]
    assert seen == [] and add_more._sessions == {}  # noqa: SLF001


def test_a_chat_question_is_answered_only_by_the_device_it_was_asked_on(
        client, monkeypatch):
    built()
    guard_network(monkeypatch)
    monkeypatch.setattr(api_server, "_query_ollama_direct",
                        lambda *a, **k: {"response": "Okay.", "sources": [],
                                         "suggestions": []})
    asked = client.post("/chat/send", headers=DEV_A,
                        json={"message": "add knitting patterns to you"})
    assert "Say \"yes\"" in asked.get_json()["response"]
    other = client.post("/chat/send", headers=DEV_B, json={"message": "yes"})
    assert "Added" not in other.get_json()["response"]
    from puffpai import routines
    assert routines.get_routine("daily_update") is None


def test_an_unpaired_chat_is_shown_the_finds_but_cannot_say_yes(client,
                                                                monkeypatch):
    built()
    guard_network(monkeypatch)
    got = client.post("/chat/send", headers=AUTH,
                      json={"message": "add knitting patterns to you"})
    text = got.get_json()["response"]
    assert "paired device" in text and "Say \"yes\"" not in text


# -- Drawbridge and the truth ----------------------------------------------------

def test_a_blocked_card_is_never_installed_and_its_draft_is_discarded(
        client, monkeypatch):
    built()
    nhtsa_network(monkeypatch)
    from puffpai import drawbridge

    real = drawbridge.check_addition

    def blocked(draft, **kw):
        verdict = real(draft, **kw)
        return dict(verdict, verdict="block",
                    line="NHTSA recalls: stopped because some of its words "
                         "read like orders to a computer. It was not added.")
    monkeypatch.setattr(drawbridge, "check_addition", blocked)
    found = _find(client, "car recalls")
    sid = found["session"]
    live = item_by_host(found, "api.nhtsa.gov")
    ticket = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                         json={"ids": [live["id"]]}).get_json()["ticket"]
    got = client.post(f"/add/{sid}/approve", headers=DEV_A,
                      json={"ids": [live["id"]], "ticket": ticket}).get_json()
    result = got["results"][0]
    assert result["status"] == "held" and "not added" in result["line"]
    assert feed_generator.list_drafts() == []
    assert feeds.feed_apps() == []
    assert add_more.added() == []


def test_a_card_on_a_bot_set_to_stay_offline_says_so_and_offers_a_switch(
        client, monkeypatch):
    built("island")
    nhtsa_network(monkeypatch)
    # an island bot does not look at a website before adding it; the card is
    # made from the list's own record only when it was built online, so let
    # it look once (the sample look is its own switch, off here)
    monkeypatch.setattr(add_more, "online_allowed", lambda: True)
    found = _find(client, "car recalls")
    sid = found["session"]
    live = item_by_host(found, "api.nhtsa.gov")
    ticket = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                         json={"ids": [live["id"]]}).get_json()["ticket"]
    result = client.post(f"/add/{sid}/approve", headers=DEV_A,
                         json={"ids": [live["id"]], "ticket": ticket}
                         ).get_json()["results"][0]
    assert result["status"] == "added_off"
    assert not result["line"].startswith("Added")
    assert "switched off" in result["line"] and "stay off the internet" in result["line"]
    service = result["switch_on"]["service"]
    assert service == f"card-{result['app_id']}"
    # switching it on is its own yes, on its own ticket
    refused = client.post(f"/add/switch-on/{service}", headers=DEV_A,
                          json={"ticket": ticket})
    assert refused.status_code == 403
    asked = client.post(f"/add/switch-on/{service}/confirm", headers=DEV_A).get_json()
    assert "stay off the internet" in asked["sentence"]
    on = client.post(f"/add/switch-on/{service}", headers=DEV_A,
                     json={"ticket": asked["ticket"]}).get_json()
    assert on["ok"] and "switched on" in on["line"]
    assert feeds.service_state(result["app_id"])["state"] == "open"


def test_a_book_on_a_bot_built_offline_is_held_and_nothing_is_fetched(
        client, monkeypatch):
    built_offline()
    seen = guard_network(monkeypatch)
    found = _find(client, "knitting and crochet")
    sid = found["session"]
    book = item_numbered(found, "knowledge")
    ticket = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                         json={"ids": [book["id"]]}).get_json()["ticket"]
    result = client.post(f"/add/{sid}/approve", headers=DEV_A,
                         json={"ids": [book["id"]], "ticket": ticket}
                         ).get_json()["results"][0]
    assert result["status"] == "held" and "stay off the internet" in result["line"]
    assert seen == [] and add_more.added() == []


def test_a_late_numbered_reply_is_turned_away_without_the_model(client,
                                                                monkeypatch):
    built()
    guard_network(monkeypatch)
    asked_model: list[str] = []

    def model(*a, **k):
        asked_model.append("x")
        return {"response": "Okay.", "sources": [], "suggestions": []}

    monkeypatch.setattr(api_server, "_query_ollama_direct", model)
    send = lambda m: client.post("/chat/send", headers=DEV_A,  # noqa: E731
                                 json={"message": m}).get_json()["response"]
    assert "Say \"yes\"" in send("add knitting patterns to you")
    assert send("no") == "Okay, nothing was added."
    late = send("just 1")
    assert "already answered" in late and "nothing more was added" in late
    assert plain_words.check(late) == []
    assert asked_model == [] and add_more.added() == []
    # once turned away (or after any other message) a number is ordinary chat
    send("just 1")
    assert asked_model == ["x"]
    # a bare yes after a closed list is never caught: other questions use it
    send("add knitting patterns to you")
    send("no")
    send("yes")
    assert asked_model == ["x", "x"]
