"""The server-side runner, its cache, its picture checks, and what it answers.

Fake transport throughout (``fake_network``): the tests pin what reaches the
wire, what is stored, what is refused, and the plain words a person reads.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from livedata_support import (JPEG_HEAD, PNG_1PX, FakeResponse, fake_network,
                              feed_manifest, install, jargon_in, no_network,
                              set_posture)
from puffpai import app_fastpath, dashboard_widgets, feeds
from puffpai.security import egress_doors

IMAGE_CARD = {"kind": "image", "title": "Picture of the day",
              "image": "item.pic", "headline": "item.name",
              "caption": "item.note", "image_credit": "item.by"}
VALUE_DOC = {"reading": {"level": 4.333, "when": "2026-09-23T18:00"}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    egress_doors.reset_cache()


def _ready(manifest=None, *, allow=True):
    set_posture("enumerated")
    install(manifest or feed_manifest())
    if allow:
        feeds.set_allowed((manifest or {}).get("id", "skyshots"), True)


def test_one_fetch_stores_data_and_reports_it(monkeypatch):
    _ready()
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/v1/latest",
           FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    state = feeds.refresh("skyshots")
    assert state["status"] == "ok"
    assert state["card"]["value"] == "4.33" and state["card"]["label"] == "Level"
    assert state["credit"] == "Data: Example Data"
    assert len(net.calls) == 1
    call = net.calls[0]
    assert call["method"] == "GET" and call["allow_redirects"] is False
    assert call["headers"]["User-Agent"].startswith("PuffPAI")
    cache = feeds.read_cache("skyshots")
    from puffpai.config import get_state_dir
    assert feeds.cache_path("skyshots").startswith(get_state_dir())
    assert cache["card"]["value"] == "4.33"
    # A second read inside the refresh window does not touch the network.
    feeds.refresh("skyshots")
    assert len(net.calls) == 1
    # Forcing inside the ten-minute floor does not either.
    feeds.refresh("skyshots", force=True)
    assert len(net.calls) == 1
    later = time.time() + 31 * 60
    feeds.refresh("skyshots", now=later)
    assert len(net.calls) == 2


def test_the_log_keeps_host_and_path_but_never_the_query(monkeypatch):
    _ready(feed_manifest(url="https://api.example.org/v1/latest?lat={lat_round}"
                            "&lon={lon_round}"))
    set_posture("enumerated", home={"latitude": 40.123, "longitude": -83.456})
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/v1/latest",
           FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    feeds.refresh("skyshots")
    assert net.calls[0]["url"].endswith("lat=40.1&lon=-83.5")
    log = open(os.path.join(feeds.feeds_root(), "fetch_log.jsonl"),
               encoding="utf-8").read()
    assert "40.1" not in log and "lat=" not in log and "/v1/latest" in log


def test_a_location_feed_without_a_town_says_so(monkeypatch):
    _ready(feed_manifest(url="https://api.example.org/v1?lat={lat_round}&lon={lon_round}"))
    no_network(monkeypatch)
    state = feeds.refresh("skyshots")
    assert state["card"] is None and "town" in state["message"]


@pytest.mark.parametrize("response, words", [
    (FakeResponse(500, b"{}"), "error"),
    (FakeResponse(200, b"<html></html>", content_type="text/html"), "expected form"),
    (FakeResponse(200, b"{" + b" " * (feeds.MAX_RESPONSE_BYTES + 10) + b"}"), "512 KB"),
    (FakeResponse(200, b"not json"), "could not be read"),
    (FakeResponse(200, b'{"other": 1}'), "were not in the reply"),
], ids=["500", "html", "too-big", "not-json", "missing-picks"])
def test_bad_replies_are_refused_and_nothing_is_made_up(response, words, monkeypatch):
    _ready()
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", response)
    state = feeds.refresh("skyshots")
    assert state["card"] is None
    assert words in state["message"]


def test_a_failure_keeps_the_last_good_copy_and_waits_before_retrying(monkeypatch):
    _ready()
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    feeds.refresh("skyshots")
    net.on("https://api.example.org/", FakeResponse(503, b"{}"))
    later = time.time() + 31 * 60
    state = feeds.refresh("skyshots", now=later)
    assert state["card"]["value"] == "4.33"
    assert "error" in state["last_error"]
    calls = len(net.calls)
    feeds.refresh("skyshots", now=later + 60)
    assert len(net.calls) == calls


def test_values_older_than_max_age_are_withheld(monkeypatch):
    _ready()
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    feeds.refresh("skyshots")
    no_network(monkeypatch)
    feeds.set_allowed("skyshots", False)   # so nothing refetches
    state = feeds.card_state("skyshots", now=time.time() + 2 * 24 * 3600)
    assert state["card"] is None and "too old" in state["message"]


def test_pictures_are_fetched_by_the_backend_and_checked(monkeypatch):
    manifest = feed_manifest(card=IMAGE_CARD)
    _ready(manifest)
    net = fake_network(monkeypatch)
    doc = {"item": {"pic": "http://img.example.org/a.png", "name": "Ring",
                    "note": "A ring.", "by": "Someone"}}
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(doc).encode()))
    net.on("https://img.example.org/a.png", FakeResponse(200, PNG_1PX, "image/png"))
    state = feeds.refresh("skyshots")
    card = state["card"]
    assert card["image"].endswith(".png") and "image_url" not in card
    assert feeds.image_file("skyshots", card["image"])
    assert net.calls[1]["url"] == "https://img.example.org/a.png"   # upgraded
    assert feeds.image_file("skyshots", "../latest.json") is None
    assert feeds.image_file("skyshots", "x.svg") is None


@pytest.mark.parametrize("body, ctype", [
    (b"<svg onload=alert(1)></svg>", "image/svg+xml"),
    (JPEG_HEAD, "image/png"),              # bytes lie about their type
    (b"GIF89a" + b"\x00" * 10, "text/html"),
    (b"\xff\xd8\xff" + b"\x00" * (feeds.MAX_IMAGE_BYTES + 1), "image/jpeg"),
], ids=["svg", "type-lie", "html", "too-big"])
def test_bad_pictures_are_refused_but_the_card_still_shows(body, ctype, monkeypatch):
    _ready(feed_manifest(card=IMAGE_CARD))
    net = fake_network(monkeypatch)
    doc = {"item": {"pic": "https://img.example.org/a.png", "name": "Ring"}}
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(doc).encode()))
    net.on("https://img.example.org/", FakeResponse(200, body, ctype))
    card = feeds.refresh("skyshots")["card"]
    assert "image" not in card and card["headline"] == "Ring"
    folder = feeds.images_dir("skyshots")
    assert not os.path.isdir(folder) or not os.listdir(folder)


def test_a_picture_on_an_undeclared_host_is_never_fetched(monkeypatch):
    _ready(feed_manifest(card=IMAGE_CARD))
    net = fake_network(monkeypatch)
    doc = {"item": {"pic": "https://tracker.example.net/p.png", "name": "Ring"}}
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(doc).encode()))
    feeds.refresh("skyshots")
    assert [c["url"] for c in net.calls] == ["https://api.example.org/v1/latest?x=1"]


def test_choices_only_take_declared_options(monkeypatch):
    manifest = feed_manifest(url="https://api.example.org/s?q={topic}",
                             params={"topic": {"label": "Subject",
                                               "choices": ["saturn", "mars"],
                                               "default": "saturn"}})
    _ready(manifest)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    feeds.set_choice("skyshots", "topic", "mars")
    with pytest.raises(feeds.FeedError):
        feeds.set_choice("skyshots", "topic", "pluto&x=1")
    feeds.refresh("skyshots")
    assert net.calls[0]["url"] == "https://api.example.org/s?q=mars"
    assert "the subject you picked" in feeds.service_state("skyshots")["what_leaves"]


# ── chat answers, no model ───────────────────────────────────────────────────

def test_chat_answers_from_the_stored_card_with_its_credit(monkeypatch):
    _ready()
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    reply = app_fastpath.handle("what's the level?")
    assert reply.startswith("Level: 4.33") and "Data: Example Data" in reply
    reply = app_fastpath.handle("show me the latest sky shots")
    assert "Level: 4.33" in reply
    assert not jargon_in(reply)


def test_chat_says_not_set_up_and_how_when_the_service_is_shut(monkeypatch):
    _ready(allow=False)
    no_network(monkeypatch)
    reply = app_fastpath.handle("show me the latest Sky Shots")
    assert "not set up yet" in reply and "puffpai apps allow skyshots" in reply
    assert not jargon_in(reply)


def test_chat_never_captures_ordinary_questions_about_the_subject(monkeypatch):
    _ready()
    no_network(monkeypatch)
    for text in ("what is the sky made of", "tell me about levels",
                 "show me the latest news", "what's the weather",
                 "what is the level of the sea", "show me sky shots of mars"):
        reply = app_fastpath.handle(text) or ""
        assert "Example Data" not in reply and "Sky Shots" not in reply, text


def test_chat_list_and_picture_answers(monkeypatch):
    _ready(feed_manifest(card={"kind": "list", "title": "Rows", "items": "rows",
                               "item_title": "name", "item_detail": "note"}))
    net = fake_network(monkeypatch)
    doc = {"rows": [{"name": f"R{i}", "note": "n"} for i in range(7)]}
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(doc).encode()))
    reply = app_fastpath.handle("show me the latest sky shots")
    assert reply.startswith("The latest from Sky Shots: R0 (n); R1")
    assert "And 2 more" in reply


# ── the dashboard widget ─────────────────────────────────────────────────────

def test_the_widget_names_an_installed_feed_app_and_nothing_else(monkeypatch, tmp_path):
    monkeypatch.setattr(dashboard_widgets, "_REGISTRY_PATH",
                        str(tmp_path / "widgets.json"))
    _ready()
    ok, message, widget = dashboard_widgets.add_widget(
        "app-feed", {"app_id": "skyshots", "url": "https://evil.example.com/"})
    assert ok and widget["props"] == {"app_id": "skyshots", "title": "Sky Shots"}
    ok, message, _ = dashboard_widgets.add_widget("app-feed", {"app_id": "skyshots"})
    assert not ok and "already" in message
    ok, message, _ = dashboard_widgets.add_widget("app-feed", {"app_id": "nothere"})
    assert not ok and "No installed app" in message


# ── the truth rows ──────────────────────────────────────────────────────────

def test_ability_rows_say_available_only_when_it_really_is():
    _ready(allow=False)
    rows = {r["id"]: r for r in feeds.ability_rows()}
    row = rows["live:skyshots"]
    assert row["status"] == "not_set_up"
    assert row["how_to_add"] == "puffpai apps allow skyshots"
    feeds.set_allowed("skyshots", True)
    row = {r["id"]: r for r in feeds.ability_rows()}["live:skyshots"]
    assert row["status"] == "available" and row["how_to_add"] is None
    assert set(row) == {"id", "label", "status", "how_to_add"}


def test_every_owner_facing_sentence_is_plain(monkeypatch):
    set_posture("island")
    install(feed_manifest())
    texts = []
    for allowed in (None, True, False):
        if allowed is not None:
            state = feeds.set_allowed("skyshots", allowed)
        else:
            state = feeds.service_state("skyshots")
        texts += [state["message"], state["what_leaves"],
                  state["how_to_fix"] or ""]
    card = feeds.card_state("skyshots")
    texts += [card["message"], card["service_message"], card["how_to_fix"] or ""]
    texts += [r["label"] + " " + (r["how_to_add"] or "")
              for r in feeds.ability_rows()]
    for text in texts:
        assert not jargon_in(text), (text, jargon_in(text))


def test_a_yes_on_a_bot_set_to_stay_offline_gives_a_plain_fix(monkeypatch):
    no_network(monkeypatch)
    set_posture("island")
    install(feed_manifest())
    state = feeds.set_allowed("skyshots", True)
    assert state["state"] == "bot_offline"
    # A real command that runs, with no raw option value in it (one plain
    # command instead of re-running the whole wizard).
    assert state["how_to_fix"] == "puffpai services on skyshots"
    assert "the outside services you said yes to" in state["message"]
    assert not jargon_in(state["message"] + " " + state["how_to_fix"])
    row = {r["id"]: r for r in feeds.ability_rows()}["live:skyshots"]
    assert row["status"] == "not_set_up"
    assert row["how_to_add"] == "puffpai services on skyshots"
    reply = app_fastpath.handle("show me the latest sky shots")
    assert reply and "puffpai services on skyshots" in reply
    assert not jargon_in(reply)
    assert reply.startswith("You said yes to") and "not set up yet" not in reply


def test_the_plain_fix_command_really_opens_the_service(monkeypatch, capsys):
    from puffpai import cli
    from puffpai.security import egress_doors
    no_network(monkeypatch)
    set_posture("island")
    install(feed_manifest())
    feeds.set_allowed("skyshots", True)
    assert cli.main(["services", "on", "skyshots"]) == 0
    out = capsys.readouterr().out
    assert "outside services you said yes to" in out and not jargon_in(out)
    assert feeds.service_state("skyshots")["state"] == "open"
    assert egress_doors.run_posture()["switches"] == {
        k: False for k in egress_doors.run_posture()["switches"]}


def test_after_a_no_the_last_copy_is_labelled_as_not_kept_up_to_date(monkeypatch):
    _ready()
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(200, json.dumps(VALUE_DOC).encode()))
    feeds.refresh("skyshots")
    feeds.set_allowed("skyshots", False)
    no_network(monkeypatch)
    reply = app_fastpath.handle("what's the level")
    assert reply.startswith("Level: 4.33")
    assert "not getting new data" in reply
    assert reply.endswith("To turn it back on, run: puffpai apps allow skyshots")


def test_an_app_named_like_its_service_is_not_named_twice():
    set_posture("enumerated")
    manifest = feed_manifest()
    manifest["name"] = "Example Data"
    install(manifest)
    assert feeds.set_allowed("skyshots", True)["message"] == (
        "Example Data may fetch from api.example.org.")
    assert feeds.set_allowed("skyshots", False)["message"] == (
        "You said no to Example Data, so nothing new is fetched.")
