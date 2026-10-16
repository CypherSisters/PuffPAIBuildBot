"""A live card is a thing with its own page, row links and a chat lookup.

Fake transport throughout (``fake_network``). A lookup's subject goes through
the privacy screen and leaves only through the app's own outside service, so
a service that is switched off, or a private subject, sends nothing.
"""
from __future__ import annotations

import json

import pytest

from livedata_support import (FakeResponse, fake_network, feed_manifest, install,
                              no_network, set_posture)
from puffpai import feeds, things
from puffpai.security import egress_doors

LIST_CARD = {"kind": "list", "title": "Albums", "items": "rows",
             "item_title": "title", "item_detail": "by",
             "item_link": "https://www.example.org/release/{id}", "max_items": 5}
ROWS = {"rows": [{"id": "a1", "title": "Blue Train", "by": "A Player"},
                 {"id": "b2", "title": "Kind of Blue", "by": "Another Player"}]}
SEARCH = "https://api.example.org/v1/search?q={topic}"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def _ready(allow=True, **extra):
    set_posture("enumerated")
    install(feed_manifest(card=LIST_CARD, image_host=None, **extra))
    if allow:
        feeds.set_allowed("skyshots", True)


def test_a_card_link_and_a_lookup_address_are_checked():
    good = feed_manifest(card=LIST_CARD, image_host=None, lookup_url=SEARCH)["feed"]
    assert feeds.validate_feed("x", good) is None
    other_host = dict(good, lookup_url="https://evil.example.com/s?q={topic}")
    assert feeds.validate_feed("x", other_host) is not None
    two = dict(good, lookup_url="https://api.example.org/s?q={topic}&d={date}")
    assert feeds.validate_feed("x", two) is not None
    bad_link = dict(good, card=dict(LIST_CARD, item_link="https://evil.example.com/{id}"))
    assert feeds.validate_feed("x", bad_link) is not None
    # A link to the API record is not a web page.
    api_link = dict(good, card=dict(LIST_CARD, item_link="https://api.example.org/release/{id}"))
    assert feeds.validate_feed("x", api_link) is not None


def test_rows_carry_their_links():
    feed = feed_manifest(card=LIST_CARD, image_host=None)["feed"]
    card = feeds.extract(ROWS, feed)
    assert card["items"][0]["link"] == "https://www.example.org/release/a1"


def test_a_lookup_goes_out_through_the_apps_own_service(monkeypatch):
    _ready(lookup_url=SEARCH)
    monkeypatch.setattr(feeds, "_first_fetch_starter", lambda _id: False)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/v1/search", FakeResponse(200, json.dumps(ROWS).encode()))
    got = feeds.lookup("skyshots", "jazz", said="find me jazz albums")
    assert got["ok"] is True
    assert got["items"][0] == {"title": "Blue Train", "detail": "A Player",
                               "link": "https://www.example.org/release/a1"}
    assert [c["url"] for c in net.calls] == ["https://api.example.org/v1/search?q=jazz"]


def test_a_private_subject_never_leaves(monkeypatch):
    _ready(lookup_url=SEARCH)
    monkeypatch.setattr(feeds, "_first_fetch_starter", lambda _id: False)
    no_network(monkeypatch)
    got = feeds.lookup("skyshots", "Zanzibar Quill",
                       said="look up my friend named Zanzibar Quill")
    assert got["ok"] is False and "stays on this computer" in got["error"]


def test_a_service_that_is_off_sends_nothing(monkeypatch):
    _ready(allow=False, lookup_url=SEARCH)
    no_network(monkeypatch)
    got = feeds.lookup("skyshots", "jazz", said="jazz")
    assert got["ok"] is False


def test_an_older_card_finds_a_search_address_on_the_list(monkeypatch):
    feed = feed_manifest(card=LIST_CARD, image_host=None)["feed"]
    monkeypatch.setattr(feeds, "_seed_lookup", lambda host: SEARCH if host == "api.example.org" else None)
    assert feeds.lookup_template(feed) == SEARCH
    from puffpai.catalogs import api_seeds
    rows = {r["id"]: r for r in api_seeds.load_rows()}
    assert "{topic}" in rows["musicbrainz"]["card_url"]   # a search, not a date
    assert rows["musicbrainz"]["card"]["item_link"]


def test_the_lookup_tool_is_offered_to_the_owner_only():
    _ready(lookup_url=SEARCH)
    names = [t.name for t in feeds.lookup_tools({"caller": "owner"})]
    assert names == ["skyshots_lookup"]
    assert feeds.lookup_tools({"caller": "keyless"}) == []


def test_a_live_card_is_a_thing_with_its_newest_items(monkeypatch):
    _ready(lookup_url=SEARCH)
    monkeypatch.setattr(feeds, "_first_fetch_starter", lambda _id: False)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/v1/latest", FakeResponse(200, json.dumps(ROWS).encode()))
    feeds.refresh("skyshots", force=True)
    listed = {t["id"]: t for t in things.listing()}
    assert listed["feed-skyshots"]["kind"] == "feed"
    page = things.page("feed-skyshots")
    assert page["feed"]["items"][1]["link"] == "https://www.example.org/release/b2"
    assert page["feed"]["tool"] == "skyshots_lookup"
    assert page["recipe"]["try"]
    assert "skyshots_lookup" in things.ask_note(things.get("feed-skyshots"))
    assert things.remove("feed-skyshots")["ok"] is True     # a live card can be taken out
