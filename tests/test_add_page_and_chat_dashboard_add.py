"""The Add page and chat "add X to my dashboard".

"Search online" must not hold the page for a minute and then do nothing, a
find outside the libraries can go on the home page, and a chat request to add
something to the dashboard is carried through its follow-up questions.

* The libraries are asked at once, each request waits a short time, and the
  search waits for all of them at most ``LIBRARY_WAIT_S``; a library that
  does not answer gets one plain line and its saved list.
* OpenStax's book list (about 370 KB) is asked for at most once a day.
* A line with something to show offers "Put it on my home page" (ticked for
  live cards); /front lists what is placed, in order.
* "add the ISS tracker to my dashboard" is an add request aimed at the home
  page, and a question it asks waits on the server, not in the model.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time

import pytest

from add_more_support import AUTH_KEY, DEVICE_A, built, nhtsa_network
from puffpai import (add_intent, add_more, add_more_api, api_server,
                     home_placement, plain_words, source_fetch)
from puffpai.catalogs import gutendex, kiwix_opds, knowledge_resolver, openstax
from puffpai.config import get_config
from puffpai.security import consent_tickets, egress_doors, outside_switches

AUTH = {"Authorization": f"Bearer {AUTH_KEY}"}
DEV_A = {**AUTH, "X-Puff-Device-Id": DEVICE_A}


# ---------------------------------------------------------------------------
# a fake internet at the transport, where one library hangs
# ---------------------------------------------------------------------------

class Resp:
    def __init__(self, status=200, body=b""):
        self.status, self.headers, self._b, self._p = status, {}, body, 0

    def read(self, amt=-1):
        amt = len(self._b) - self._p if amt is None or amt < 0 else amt
        chunk = self._b[self._p:self._p + amt]
        self._p += len(chunk)
        return chunk

    def close(self):
        pass


FEED = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry>
  <title>Astronomy Stack Exchange</title><updated>2026-09-11T00:00:00Z</updated>
  <name>astronomy.stackexchange.com_en_all</name><flavour>nopic</flavour>
  <category>stack_exchange</category><summary>Questions about black holes and stars</summary>
  <link rel="http://opds-spec.org/acquisition/open-access"
        href="https://download.kiwix.org/zim/stack_exchange/astronomy.zim.meta4"
        length="1000000"/></entry></feed>"""

OPENSTAX_LIST = json.dumps({"items": [{
    "title": "Astronomy 2e", "book_state": "live",
    "license_name": "Creative Commons Attribution License", "license_version": "4.0",
    "meta": {"slug": "astronomy-2e", "locale": "en"},
    "book_subjects": [{"subject_name": "Science"}],
    "description": "Black holes, stars and galaxies."}]}).encode()


class Net:
    """Answers Kiwix and OpenStax; Gutendex hangs (``hang``) or waits out
    the request's own timeout (``slow``). With *openstax* set the same way,
    OpenStax does that too (Project Gutenberg is read from its saved
    catalog, so OpenStax is the library asked online that can hang)."""

    def __init__(self, gutendex="slow", hang_s=6.0, openstax=None):
        self.calls, self.timeouts = [], {}
        self.gutendex, self.hang_s = gutendex, hang_s
        self.openstax = openstax
        self.lock = threading.Lock()

    def __call__(self, url, headers, timeout):
        with self.lock:
            self.calls.append(url)
            self.timeouts[url.split("/")[2]] = timeout
        if url.startswith(kiwix_opds.SEARCH_URL):
            return Resp(200, FEED)
        if url.startswith("https://gutendex.com/"):
            if self.gutendex == "hang":
                time.sleep(self.hang_s)  # ignores its timeout
            else:
                time.sleep(min(timeout, self.hang_s))
            raise socket.timeout("timed out")
        if url.startswith(openstax.LIST_URL.split("?")[0]):
            if self.openstax == "hang":
                time.sleep(self.hang_s)  # ignores its timeout
                raise socket.timeout("timed out")
            if self.openstax == "slow":
                time.sleep(min(timeout, self.hang_s))
                raise socket.timeout("timed out")
            return Resp(200, OPENSTAX_LIST)
        return Resp(404)


