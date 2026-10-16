"""What leaves your computer: every outside service listed, every switch real.

A switch that is OFF must refuse the next request before a socket opens, and
the refusal must be in the log. Proven here for each kind of service: a
built-in one, a live card, the book library, the list refreshes, the sample
look and the internet check.
"""

from __future__ import annotations

import json
import sys
import types

import pytest

from livedata_support import (FakeResponse, fake_network, feed_manifest,
                              install, jargon_in, no_network, set_posture)
from puffpai import outside_services, source_fetch
from puffpai.catalogs import live
from puffpai.security import egress_doors, egress_ledger, outside_switches
from puffpai.wizard import netcheck

SHOWN = ("name", "for", "what_leaves", "state_line", "why_cannot_switch",
         "turned_on_by", "how_to_switch")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(egress_doors.APPS_KILL_ENV, raising=False)
    for d in egress_doors.DOORS:
        monkeypatch.delenv(d.env_var, raising=False)
    monkeypatch.delenv("PUFFPAI_BUILD_PROBES", raising=False)
    monkeypatch.delenv("PUFFPAI_WIZARD_NET_PROBE", raising=False)
    egress_doors.clear_app_doors()
    outside_switches.forget()
    egress_doors.reset_cache()
    yield
    egress_doors.clear_app_doors()
    outside_switches.forget()
    egress_doors.reset_cache()


def _ids():
    return [r["id"] for r in outside_services.rows()]


def test_every_outside_service_is_listed():
    set_posture("enumerated")
    install(feed_manifest())
    ids = _ids()
    for d in egress_doors.DOORS:
        assert d.id in ids
    for sid in ("card-skyshots", "library", "catalog_refresh", "sample_look",
                "internet_check", "reading_helper", "brain_downloads",
                "web_search"):
        assert sid in ids, sid


def test_every_row_has_the_promised_shape_in_plain_words():
    set_posture("enumerated")
    install(feed_manifest())
    for surface in ("browser", "terminal"):
        page = outside_services.page(surface)
        assert "every request BuildBot makes" in page["line"]
        assert any("Ollama" in line for line in page["not_seen"])
        for row in page["services"]:
            for key in ("id", "name", "for", "what_leaves", "state",
                        "can_switch", "turned_on_by", "turned_on_at"):
                assert key in row
            text = " ".join(str(row[k]) for k in SHOWN)
            assert not jargon_in(text), (row["id"], jargon_in(text))
        text = " ".join([page["line"], page["home_network_line"],
                         page["log_line"], *page["not_seen"]])
        assert not jargon_in(text)


def test_each_surface_gets_its_own_switch_wording():
    set_posture("enumerated")
    browser = outside_services.page("browser")["services"]
    terminal = outside_services.page("terminal")["services"]
    weather_b = next(r for r in browser if r["id"] == "weather")
    weather_t = next(r for r in terminal if r["id"] == "weather")
    assert weather_b["how_to_switch"] == "Switch on"
    assert weather_t["how_to_switch"] == f"puffpai outside on {weather_t['number']}"


def test_fixed_rows_cannot_be_switched_and_say_why():
    set_posture("enumerated")
    # web_search became a real service (keyless Wikipedia
    # search, switchable like any other), so it is no longer a fixed row.
    for sid in ("brain_downloads", "reading_helper"):
        row = outside_services.row(sid)
        assert row["can_switch"] is False and row["why_cannot_switch"]
        with pytest.raises(outside_services.OutsideError):
            outside_services.switch_off(sid)
        with pytest.raises(outside_services.OutsideError):
            outside_services.proposal(sid)


def test_an_operator_setting_is_not_overruled_from_here(monkeypatch):
    set_posture("enumerated")
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "off")
    row = outside_services.row("weather")
    assert row["can_switch"] is False and "settings file" in row["why_cannot_switch"]


def test_the_reading_helper_row_comes_from_the_indexing_lane_when_present(
        monkeypatch):
    fake = types.ModuleType("puffpai.knowledge.reading_helper")
    fake.outside_row = lambda: {"name": "Reading helper", "for": "Finding passages.",
                                "what_leaves": "Nothing: it came with the install.",
                                "state": "not switchable", "can_switch": False}
    monkeypatch.setitem(sys.modules, "puffpai.knowledge.reading_helper", fake)
    import puffpai.knowledge as knowledge
    monkeypatch.setattr(knowledge, "reading_helper", fake, raising=False)
    row = outside_services.row("reading_helper")
    assert row["what_leaves"] == "Nothing: it came with the install."


# ── OFF refuses the next request, and the refusal is logged ──────────────────

