"""Live cards show real content, about the owner's subject, and are looked at
before they are offered.

All reported "ok" by the card itself:

* weather showed ``longitude: -83.01``, fossils ``elapsed time: 0`` and music
  a bare ``count: 294`` -- numbers every reply carries about itself;
* the wildlife card showed a random butterfly for a vegetable garden in Ohio:
  the card read the list's one-row sample address (``taxa?q=monarch``);
* a third of the websites owners said yes to never arrived, and the fix line
  for each was ``puffpai setup``, which asks the same website the same thing.

No network: every reply here is a literal.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json

import pytest

from puffpai import feed_generator, feeds
from puffpai.wizard import fixes

OPEN_METEO = {"latitude": 39.99927, "longitude": -83.012764,
              "generationtime_ms": 0.06, "utc_offset_seconds": 0,
              "elevation": 247.0,
              "current_units": {"time": "iso8601", "interval": "seconds",
                                "temperature_2m": "C"},
              "current": {"time": "2026-09-24T06:00", "interval": 900,
                          "temperature_2m": 14.5}}
MUSICBRAINZ = {"created": "2026-09-24T06:02:00Z", "count": 294, "offset": 0,
               "artists": [{"id": "b10bbbfc-cf9e-42e0-be17-e2c3e1d2600d",
                            "name": "The Beatles",
                            "disambiguation": "UK rock band"}]}
PBDB = {"elapsed_time": 0.0, "records": [{"oid": "txn:1", "nam": "T"}]}


def _cand(**extra):
    base = {"id": "seed:x", "kind": "live_data", "name": "Open-Meteo weather",
            "what_it_is": "Weather forecast for your town: temperature, rain, "
                          "wind and clouds.",
            "source_catalog": "api_seeds", "host": "api.example.org",
            "sample_endpoint": "https://api.example.org/v1/sample?x=1",
            "auth": "none", "license": "CC-BY-4.0", "match_terms": ["garden"]}
    base.update(extra)
    return base


def _probe(document):
    body = json.dumps(document).encode()
    calls = []

    def probe(url, host, build_online, max_bytes):
        calls.append(url)
        return {"status": 200, "content_type": "application/json",
                "bytes": len(body), "truncated": False, "body": body}
    probe.calls = calls
    return probe


def test_housekeeping_numbers_never_become_cards():
    keys = [o.get("value_key") for o in feed_generator.inspect(OPEN_METEO)]
    assert "temperature_2m" in keys
    for junk in ("latitude", "longitude", "generationtime_ms", "elevation",
                 "interval", "utc_offset_seconds"):
        assert junk not in keys, junk
    assert all(o.get("value_key") != "count"
               for o in feed_generator.inspect(MUSICBRAINZ))
    assert all(o.get("value_key") != "elapsed_time"
               for o in feed_generator.inspect(PBDB))


def test_the_weather_card_shows_the_temperature_with_its_unit():
    draft = feed_generator.draft_feed_app(_cand(), build_online=True,
                                          probe=_probe(OPEN_METEO))
    card = draft["preview"]
    assert card["kind"] == "value" and card["value"] == "14.5"
    assert card["unit"] == "C" and "emperature" in card["label"]


def test_one_entry_is_still_a_card():
    cand = _cand(name="MusicBrainz", what_it_is="Facts about bands.")
    draft = feed_generator.draft_feed_app(cand, build_online=True,
                                          probe=_probe(MUSICBRAINZ))
    assert draft["preview"]["items"][0]["title"] == "The Beatles"


def test_a_number_the_description_does_not_name_is_refused():
    cand = _cand(name="Fossils", what_it_is="Fossil finds around the world.")
    with pytest.raises(feed_generator.DraftError) as caught:
        feed_generator.draft_feed_app(cand, build_online=True,
                                      probe=_probe({"elapsed_time": 0.1,
                                                    "rows_seen": 3}))
    assert "worth showing" in caught.value.message


def test_a_declared_card_address_asks_about_the_owners_subject_near_them(monkeypatch):
    monkeypatch.setattr(feeds, "home_block", lambda: {"lat": 40.0, "lon": -83.0})
    doc = {"results": [{"taxon": {"preferred_common_name": "garden sage"},
                        "place_guess": "Xenia, OH",
                        "photos": [{"url": "https://static.example.org/p/1.jpg"}]},
                       {"taxon": {"preferred_common_name": "tomato hornworm"},
                        "place_guess": "Columbus, OH",
                        "photos": [{"url": "https://static.example.org/p/2.jpg"}]}]}
    cand = _cand(name="iNaturalist", what_it_is="Plants and animals people spotted.",
                 card_url="https://api.example.org/v1/obs?q={topic}&lat={lat_round}"
                          "&lng={lon_round}&per_page=5",
                 card_topic="monarch", match_terms=["garden", "plant"],
                 card={"kind": "list", "items": "results",
                       "item_title": "taxon.preferred_common_name",
                       "item_detail": "place_guess", "item_image": "photos[0].url"})
    probe = _probe(doc)
    draft = feed_generator.draft_feed_app(
        cand, build_online=True, probe=probe,
        goal="A garden helper for my vegetable beds in Ohio")
    assert probe.calls == ["https://api.example.org/v1/obs?q=garden&lat=40.0"
                           "&lng=-83.0&per_page=5"]
    feed = draft["manifest"]["feed"]
    assert feed["url"].endswith("q={topic}&lat={lat_round}&lng={lon_round}&per_page=5")
    assert feed["params"]["topic"]["default"] == "garden"
    assert feed["service"]["image_host"] == "static.example.org"
    assert draft["preview"]["items"][0]["title"] == "garden sage"


def test_with_no_town_the_subject_is_kept_and_the_town_left_out(monkeypatch):
    monkeypatch.setattr(feeds, "home_block", lambda: {})
    cand = feed_generator.check_candidate(_cand(
        card_url="https://api.example.org/v1/obs?q={topic}&lat={lat_round}"
                 "&lng={lon_round}&per_page=5", card_topic="monarch"))
    template, params, look = feed_generator.card_address(cand, "tomato")
    assert template == "https://api.example.org/v1/obs?q={topic}&per_page=5"
    assert look == "https://api.example.org/v1/obs?q=tomato&per_page=5"


def test_the_look_before_the_plan_uses_the_neutral_sample(monkeypatch):
    monkeypatch.setattr(feeds, "home_block", lambda: {"lat": 40.0, "lon": -83.0})
    probe = _probe(OPEN_METEO)
    got = feed_generator.preview(_cand(card_url="https://api.example.org/v1/f?"
                                                "lat={lat_round}&lon={lon_round}"),
                                 build_online=True, probe=probe)
    assert got == {"ok": True, "looked": True, "why": "",
                   "shows": "Temperature: 14.5 C"}
    assert probe.calls == ["https://api.example.org/v1/sample?x=1"]


def test_a_look_that_finds_nothing_is_told_apart_from_no_look():
    empty = feed_generator.preview(_cand(), build_online=True,
                                   probe=_probe({"generationtime_ms": 1}))
    assert empty["ok"] is False and empty["looked"] is True
    offline = feed_generator.preview(_cand(), build_online=False)
    assert offline["ok"] is False and offline["looked"] is False


def test_a_card_that_did_not_arrive_offers_no_useless_command():
    gap = fixes.gap(skill="factory", what="Live: X was not added.", why="no",
                    impact="", fix={"kind": "none"})
    assert fixes.fix_line(gap) == ""
    from puffpai.wizard import render_cli
    text = "\n".join(render_cli.card_lines({"title": "Finishing", "fields": [],
                                             "gaps": [gap]}))
    assert "puffpai setup" not in text
    assert "There is nothing to run for this yet." in text