@pytest.fixture
def net_state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    get_config(_force_reload=True)
    source_fetch.clear_caches()
    kiwix_opds.clear_search_cache()
    gutendex._CACHE.clear()  # noqa: SLF001
    openstax._LIVE.clear()  # noqa: SLF001
    outside_switches.forget()
    monkeypatch.setattr(source_fetch, "_default_resolver", lambda host: ["93.184.216.34"])
    yield tmp_path
    source_fetch.clear_caches()
    kiwix_opds.clear_search_cache()
    openstax._LIVE.clear()  # noqa: SLF001
    get_config(_force_reload=True)


def test_a_request_waits_only_as_long_as_its_thread_allows():
    assert source_fetch.request_timeout() == source_fetch.TIMEOUT_S
    with source_fetch.quick_timeout(4):
        assert source_fetch.request_timeout() == 4
        seen = []
        t = threading.Thread(target=lambda: seen.append(source_fetch.request_timeout()))
        t.start(), t.join()
        assert seen == [source_fetch.TIMEOUT_S]  # other threads keep theirs
    assert source_fetch.request_timeout() == source_fetch.TIMEOUT_S


def test_libraries_are_asked_at_once_and_a_hung_one_is_given_up_on():
    heard = []

    def slow():
        time.sleep(5)
        return "late"

    start = time.monotonic()
    out = knowledge_resolver.ask_libraries(
        {"kiwix": lambda: "k", "gutenberg": slow, "openstax": lambda: "o"},
        wait=1.0, on_library=lambda n, s: heard.append((n, s)))
    took = time.monotonic() - start
    assert took < 2.0
    assert out["kiwix"] == ("done", "k") and out["openstax"] == ("done", "o")
    assert out["gutenberg"] == ("no_answer", None)
    assert ("gutenberg", "asking") in heard and ("gutenberg", "no_answer") in heard
    assert ("kiwix", "answered") in heard


@pytest.mark.parametrize("mode", ["slow", "hang"])
def test_a_hung_library_never_holds_the_search(net_state, monkeypatch, mode):
    # Project Gutenberg is read from its saved catalog, so
    # the library asked online that hangs here is OpenStax.
    net = Net(openstax=mode, hang_s=6.0)
    monkeypatch.setattr(source_fetch, "_default_transport", net)
    monkeypatch.setattr(knowledge_resolver, "LIBRARY_REQUEST_S", 1.0)
    monkeypatch.setattr(knowledge_resolver, "LIBRARY_WAIT_S", 2.5)
    heard = []
    start = time.monotonic()
    result = knowledge_resolver.find("black holes", live=True,
                                     on_library=lambda n, st: heard.append((n, st)))
    took = time.monotonic() - start
    # progress and the note agree: it did not answer (real run)
    assert ("openstax", "no_answer") in heard
    assert ("openstax", "answered") not in heard
    assert ("kiwix", "answered") in heard
    assert took < 4.0, took
    assert result["searched"] == "live"  # Kiwix answered
    assert "OpenStax didn't answer this time" in result["note"]
    assert "timed out" not in result["note"]
    assert plain_words.check(result["note"]) == []
    # the library's request was given the short wait, not 30 s
    host = openstax.LIST_URL.split("/")[2]
    assert net.timeouts[host] == 1.0


def test_the_openstax_book_list_is_asked_for_once_a_day(net_state, monkeypatch):
    net = Net()
    monkeypatch.setattr(source_fetch, "_default_transport", net)
    first = openstax.list_live()
    assert [b["slug"] for b in first] == ["astronomy-2e"]
    lists = [u for u in net.calls if "/apps/cms/api/v2/pages/" in u]
    assert len(lists) == 1
    kept = os.path.join(str(net_state / "state"), "catalog_cache", openstax.LIVE_LIST_FILE)
    assert os.path.isfile(kept)
    # a restart forgets the memory copy; the kept list is used, nothing sent
    openstax._LIVE.clear()  # noqa: SLF001
    assert [b["slug"] for b in openstax.list_live()] == ["astronomy-2e"]
    assert len([u for u in net.calls if "/apps/cms/api/v2/pages/" in u]) == 1
    # a day later it is asked again
    data = json.load(open(kept, encoding="utf-8"))
    data["fetched_at"] -= openstax.LIVE_LIST_SECONDS + 5
    json.dump(data, open(kept, "w", encoding="utf-8"))
    openstax._LIVE.clear()  # noqa: SLF001
    openstax.list_live()
    assert len([u for u in net.calls if "/apps/cms/api/v2/pages/" in u]) == 2