def test_a_built_in_service_switched_off_is_refused_and_logged(monkeypatch):
    set_posture("enumerated")
    net = fake_network(monkeypatch)
    net.on("https://api.open-meteo.com/", FakeResponse(200, b"{}"))
    outside_switches.set_switch("weather", True, by="owner")
    egress_doors.reset_cache()
    egress_doors.egress_get("weather", "https://api.open-meteo.com/v1/forecast"
                            "?latitude=39.8&longitude=-84.2")
    assert len(net.calls) == 1
    outside_services.switch_off("weather")
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.egress_get("weather", "https://api.open-meteo.com/v1/forecast"
                                "?latitude=39.8&longitude=-84.2")
    assert exc.value.code == "switched_off" and len(net.calls) == 1
    lines = egress_ledger.read(1)
    assert [(l["outcome"], l["code"]) for l in lines][:2] == [
        ("refused", "switched_off"), ("done", "")]
    raw = open(egress_ledger.log_path(), encoding="utf-8").read()
    assert "39.8" not in raw and "latitude" not in raw


def test_off_beats_the_config_file_and_the_wizard(monkeypatch):
    set_posture("enumerated")
    from puffpai import setup_ops
    setup_ops.write_main_config({"egress_doors": {"weather": True}})
    egress_doors.reset_cache()
    assert egress_doors.egress_allowed("weather")[0] is True
    outside_services.switch_off("weather")
    assert egress_doors.egress_allowed("weather")[0] is False


def test_a_live_card_switched_off_is_refused_and_logged(monkeypatch):
    set_posture("enumerated")
    install(feed_manifest())
    from puffpai import feeds
    feeds.set_allowed("skyshots", True)
    no_network(monkeypatch)
    outside_services.switch_off("card-skyshots")
    with pytest.raises(egress_doors.EgressBlocked):
        egress_doors.app_egress_get("skyshots", "https://api.example.org/v1/x")
    (line,) = egress_ledger.read(1)[:1]
    assert line["service"] == "card-skyshots" and line["outcome"] == "refused"
    assert line["path"] == "", "a card's address may carry owner values"


def test_the_book_library_switched_off_refuses_before_the_transport():
    def transport(*a, **k):
        raise AssertionError("a socket opened for a switched-off library")

    outside_services.switch_off("library")
    body, why = source_fetch.get_small(
        "https://opds.library.kiwix.org/v2/entries?q=dinosaurs",
        allowed_hosts={"opds.library.kiwix.org"}, max_bytes=1000,
        honour_robots=False, transport=transport,
        resolver=lambda h: ["93.184.216.34"])
    assert body is None and "switched off" in why
    (line,) = egress_ledger.read(1)[:1]
    assert (line["service"], line["outcome"], line["code"]) == (
        "library", "refused", "switched_off")
    assert "dinosaurs" not in open(egress_ledger.log_path(), encoding="utf-8").read()


def test_a_library_search_is_logged_host_only():
    class Resp:
        status = 200
        headers = {"Content-Length": "5"}

        def read(self, n=-1):
            return b"<a/>"

        def close(self):
            pass

    body, _why = source_fetch.get_small(
        "https://opds.library.kiwix.org/v2/entries?q=my+son+jake",
        allowed_hosts={"opds.library.kiwix.org"}, max_bytes=1000,
        honour_robots=False, transport=lambda u, h, t: Resp(),
        resolver=lambda h: ["93.184.216.34"])
    assert body == b"<a/>"
    (line,) = egress_ledger.read(1)[:1]
    assert (line["service"], line["what"], line["path"]) == (
        "library", "library_search", "")
    assert "jake" not in open(egress_ledger.log_path(), encoding="utf-8").read()


def test_list_refreshes_switched_off_are_refused():
    outside_services.switch_off("catalog_refresh")
    with pytest.raises(live.CatalogFetchError) as exc:
        live.fetch_url("https://api.apis.guru/v2/list.json", hosts=["api.apis.guru"],
                       transport=lambda *a: pytest.fail("socket opened"),
                       resolver=lambda h: ["93.184.216.34"])
    assert "switched off" in str(exc.value)
    assert egress_ledger.read(1)[0]["service"] == "catalog_refresh"


def test_the_sample_look_switched_off_is_refused(monkeypatch):
    outside_services.switch_off("sample_look")
    no_network(monkeypatch)
    with pytest.raises(egress_doors.EgressBlocked) as exc:
        egress_doors.build_probe_get("https://api.example.org/x",
                                     host="api.example.org", build_online=True)
    assert exc.value.code == "switched_off"
    assert egress_ledger.read(1)[0]["service"] == "sample_look"


def test_the_internet_check_switched_off_is_not_checked(monkeypatch):
    monkeypatch.setenv(netcheck.PROBE_ENV, "on")
    netcheck.forget()
    outside_services.switch_off("internet_check")
    import socket
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: pytest.fail("connected while off"))
    assert netcheck.internet_reachable() is None


