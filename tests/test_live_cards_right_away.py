"""Live cards work right away.

* Switching a card on starts its first fetch at once, in the background, so
  its tile shows data within seconds instead of "Nothing has been fetched yet"
  until the next scheduled look. Every switch-on path reaches it: the Add page
  and chat (``install_draft`` / ``outside_services.switch_on``), the "What
  leaves your computer" page (``switch_on``), ``puffpai apps allow`` and
  ``puffpai services on``. It still goes through ``refresh``: the ten-minute
  floor and the one-fetch-at-a-time lock hold.
* 7Timer: the old ``api.pl`` address answers a place it has not worked out
  yet with a 302 to ``/bin/astro.php`` (checked live). The row now
  reads ``astro.php`` itself, which labels its JSON ``text/html``; the row
  declares that one label (``json_label``). The redirect rules are unchanged.
* Art Institute of Chicago: its picture server challenges the plain
  User-Agent and lets the same name through in the ``AIC-User-Agent`` header
  its documentation asks for. The row declares that header (``ua_header``);
  its value is always this product's own User-Agent.
* The chat greeting reads naturally for any topic ("about bread", never
  "helper for bread"), with a hook for the topic phrase.

No network: fake transport throughout.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path

import pytest

from livedata_support import (PNG_1PX, FakeResponse, fake_network,
                              feed_manifest, install, no_network, set_posture)
from puffpai import bot_identity, feed_generator, feeds, outside_services
from puffpai.catalogs import api_seeds
from puffpai.security import egress_doors, outside_switches

VALUE_DOC = {"reading": {"level": 4.333, "when": "2026-09-23T18:00"}}
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    egress_doors.clear_app_doors()
    outside_switches.forget()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    outside_switches.forget()
    egress_doors.reset_cache()


def _value_route(net):
    net.on("https://api.example.org/v1/latest",
           FakeResponse(200, json.dumps(VALUE_DOC).encode()))


# ---------------------------------------------------------------------------
# 1. switching on starts the first fetch
# ---------------------------------------------------------------------------

def test_a_yes_on_an_online_bot_starts_the_first_fetch(monkeypatch, first_fetches):
    set_posture("enumerated")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    _value_route(net)
    state = feeds.set_allowed("skyshots", True)
    assert state["state"] == "open" and state["fetching"] is True
    assert first_fetches == ["skyshots"]
    assert net.calls == [], "the switch-on itself never waits on the service"
    assert feeds.card_state("skyshots")["status"] == "never_fetched"
    feeds._run_first_fetch("skyshots")
    card = feeds.card_state("skyshots")
    assert card["status"] == "ok" and card["card"]["value"] == "4.33"
    assert len(net.calls) == 1


def test_a_no_starts_nothing(first_fetches):
    set_posture("enumerated")
    install(feed_manifest())
    feeds.set_allowed("skyshots", False)
    assert first_fetches == []


def test_a_yes_on_a_bot_that_stays_offline_waits_for_services_on(monkeypatch,
                                                                  first_fetches):
    no_network(monkeypatch)
    set_posture("island")
    install(feed_manifest())
    assert feeds.set_allowed("skyshots", True)["state"] == "bot_offline"
    assert first_fetches == []
    state = feeds.services_on("skyshots")
    assert state["state"] == "open" and state["fetching"] is True
    assert first_fetches == ["skyshots"]


def test_the_what_leaves_page_switch_starts_the_first_fetch(monkeypatch,
                                                            first_fetches):
    set_posture("island")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    _value_route(net)
    outside_services.switch_on("card-skyshots")
    assert first_fetches == ["skyshots"]
    feeds._run_first_fetch("skyshots")
    assert feeds.card_state("skyshots")["card"]["value"] == "4.33"


def test_switching_a_built_in_service_on_starts_no_card_fetch(monkeypatch,
                                                              first_fetches):
    no_network(monkeypatch)
    set_posture("enumerated")
    install(feed_manifest())
    outside_services.switch_on("weather")
    assert first_fetches == []


def test_the_first_fetch_keeps_the_ten_minute_floor(monkeypatch, first_fetches):
    set_posture("enumerated")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    _value_route(net)
    feeds.set_allowed("skyshots", True)
    feeds._run_first_fetch("skyshots")
    # Off and straight back on: the copy from a moment ago is not fetched again.
    feeds.set_allowed("skyshots", False)
    feeds.set_allowed("skyshots", True)
    feeds._run_first_fetch("skyshots")
    assert len(net.calls) == 1
    assert first_fetches == ["skyshots", "skyshots"]


def test_the_running_bot_fetches_in_a_background_thread(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    gate = threading.Event()
    real = net.request

    def slow(method, url, **kwargs):
        gate.wait(5)
        return real(method, url, **kwargs)
    monkeypatch.setattr(egress_doors, "_send", slow)
    _value_route(net)
    monkeypatch.setattr(feeds, "_first_fetch_starter", feeds._start_thread)
    state = feeds.set_allowed("skyshots", True)
    assert state["fetching"] is True
    # The switch-on returned while the service had not answered yet.
    assert feeds.card_state("skyshots")["status"] == "never_fetched"
    gate.set()
    for thread in threading.enumerate():
        if thread.name.startswith("live-card-first-fetch-skyshots"):
            thread.join(5)
    assert feeds.card_state("skyshots")["card"]["value"] == "4.33"


def test_too_many_requests_waits_once_and_tries_again(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    replies = [FakeResponse(429, b"{}"),
               FakeResponse(200, json.dumps(VALUE_DOC).encode())]
    real = net.request

    def request(method, url, **kwargs):
        real(method, url, **kwargs)
        return replies.pop(0)
    monkeypatch.setattr(egress_doors, "_send", request)
    feeds.set_allowed("skyshots", True)
    waits = []
    feeds._run_first_fetch("skyshots", sleep=waits.append)
    assert waits == [feeds.FIRST_FETCH_RETRY_SECONDS] and waits[0] >= 5
    assert len(net.calls) == 2
    state = feeds.card_state("skyshots")
    assert state["status"] == "ok" and state["card"]["value"] == "4.33"
    assert "busy" not in feeds.read_cache("skyshots")


@pytest.mark.parametrize("status", [429, 500])
def test_a_failed_retry_or_another_error_is_not_retried_again(monkeypatch, status):
    set_posture("enumerated")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/", FakeResponse(status, b"{}"))
    feeds.set_allowed("skyshots", True)
    waits = []
    feeds._run_first_fetch("skyshots", sleep=waits.append)
    assert len(net.calls) == (2 if status == 429 else 1)
    assert waits == ([feeds.FIRST_FETCH_RETRY_SECONDS] if status == 429 else [])
    assert feeds.card_state("skyshots")["status"] == "error"


def test_the_add_more_switch_on_line_is_now_true(monkeypatch, first_fetches):
    from puffpai import add_more
    from puffpai.security import consent_tickets
    set_posture("island")
    install(feed_manifest())
    fake_network(monkeypatch)
    line = add_more.switch_on("card-skyshots", ticket=None,
                              device=consent_tickets.terminal_device_id(),
                              surface="terminal")
    assert line.endswith("It fetches its data now.")
    assert first_fetches == ["skyshots"]


# ---------------------------------------------------------------------------
# 2. a service's own app-naming header, and its odd JSON label
# ---------------------------------------------------------------------------

def _art_manifest(**service_extra):
    manifest = feed_manifest(
        card={"kind": "gallery", "title": "Art", "items": "data", "item_title": "title",
              "item_image_template":
                  "https://img.example.org/iiif/{image_id}/full/400,/0/default.jpg"},
        url="https://api.example.org/v1/art")
    manifest["feed"]["service"].update(service_extra)
    return manifest


def test_a_declared_ua_header_rides_on_every_request_with_our_own_name(monkeypatch):
    set_posture("enumerated")
    install(_art_manifest(ua_header="AIC-User-Agent"))
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/v1/art", FakeResponse(200, json.dumps(
        {"data": [{"title": f"Work {i}", "image_id": f"abc-{i}"} for i in range(3)]}
    ).encode()))
    net.on("https://img.example.org/", FakeResponse(200, PNG_1PX, "image/png"))
    feeds.set_allowed("skyshots", True)
    feeds._run_first_fetch("skyshots")
    assert len(net.calls) == 4
    for call in net.calls:
        assert call["headers"]["AIC-User-Agent"] == egress_doors.FEED_USER_AGENT
        assert call["headers"]["User-Agent"] == egress_doors.FEED_USER_AGENT
    items = feeds.card_state("skyshots")["card"]["items"]
    assert [bool(i.get("image")) for i in items] == [True, True, True]


def test_without_a_declared_header_none_is_sent(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    net = fake_network(monkeypatch)
    _value_route(net)
    feeds.set_allowed("skyshots", True)
    feeds._run_first_fetch("skyshots")
    assert set(net.calls[0]["headers"]) == {"User-Agent", "Accept"}


@pytest.mark.parametrize("field, value", [
    ("ua_header", "X-Api-Key"), ("ua_header", "Authorization"),
    ("ua_header", "aic-user-agent"), ("json_label", "text/xml"),
    ("json_label", "application/octet-stream")])
def test_a_manifest_cannot_name_any_other_header_or_label(field, value):
    manifest = _art_manifest(**{field: value})
    assert feeds.validate_feed(manifest["id"], manifest["feed"]) is not None


def test_request_headers_never_take_a_value_from_the_manifest():
    got = feeds.request_headers({"service": {"ua_header": "AIC-User-Agent",
                                             "AIC-User-Agent": "evil"}},
                                "application/json")
    assert got == {"User-Agent": egress_doors.FEED_USER_AGENT,
                   "Accept": "application/json",
                   "AIC-User-Agent": egress_doors.FEED_USER_AGENT}


@pytest.mark.parametrize("declared, body, ok", [
    ("text/html", json.dumps(VALUE_DOC).encode(), True),
    (None, json.dumps(VALUE_DOC).encode(), False),
    ("text/html", b"<html><body>busy</body></html>", False),
])
def test_an_odd_json_label_is_read_only_when_declared(monkeypatch, declared, body, ok):
    set_posture("enumerated")
    manifest = feed_manifest()
    if declared:
        manifest["feed"]["service"]["json_label"] = declared
    install(manifest)
    net = fake_network(monkeypatch)
    net.on("https://api.example.org/v1/latest",
           FakeResponse(200, body, "text/html; charset=UTF-8"))
    feeds.set_allowed("skyshots", True)
    feeds._run_first_fetch("skyshots")
    assert (feeds.card_state("skyshots")["status"] == "ok") is ok


def _seed(row_id):
    return next(r for r in api_seeds.load_rows() if r["id"] == row_id)


def test_the_7timer_row_reads_astro_php_and_declares_its_label():
    row = _seed("seventimer")
    assert api_seeds.row_problem(row) is None
    for key in ("sample_endpoint", "card_url"):
        assert row[key].startswith("https://www.7timer.info/bin/astro.php?")
        assert "api.pl" not in row[key]
    assert row["json_label"] == "text/html"
    cand = next(c for c in api_seeds.candidates() if c["id"] == "seed:seventimer")
    assert cand["json_label"] == "text/html"


def test_the_art_institute_row_has_pictures_and_names_its_header():
    row = _seed("artic")
    assert api_seeds.row_problem(row) is None
    assert row["ua_header"] == "AIC-User-Agent"
    assert row["image_template"].startswith("https://www.artic.edu/iiif/2/{image_id}/")
    assert "{topic}" in row["card_url"] and "image_id" in row["card_url"]


@pytest.mark.parametrize("field, value", [("ua_header", "X-Anything"),
                                          ("json_label", "text/xml")])
def test_a_seed_row_with_another_header_or_label_is_refused(field, value):
    row = dict(_seed("artic"), **{field: value})
    assert field in (api_seeds.row_problem(row) or "")


def test_the_generator_carries_both_into_the_card(monkeypatch):
    body = json.dumps({"data": [{"title": f"Work {i}", "image_id": f"abc-{i}"}
                                for i in range(4)]}).encode()

    def probe(url, host, build_online, max_bytes):
        return {"status": 200, "content_type": "text/html; charset=UTF-8",
                "bytes": len(body), "truncated": False, "body": body}
    cand = {"id": "seed:art", "kind": "live_data", "name": "Art service",
            "what_it_is": "Artworks with titles and pictures.",
            "source_catalog": "api_seeds", "host": "api.art.example.org",
            "sample_endpoint": "https://api.art.example.org/search?q=cats",
            "auth": "none", "license": "CC0-1.0", "match_terms": ["art"],
            "image_template": "https://www.art.example.org/iiif/{image_id}/full/400,/0/default.jpg",
            "ua_header": "AIC-User-Agent", "json_label": "text/html"}
    draft = feed_generator.draft_feed_app(cand, build_online=True, probe=probe)
    service = draft["manifest"]["feed"]["service"]
    assert service["ua_header"] == "AIC-User-Agent"
    assert service["json_label"] == "text/html"
    # Without the declared label the same reply makes no card.
    cand.pop("json_label")
    with pytest.raises(feed_generator.DraftError):
        feed_generator.draft_feed_app(dict(cand, id="seed:art2"), build_online=True,
                                      probe=probe)


def test_the_build_look_still_refuses_every_redirect():
    # The fix is in the data; the zero-redirect rule of the build look stays.
    src = (REPO / "src" / "puffpai" / "security" / "egress_doors.py").read_text(
        encoding="utf-8")
    body = src[src.index("def build_probe_get("):src.index("SAMPLE_LOOK_SERVICE = ")]
    assert "allow_redirects=False" in body and "redirected_away" in body


# ---------------------------------------------------------------------------
# 3. the chat's role sentence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("goal, topic", [
    ("A bot that helps me learn to bake bread", "bread"),
    ("Help me track and learn about comets and meteor showers",
     "comets and meteor showers"),
])
def test_the_greeting_puts_the_topic_after_about_only(goal, topic):
    from puffpai import plain_words
    line = bot_identity.greeting("Crumb", goal)
    assert line.startswith("Hello! I'm Crumb, your private helper. ")
    assert f"Ask me anything about {topic}," in line
    assert "helper for" not in line and "—" not in line
    assert plain_words.check(line) == []


def test_the_role_sentences_use_the_topic_phrase(monkeypatch):
    goal = "A bot that helps me learn to bake bread"
    assert bot_identity.role_topic(goal) == bot_identity.topic_phrase(goal)
    monkeypatch.setattr(bot_identity, "topic_phrase",
                        lambda g: "baking bread at home")
    assert bot_identity.role_topic(goal) == "baking bread at home"
    assert "anything about baking bread at home," in bot_identity.greeting("Crumb", goal)
    monkeypatch.setattr(bot_identity, "topic_phrase", lambda g: "")
    assert bot_identity.role_topic(goal) == ""
    assert "anything about" not in bot_identity.greeting("Crumb", goal)


# ---------------------------------------------------------------------------
# 4. the generic app page speaks plain words
# ---------------------------------------------------------------------------

def _page_text() -> list[str]:
    """The words the generic app page can show: JSX text and button labels."""
    src = (REPO / "dashboard" / "app" / "dashboard" / "apps" / "[id]" /
           "page.tsx").read_text(encoding="utf-8")
    body = src[src.index("if (loading) {"):]
    body = re.sub(r"\{/\*.*?\*/\}", " ", body, flags=re.S)
    texts = [" ".join(t.split()) for t in re.findall(r">([^<>{}]*[A-Za-z][^<>{}]*)<", body)]
    texts += re.findall(r'label=\{?[`"]([^`"]+)[`"]', body)
    texts += re.findall(r'set(?:Draft|Edit)?Error\("([^"]+)"\)', src)
    # A placeholder (the app's own name, a count) is not a word on the page.
    return [re.sub(r"\$\{[^}]*\}", "Name", t) for t in texts if t.strip()]


def test_the_generic_app_page_has_no_developer_words():
    from puffpai import plain_words
    texts = _page_text()
    assert len(texts) > 20
    joined = "\n".join(texts)
    assert plain_words.check(joined, also=("persona", "backend", "vocabulary",
                                           "rescan", "generic page", "schema",
                                           "declared", "router", "docs/")) == []
    assert "—" not in joined
    src = (REPO / "dashboard" / "app" / "dashboard" / "apps" / "[id]" /
           "page.tsx").read_text(encoding="utf-8")
    shown = re.sub(r"\{/\*.*?\*/\}", " ", src[src.index("if (loading) {"):], flags=re.S)
    for gone in ("vocabulary.keywords", "Delete this persona", "Generic page",
                 "/apps/{id}/data", "declared no data shape", "Saying it out loud"):
        assert gone not in shown