def test_a_busy_book_download_never_delays_a_search_of_another_library():
    """The learner fetches its pages one a second from one site; the pace is
    kept per site, so a search of another library does not wait behind it."""
    source_fetch.clear_caches()
    now = [100.0]
    slept = []
    for _ in range(20):  # the learner's reservations on one site
        source_fetch._rate_limit("en.wikipedia.org", slept.append, lambda: now[0])  # noqa: SLF001
    slept.clear()
    for host in ("opds.library.kiwix.org", "gutendex.com", "openstax.org"):
        source_fetch._rate_limit(host, slept.append, lambda: now[0])  # noqa: SLF001
    assert slept == []
    source_fetch.clear_caches()


# ---------------------------------------------------------------------------
# the home page placement store
# ---------------------------------------------------------------------------

def test_placement_keeps_order_and_survives_a_reread():
    assert home_placement.placed() == []
    assert home_placement.place("card", "nasa-epic", "NASA EPIC")
    assert home_placement.place("update", "daily_update", "A daily update")
    assert home_placement.place("card", "nasa-epic", "again")  # once only
    assert [(r["kind"], r["ref"]) for r in home_placement.placed()] == [
        ("card", "nasa-epic"), ("update", "daily_update")]
    assert home_placement.unplace("card", "nasa-epic")
    assert not home_placement.unplace("card", "nasa-epic")
    assert not home_placement.place("card", "../etc", "bad")
    assert not home_placement.place("Card!", "x", "bad")
    assert home_placement.for_undo({"kind": "book", "ref": "wikipedia:x"}) is None
    assert home_placement.for_undo({"kind": "card", "ref": "a1"}) == ("card", "a1")


def test_front_lists_only_placed_things_that_still_exist():
    home_placement.place("card", "gone-card", "Gone")
    home_placement.place("card", "here", "Here")
    rows = home_placement.front_rows([{"app_id": "here", "name": "Here card",
                                       "status": "on", "line": "3 things",
                                       "href": "/dashboard/apps/here"}])
    assert [r["id"] for r in rows] == ["here"]
    assert rows[0]["kind"] == "card" and rows[0]["name"] == "Here card"


# ---------------------------------------------------------------------------
# the Add page over HTTP
# ---------------------------------------------------------------------------

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
    asked = []

    def model(*a, **k):
        asked.append(a[0] if a else "")
        return {"response": "Okay.", "sources": [], "suggestions": []}

    monkeypatch.setattr(api_server, "_query_ollama_direct", model)
    c = api_server.app.test_client()
    c.asked_model = asked
    yield c
    add_more.reset()
    consent_tickets.reset()
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def _live_line(found):
    for group in found["groups"]:
        for item in group["items"]:
            if group["section"] == "live_info" and item["can_add"]:
                return item
    raise AssertionError(found)


def _add(client, found, ids, home=None):
    sid = found["session"]
    asked = client.post(f"/add/{sid}/confirm", headers=DEV_A,
                        json={"ids": ids}).get_json()
    body = {"ids": ids, "ticket": asked["ticket"]}
    if home is not None:
        body["home"] = home
    return client.post(f"/add/{sid}/approve", headers=DEV_A, json=body).get_json()