def test_a_broken_switch_file_reads_as_everything_off():
    with open(outside_switches.path(), "w", encoding="utf-8") as fh:
        fh.write("{not json")
    outside_switches.forget()
    assert outside_switches.is_off("library")
    assert outside_services.row("library")["state"] == "off"


# ── ON: the sentence, and what it changes ────────────────────────────────────

def test_the_sentence_says_what_leaves_and_where():
    set_posture("enumerated")
    text = outside_services.proposal("weather")
    assert "rough location" in text and "api.open-meteo.com" in text
    assert "switch it off again" in text
    assert not jargon_in(text)


def test_on_an_offline_bot_the_sentence_names_the_wider_change():
    set_posture("island")
    install(feed_manifest())
    from puffpai import feeds
    feeds.set_allowed("skyshots", True)  # said yes earlier; off by the setting
    text = outside_services.proposal("weather")
    assert "set to stay off the internet" in text and "Sky Shots" in text
    outside_services.switch_on("weather")
    assert egress_doors.run_posture()["run_egress"] == "enumerated"
    assert egress_doors.egress_allowed("weather")[0] is True


def test_nothing_can_be_switched_on_for_a_bot_that_keeps_everything_local():
    set_posture("sealed")
    row = outside_services.row("weather")
    assert row["can_switch"] is False
    with pytest.raises(outside_services.OutsideError):
        outside_services.proposal("weather")


def test_the_log_reads_as_plain_lines(monkeypatch):
    set_posture("enumerated")
    egress_ledger.record("weather", host="api.open-meteo.com", outcome="done",
                         what="forecast", bytes_in=217000, status=200)
    egress_ledger.record("weather", host="api.open-meteo.com", outcome="refused",
                         code="switched_off", what="forecast")
    rows = outside_services.log_rows(24)
    assert rows[0]["line"].endswith(
        "Weather forecast - asked for the weather forecast - stopped: switched off")
    assert rows[1]["line"].endswith(
        "Weather forecast - asked for the weather forecast - 212 KB")
    assert not jargon_in(json.dumps([r["line"] for r in rows]))


def test_a_service_another_lane_adds_is_listed_once_in_its_own_words(
        monkeypatch):
    set_posture("enumerated")
    # The real web_search service replaced the fixed
    # "not added" row instead of being listed next to it.
    rows = [r for r in outside_services.rows() if r["id"] == "web_search"]
    assert len(rows) == 1 and rows[0]["can_switch"] is True
    assert rows[0]["kind"] == "built_in"
    assert rows[0]["what_leaves"].startswith("The words you asked me to search")


def test_the_not_seen_list_drops_the_helper_when_the_indexing_lane_owns_it(
        monkeypatch):
    set_posture("enumerated")
    # The real row: the page lists the helper as a
    # row of its own, with its website, and no longer as something unseen.
    helper = outside_services.row("reading_helper")
    assert helper["kind"] == "reading_helper"
    assert helper["websites"] == ["chroma-onnx-models.s3.amazonaws.com"]
    assert not any("reading helper" in x
                   for x in outside_services.page()["not_seen"])
    fake = types.ModuleType("puffpai.knowledge.reading_helper")
    fake.outside_row = lambda: {"name": "Reading helper",
                                "what_leaves": "Nothing: it came with the install.",
                                "state": "not switchable"}
    monkeypatch.setitem(sys.modules, "puffpai.knowledge.reading_helper", fake)
    import puffpai.knowledge as knowledge
    monkeypatch.setattr(knowledge, "reading_helper", fake, raising=False)
    assert not any("reading helper" in x
                   for x in outside_services.page()["not_seen"])


def test_switching_the_library_off_beats_a_remembered_search(monkeypatch):
    """A search answer kept for 10 minutes is never reused once the owner
    switched the book library off: the next search goes to the fetch, which
    refuses (nothing sent), and the search says it used the saved list."""
    from test_catalogs_kiwix import FEED, _row_with

    from puffpai import source_fetch
    from puffpai.catalogs import kiwix_opds
    from puffpai.security import outside_switches

    _row_with(monkeypatch, {"allowed": True, "cache_seconds": 600})
    sent = []

    def fake_get_small(url, *, allowed_hosts, max_bytes, honour_robots):
        if outside_switches.is_off(source_fetch.LIBRARY_SERVICE):
            return None, "The book library is switched off."
        sent.append(url)
        return FEED, "ok"

    monkeypatch.setattr(source_fetch, "get_small", fake_get_small)
    outside_switches.forget()
    try:
        kiwix_opds.search_live("children")
        kiwix_opds.search_live("children")
        assert len(sent) == 1  # the second one was remembered
        outside_switches.set_switch("library", False, by="owner")
        with pytest.raises(kiwix_opds.CatalogError):
            kiwix_opds.search_live("children")
        assert len(sent) == 1  # nothing new was sent
    finally:
        kiwix_opds.clear_search_cache()
        outside_switches.forget()