def test_a_live_card_offers_the_home_page_ticked_and_lands_in_front(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    found = client.post("/add/find", headers=DEV_A,
                        json={"words": "car recalls"}).get_json()
    live = _live_line(found)
    assert live["home"] == {"offer": True, "default": True}
    books = [i for g in found["groups"] if g["section"] == "knowledge" for i in g["items"]]
    assert all(not i["home"]["offer"] for i in books)  # a book has nothing to show
    got = _add(client, found, [live["id"]], home=[live["id"]])
    result = got["results"][0]
    assert result["placed"] is True and "home page" in result["line"]
    front = client.get("/front", headers=AUTH).get_json()
    assert [p["id"] for p in front["placed"]] == [result["app_id"]]
    assert front["placed"][0]["kind"] == "card"
    assert plain_words.check(front["placed"][0]["name"]) == []
    # the Added list says it is there, and it can be taken off and put back
    rows = client.get("/add/added", headers=DEV_A).get_json()["rows"]
    assert rows[0]["home"] == {"can": True, "on": True}
    off = client.post(f"/add/added/{rows[0]['entry']}/home", headers=DEV_A,
                      json={"on": False}).get_json()
    assert off["ok"] and "off your home page" in off["line"]
    assert client.get("/front", headers=AUTH).get_json()["placed"] == []
    assert client.post(f"/add/added/{rows[0]['entry']}/home", headers=AUTH,
                       json={"on": True}).status_code == 403  # a paired device only
    client.post(f"/add/added/{rows[0]['entry']}/home", headers=DEV_A, json={"on": True})
    assert len(client.get("/front", headers=AUTH).get_json()["placed"]) == 1
    # taking it out takes it off the home page too
    client.post(f"/add/added/{rows[0]['entry']}/remove", headers=DEV_A)
    assert client.get("/front", headers=AUTH).get_json()["placed"] == []


def test_unticking_keeps_it_off_the_home_page_and_says_where_it_is(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    found = client.post("/add/find", headers=DEV_A,
                        json={"words": "car recalls"}).get_json()
    live = _live_line(found)
    result = _add(client, found, [live["id"]], home=[])["results"][0]
    assert result["placed"] is False
    assert "on your home page" not in result["line"]
    assert "own page under Apps" in result["line"]
    front = client.get("/front", headers=AUTH).get_json()
    assert front["placed"] == []
    # the home grid leaves it out (the cards code reads off_home) ...
    assert front["off_home"] == [result["app_id"]]
    assert result["app_id"] in [c["app_id"] for c in front["cards"]]
    # ... until the owner puts it there after all
    rows = client.get("/add/added", headers=DEV_A).get_json()["rows"]
    client.post(f"/add/added/{rows[0]['entry']}/home", headers=DEV_A, json={"on": True})
    front = client.get("/front", headers=AUTH).get_json()
    assert front["off_home"] == [] and [p["id"] for p in front["placed"]] == [result["app_id"]]


def test_the_page_can_ask_how_the_online_search_is_going(client, monkeypatch):
    from puffpai.knowledge import find_more
    built(name="Nova")
    release = threading.Event()

    def slow_search(words, *, live=None, on_library=None, **kw):
        for name in ("kiwix", "gutenberg", "openstax"):
            on_library(name, "asking")
        on_library("kiwix", "answered")
        release.wait(5)
        on_library("gutenberg", "no_answer")
        on_library("openstax", "answered")
        return {"searched": "live", "candidates": [],
                "note": "Project Gutenberg didn't answer this time, so its saved "
                        "list was used. Try again later."}

    monkeypatch.setattr(find_more, "search", slow_search)
    out = {}
    app = api_server.app

    def run():
        with app.test_client() as c2:
            out["r"] = c2.post("/add/find", headers=DEV_A,
                               json={"words": "black holes", "online": True}).get_json()

    t = threading.Thread(target=run)
    t.start()
    for _ in range(50):
        now = client.get("/add/looking", headers=DEV_A).get_json()
        if now.get("active") and len(now["libraries"]) == 3:
            break
        time.sleep(0.05)
    assert now["active"] and now["line"] == "Looking in 3 libraries online..."
    assert {r["line"] for r in now["libraries"]} >= {"The Kiwix library: answered",
                                                     "Project Gutenberg: still looking"}
    release.set()
    t.join(10)
    done = client.get("/add/looking", headers=DEV_A).get_json()
    assert not done["active"] and done["line"] == "Looked in 3 libraries online."
    assert "Project Gutenberg: didn't answer this time" in {r["line"] for r in done["libraries"]}
    assert any("didn't answer this time" in n for n in out["r"]["notes"])
    for text in [now["line"], done["line"]] + [r["line"] for r in done["libraries"]]:
        assert plain_words.check(text) == []
    assert client.get("/add/looking", headers=AUTH).status_code in (401, 403)


# ---------------------------------------------------------------------------
# chat: "add X to my dashboard", held on the server
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("message,words", [
    ("add the ISS tracker to my dashboard", "ISS tracker"),
    ("put a weather card on my home page", "weather"),
    ("show me NASA pictures on my dashboard", "NASA pictures"),
    ("can you add car recalls to the dashboard please", "car recalls"),
    ("could you display bird songs on my home screen", "bird songs"),
])
def test_home_page_requests_are_add_requests(message, words):
    got = add_intent.detect(message, bot_name="Nova")
    assert got == {"words": words, "shape": "add", "place": "home"}


@pytest.mark.parametrize("message", [
    "show me what is on my dashboard", "what is on my dashboard",
    "add milk to the shopping list", "how do I add things to my dashboard?",
])
def test_questions_and_list_sentences_are_not(message):
    assert add_intent.detect(message, bot_name="Nova") is None


def _say(client, message):
    return client.post("/chat/send", headers=DEV_A,
                       json={"message": message}).get_json()["response"]


def test_three_turn_chat_add_from_the_dashboard_words(client, monkeypatch):
    """1: the request (nothing on this computer) asks "Look online too?";
    2: "yes" looks online; 3: "the first one" adds it. The model is never
    asked, so it cannot forget what was being discussed."""
    from puffpai.knowledge import find_more
    calls = []

    def empty_online(words, *, live=None, **kw):
        calls.append(words)
        return {"searched": "live", "candidates": [], "note": ""}

    monkeypatch.setattr(find_more, "search", empty_online)
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda f: None)
    built(name="Nova")
    first = _say(client, "can you add zxqv blorf to my dashboard")
    assert "Look online too? It sends only these words: zxqv, blorf" in first
    second = _say(client, "yes")
    assert calls == ["zxqv blorf"]
    assert "1. Wikipedia articles about zxqv blorf" in second
    third = _say(client, "the first one")
    assert "being fetched and learned in the background" in third
    assert "wikipedia:en:zxqv-blorf" in find_more.pending_orders()
    assert client.asked_model == []
    for text in (first, second, third):
        assert plain_words.check(text) == []


def test_a_dashboard_card_request_survives_a_side_question(client, monkeypatch):
    """The owner asks the bot something else in between. The list waits on
    the server; "the first one" later is checked back, then "yes" adds it
    and puts it on the home page."""
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    first = _say(client, "put a car recalls card on my home page")
    assert "Live: NHTSA recalls" in first
    assert "What you pick that can be shown goes on your home page." in first
    assert "Wikipedia" not in first  # a home page request looks for things to show
    side = _say(client, "which one is the most useful?")
    assert side == "Okay." and len(client.asked_model) == 1
    back = _say(client, "the first one")
    assert back.startswith("Back to what you asked me to add (\"car recalls\"):")
    assert "api.nhtsa.gov" in back and back.endswith("Say \"yes\" to add it, or \"no\".")
    done = _say(client, "yes")
    assert "Added NHTSA recalls: a live card on your home page." in done
    front = client.get("/front", headers=AUTH).get_json()
    assert len(front["placed"]) == 1 and front["placed"][0]["kind"] == "card"
    for text in (first, back, done):
        assert plain_words.check(text) == []


def test_a_bare_yes_after_a_side_question_is_said_back_then_added(client, monkeypatch):
    """A run: "show me rocket launches on my dashboard",
    a side question, then "yes" (the word the list told her to say) went to
    the model and the add was lost. A bare yes is now said back first (it
    may have answered something else), and the next yes adds."""
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    _say(client, "who was the first person on the moon?")
    back = _say(client, "yes")
    assert back.startswith("Back to what you asked me to add (\"car recalls\"):")
    assert back.endswith("or \"no\".") and len(client.asked_model) == 1
    assert client.get("/front", headers=AUTH).get_json()["placed"] == []
    assert "Added NHTSA recalls: a live card on your home page." in _say(client, "yes")
    # "yes" was to every line shown, so the daily update came along too
    placed = client.get("/front", headers=AUTH).get_json()["placed"]
    assert [p["kind"] for p in placed] == ["card", "update"]
    assert plain_words.check(back) == []


def test_a_bare_no_after_a_side_question_lets_the_request_go(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    _say(client, "tell me a joke")
    no = _say(client, "no")
    assert no.startswith("Okay, I let go of what you asked me to add (\"car recalls\").")
    assert "Nothing was added" in no and plain_words.check(no) == []
    assert _say(client, "yes") == "Okay."  # nothing is waiting any more
    assert client.get("/front", headers=AUTH).get_json()["placed"] == []


def test_a_held_look_online_question_takes_a_bare_yes_back(client, monkeypatch):
    from puffpai.knowledge import find_more
    monkeypatch.setattr(find_more, "search", lambda words, **kw: {
        "searched": "live", "candidates": [], "note": ""})
    monkeypatch.setattr(find_more, "_learn_rest_in_background", lambda f: None)
    built(name="Nova")
    _say(client, "can you add zxqv blorf to my dashboard")
    _say(client, "tell me a joke")
    back = _say(client, "yes")
    assert back.startswith("Back to what you asked me to add (\"zxqv blorf\"):")
    assert "Look online too?" in back
    assert "1. Wikipedia articles about zxqv blorf" in _say(client, "yes")


def test_a_request_let_go_after_too_many_side_messages_says_so(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    for n in range(add_more.MAX_INTERRUPTS + 1):
        _say(client, f"tell me joke number {n}")
    late = _say(client, "the first one")
    assert late.startswith("I let go of what you asked me to add (\"car recalls\")")
    assert plain_words.check(late) == []


def test_the_offer_owns_a_bare_yes_asked_after_the_side_message(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    _say(client, "which one is the most useful?")
    from datetime import datetime, timezone
    asked = datetime.now(timezone.utc)  # the offer asks in the side reply
    monkeypatch.setattr(add_more, "_offer_asked_after", lambda when: asked > when)
    assert _say(client, "yes") == "Okay."  # the offer's yes, not ours
    assert _say(client, "the first one").startswith("Back to what you asked me to add")


def test_a_held_question_outlives_the_daily_update_offer(client, monkeypatch):
    """Real run: the side question's answer carried the daily
    update offer's own "say yes or no", and that dropped the list, so "the
    first one" went to the model. The offer owns a bare yes; the list still
    takes a numbered pick."""
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    _say(client, "which one is the most useful?")
    from datetime import datetime, timezone
    asked = datetime.now(timezone.utc)  # the offer asks, after our list
    monkeypatch.setattr(add_more, "_offer_asked_after", lambda when: asked > when)
    back = _say(client, "the first one")
    assert back.startswith("Back to what you asked me to add")
    assert "Added NHTSA recalls" in _say(client, "yes")


def test_an_unheld_question_still_gives_way_to_the_offer(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    monkeypatch.setattr(add_more, "_offer_asked_after", lambda when: True)
    assert _say(client, "yes") == "Okay."  # the offer's yes, not ours


def test_a_new_add_request_replaces_a_held_question(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    _say(client, "put a car recalls card on my home page")
    _say(client, "tell me a joke")
    again = _say(client, "add car recalls to you")
    assert "Back to what you asked" not in again
    assert "Live: NHTSA recalls" in again


#: The real model call, kept before the client fixture swaps it for a fake.
_REAL_QUERY = api_server._query_ollama_direct


def test_a_side_question_tells_the_model_the_add_is_still_waiting(client, monkeypatch):
    """Merge wiring (t1 chat + add-more): while a list waits, the model that
    answers the side question is told so under the bot's own name, through
    chat_memory's never-trimmed ``pending`` part."""
    sent = []

    class Reply:
        status_code = 200

        @staticmethod
        def json():
            return {"message": {"content": "Okay."}}

    def ollama_chat(messages, **kw):
        sent.append(messages)
        return Reply()

    monkeypatch.setattr(api_server, "_query_ollama_direct", _REAL_QUERY)
    monkeypatch.setattr(api_server, "_ollama_chat", ollama_chat)
    monkeypatch.setattr(api_server, "_grounding_context", lambda q: ("", []))
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    assert api_server._add_more_pending_note() == ""  # no request, nothing waits
    _say(client, "put a car recalls card on my home page")
    assert sent == []  # the add request itself never reaches the model
    _say(client, "which one is the most useful?")
    assert len(sent) == 1
    system = [m["content"] for m in sent[0] if m["role"] == "system"]
    note = next(c for c in system if "to add \"car recalls\"" in c)
    assert "The owner asked Wrench to add \"car recalls\"." in note
    assert "Nothing has been added yet." in note and "PuffPAI" not in note
    assert plain_words.check(note) == []
    # the add finished: nothing is waiting any more
    _say(client, "the first one")
    _say(client, "yes")
    assert add_more.pending_note(f"device:{DEVICE_A}") == ""


def _stream_say(client, message, headers=DEV_A, stream=True):
    """What the dashboard's chat page does: POST /v1/chat/completions,
    model puffpai, stream true, and read the text out of the chunks."""
    got = client.post("/v1/chat/completions", headers=headers, json={
        "model": "puffpai", "stream": stream, "conversation_id": "conv-smoke",
        "messages": [{"role": "user", "content": message}]})
    body = got.get_data(as_text=True)
    if not stream:
        return got.get_json()["choices"][0]["message"]["content"]
    out = []
    for line in body.splitlines():
        if line.startswith("data: ") and line != "data: [DONE]":
            chunk = json.loads(line[6:])
            out.append(((chunk.get("choices") or [{}])[0].get("delta") or {})
                       .get("content") or "")
    return "".join(out) if out else body


def test_the_dashboard_chat_page_streaming_route_adds_to_the_home_page(client, monkeypatch):
    """The dashboard's chat page streams through
    /v1/chat/completions, which add-more treated as voice ("adding things is
    chosen on the dashboard, not by voice"), and "the first one" went to the
    model. From a paired device it is the dashboard's chat."""
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    first = _stream_say(client, "put a car recalls card on my home page")
    assert "not by voice" not in first
    assert "Live: NHTSA recalls" in first
    # A side question on this route would tell the model the list is waiting.
    with api_server.app.test_request_context("/v1/chat/completions", method="POST",
                                             headers=DEV_A,
                                             environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        api_server.app.preprocess_request()
        assert "to add \"car recalls\"" in api_server._add_more_pending_note()
    with api_server.app.test_request_context("/v1/chat/completions", method="POST",
                                             headers=AUTH,
                                             environ_base={"REMOTE_ADDR": "127.0.0.1"}):
        api_server.app.preprocess_request()
        assert api_server._add_more_pending_note() == ""
    back = _stream_say(client, "the first one")
    assert "api.nhtsa.gov" in back or "Added NHTSA recalls" in back
    if "Added" not in back:
        back = _stream_say(client, "yes")
    assert "Added NHTSA recalls: a live card on your home page." in back
    assert client.asked_model == []
    front = client.get("/front", headers=AUTH).get_json()
    assert [p["kind"] for p in front["placed"]] == ["card"]


def test_other_clients_of_the_streaming_route_keep_the_voice_rule(client, monkeypatch):
    """Home Assistant's conversation agent (no paired device) is still voice:
    it points to the Add page and never searches or says yes."""
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    said = _stream_say(client, "put a car recalls card on my home page",
                       headers=AUTH, stream=False)
    assert "not by voice" in said
    assert client.get("/front", headers=AUTH).get_json()["placed"] == []


def test_a_side_question_counts_once_on_the_streaming_route(client, monkeypatch):
    built(name="Wrench")
    nhtsa_network(monkeypatch)
    first = _stream_say(client, "put a car recalls card on my home page")
    assert "Live: NHTSA recalls" in first
    assert add_more._pending_for(f"device:{DEVICE_A}") is not None  # noqa: SLF001
    _stream_say(client, "which one is the most useful?", stream=False)
    question = add_more._pending_for(f"device:{DEVICE_A}")  # noqa: SLF001
    assert question is not None and question.get("interrupted") == 1
