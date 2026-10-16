"""Weather needs a town, and the wizard asks for it once.

The plan turned the weather forecast on for a garden bot, and then nobody
asked where the garden was: the weather card and chat could only say "I don't
know your home town yet". Now, when a line on the plan needs the town:

* the terminal asks one question right after "Looks good";
* the browser draws a field on that plan line;
* the town is looked up through the town lookup on What leaves your computer
  and kept the town-level way (a rough position, never the street);
* an empty answer is a skip, and the weather card then says how to add it.

A plan that needs no town (a music bot) never asks. Any bot with the weather
switched on gets the weather card, whatever its page list says.

Nothing here reaches the network: every outside request is answered here.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _wizard_driver import drive_in_process  # noqa: E402
from livedata_support import FIXTURES  # noqa: E402

from puffpai import (brain_health, build_manifest, build_posture,  # noqa: E402
                     hardware_census, home_location, setup_ops,
                     weather_service)
from puffpai.config import get_config  # noqa: E402
from puffpai.security import egress_doors  # noqa: E402
from puffpai.wizard import (factory, netcheck, proposal, render_cli,  # noqa: E402
                            sources_suggest, wording)

GARDEN = "A garden helper for my vegetable beds that warns me about frost"
MUSIC = ("A music bot that helps me learn guitar chords and finds new songs I "
         "would like")
WEATHER_ID = "outside_services:door:weather"
QUESTION = "which town are you in?"

_TOWN = {"results": [
    {"name": "Columbus", "admin1": "Ohio", "country": "United States",
     "country_code": "US", "latitude": 39.96118, "longitude": -82.99879,
     "timezone": "America/New_York"},
    {"name": "Columbus", "admin1": "Georgia", "country": "United States",
     "country_code": "US", "latitude": 32.46098, "longitude": -84.98771,
     "timezone": "America/New_York"}]}
_DAILY = {"daily": {
    "time": ["2026-09-26", "2026-09-27", "2026-09-28"],
    "weather_code": [2, 61, 0],
    "temperature_2m_max": [71.6, 64.2, 75.0],
    "temperature_2m_min": [55.1, 50.4, 57.9],
    "precipitation_probability_max": [10, 80, 0]}}


class _Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _pin_the_day(monkeypatch):
    """The recorded forecast covers 2026-09-26..28, so "today" is pinned to
    2026-09-26 for a chat ask with no clock of its own (it read the real
    clock and failed once the date passed the recording)."""
    from datetime import datetime as _dt, timezone as _tz

    from puffpai import weather_service
    answer = weather_service.chat_answer

    def _on_the_recorded_day(message, *, now=None, **kwargs):
        return answer(message, now=now or _dt(2026, 9, 26, 15, 0, tzinfo=_tz.utc), **kwargs)

    monkeypatch.setattr(weather_service, "chat_answer", _on_the_recorded_day)


@pytest.fixture
def sent(monkeypatch):
    """Every request that would leave, kept here; nothing leaves."""
    seen: list[dict] = []
    _pin_the_day(monkeypatch)

    def _fake(method, url, **kwargs):
        seen.append({"url": url, "params": dict(kwargs.get("params") or {})})
        if "geocoding" in url:
            return _Resp(_TOWN)
        return _Resp(_DAILY)

    monkeypatch.setattr(egress_doors, "_send", _fake)
    return seen


@pytest.fixture
def machine(monkeypatch, tmp_path):
    """Online, nothing downloaded, and every sample look answered from disk."""
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("COLUMNS", "80")
    for var in ("PUFFPAI_EGRESS_WEATHER", "PUFFPAI_EGRESS_GEOCODE"):
        monkeypatch.delenv(var, raising=False)
    get_config(_force_reload=True)
    census = hardware_census.run_census()
    census.setdefault("memory", {})["total_gb"] = 32.0
    monkeypatch.setattr(hardware_census, "run_census",
                        lambda *a, **k: copy.deepcopy(census))
    monkeypatch.setattr(setup_ops, "list_models", lambda *a, **k: [])
    monkeypatch.setattr(setup_ops, "ollama_survey", lambda *a, **k: [])
    monkeypatch.setattr(netcheck, "internet_reachable", lambda *a, **k: True)
    monkeypatch.setattr(sources_suggest, "suggest", lambda *a, **k: [])
    monkeypatch.setattr(brain_health, "pull_model", lambda *a, **k: {
        "ok": False, "message": "tests never download"})
    monkeypatch.setattr(setup_ops, "fetch_source", lambda *a, **k: {
        "ok": False, "reason": "tests never download"})
    body = (FIXTURES / "samples" / "inat.json").read_bytes()
    monkeypatch.setattr(egress_doors, "build_probe_get", lambda url, **kw: {
        "status": 200, "content_type": "application/json",
        "bytes": len(body), "truncated": False, "body": body})
    egress_doors.reset_cache()
    yield tmp_path
    egress_doors.reset_cache()
    get_config(_force_reload=True)


def _record() -> dict:
    with open(os.path.join(get_config().CONFIG_DIR, "wizard_run.json"),
              encoding="utf-8") as handle:
        return json.load(handle)


def _sctx(goal, **kw):
    base = dict(goal=goal, terms=proposal.goal_terms(goal), name="Sprout",
                mode=wording.STANDARD, shared={}, previews={},
                args=types.SimpleNamespace(egress_door=None, skip_ha=True),
                cfg=None, internet=True)
    base.update(kw)
    return proposal.SummaryContext(**base)


def _terminal(goal, town, monkeypatch, capsys):
    return drive_in_process(
        ["setup", "--skip-ha", "--no-suggestions", "--skip-vault",
         "--skip-knowledge"],
        [("what kind of bot", goal), ("how old are you", "40"),
         (QUESTION, town)],
        monkeypatch=monkeypatch, capsys=capsys)


# -- the plan ---------------------------------------------------------------------

def test_the_plan_asks_on_the_weather_line_for_a_garden_bot(machine):
    town = proposal.build(_sctx(GARDEN))["town"]
    assert town["ask"] is True
    assert town["item"] == WEATHER_ID
    assert town["question"] == ("Which town are you in? (only the town, for "
                                "the weather)")
    assert "geocoding-api.open-meteo.com" in town["help"]
    assert "about 11 km" in town["help"]
    assert "weather card" in town["skip"]
    # "What leaves your computer" names the town lookup too.
    summary = proposal.build(_sctx(GARDEN))
    assert "geocoding-api.open-meteo.com" in summary["lines"]["leaves"]
    assert "geocoding-api.open-meteo.com" not in proposal.build(
        _sctx(MUSIC))["lines"]["leaves"]


def test_a_music_bot_is_never_asked(machine):
    assert proposal.build(_sctx(MUSIC))["town"]["ask"] is False


def test_not_asked_when_every_line_that_needs_it_is_off(machine):
    first = proposal.build(_sctx(GARDEN))
    needing = [i for i in proposal.items_of(first) if i.get("needs_town")]
    assert WEATHER_ID in [i["id"] for i in needing]
    # With the forecast off, a live card about the weather where you live
    # (it asks with the town's rough position) still needs the town, and the
    # field moves to that line.
    live = [i["id"] for i in needing if i["section"] == "live_info"]
    summary = proposal.build(_sctx(GARDEN), state={"on": {WEATHER_ID: False}})
    if live:
        assert summary["town"]["ask"] is True
        assert summary["town"]["item"] == live[0]
    off = {i["id"]: False for i in needing}
    summary = proposal.build(_sctx(GARDEN), state={"on": off})
    assert summary["town"]["ask"] is False


def test_not_asked_when_the_bot_stays_off_the_internet(machine):
    summary = proposal.build(_sctx(GARDEN),
                             state={"reach": proposal.REACH_OFFLINE})
    assert summary["town"]["ask"] is False


def test_not_asked_when_the_town_is_already_known(machine):
    home_location.set_home_coords(40.0, -83.0, label="Columbus, Ohio")
    assert proposal.build(_sctx(GARDEN))["town"]["ask"] is False


def test_a_live_line_that_needs_the_town_keeps_saying_so():
    item = proposal.validate_item({
        "id": "live_info:api_seeds:x", "section": "live_info", "label": "Live",
        "detail": "", "size_bytes": None, "outside_host": "api.example.org",
        "default_on": True, "needs_yes": True, "needs_town": True,
        "apply": {"kind": "live_feed", "ref": "x"}})
    assert item["needs_town"] is True
    plain = dict(item)
    plain.pop("needs_town")
    assert "needs_town" not in proposal.validate_item(plain)


def test_the_town_is_kept_only_when_the_plan_asked(machine):
    garden = proposal.build(_sctx(GARDEN))
    state = proposal.decide(garden, changes={"town": "  Columbus,  Ohio "})
    assert state["town_asked"] is True and state["town"] == "Columbus, Ohio"
    music = proposal.build(_sctx(MUSIC))
    state = proposal.decide(music, changes={"town": "Columbus"})
    assert state["town_asked"] is False and state["town"] == ""
    # --yes: nobody was asked, so nothing is looked up.
    state = proposal.decide(garden, changes={"town": "Columbus"},
                            non_interactive=True)
    assert state["town_asked"] is False and state["town"] == ""
    with pytest.raises(proposal.ProposalError):
        proposal.decide(garden, changes={"town": ["Columbus"]})


def test_the_terminal_question_is_skipped_when_not_asked():
    said = []
    assert render_cli.town_screen({"town": {"ask": False}},
                                  ask=lambda p: "x", out=said.append) is None
    assert said == []


def test_the_town_words_are_plain():
    from puffpai import plain_words
    for key in ("town_question", "town_help", "town_skip", "town_skipped"):
        for mode in wording.MODES:
            text = " ".join(wording.say(key, mode, host="h.example.org",
                                        name="Sprout").split())
            assert plain_words.is_plain(text), (key, plain_words.check(text))
            assert "—" not in text


# -- the terminal, end to end -------------------------------------------------------

def test_the_terminal_asks_once_and_the_weather_works_after(
        machine, sent, monkeypatch, capsys):
    code, transcript, asked = _terminal(GARDEN, "Columbus, Ohio", monkeypatch,
                                        capsys)
    assert code == 0, transcript
    assert sum(1 for q in asked if QUESTION in q.lower()) == 1, asked
    assert transcript.lower().count(QUESTION) == 1
    # After "Looks good", before the vault and the building.
    assert transcript.index("Looks good") < transcript.lower().index(QUESTION)
    home = home_location.get_home()
    assert home["label"] == "Columbus, Ohio, United States"
    assert (home["latitude"], home["longitude"]) == (40.0, -83.0)
    # Only the typed name was sent to the town lookup.
    looked = [s for s in sent if "geocoding" in s["url"]]
    assert len(looked) == 1 and looked[0]["params"]["name"] == "Columbus"
    assert "Your home town: Columbus, Ohio, United States" in transcript
    # The weather is on and chat answers from the weather service.
    egress_doors.reset_cache()
    assert weather_service.state()["state"] == weather_service.OK
    reply = weather_service.chat_answer("what's the weather tomorrow?")
    assert "The weather for tomorrow in Columbus, Ohio" in reply
    forecast = [s for s in sent if "api.open-meteo.com" in s["url"]]
    assert forecast[-1]["params"]["latitude"] == 40.0


def test_the_terminal_never_asks_a_music_bot(machine, sent, monkeypatch,
                                             capsys):
    code, transcript, asked = _terminal(MUSIC, "Columbus", monkeypatch, capsys)
    assert code == 0, transcript
    assert not any(QUESTION in q.lower() for q in asked)
    assert QUESTION not in transcript.lower()
    assert home_location.get_home() is None
    assert [s for s in sent if "geocoding" in s["url"]] == []


def test_a_skip_sends_nothing_and_the_card_says_how_to_add_it(
        machine, sent, monkeypatch, capsys):
    code, transcript, _asked = _terminal(GARDEN, "", monkeypatch, capsys)
    assert code == 0, transcript
    assert "Skipped. Type your town on the weather card" in transcript
    assert home_location.get_home() is None
    assert home_location.skipped_in_setup() is True
    assert [s for s in sent if "geocoding" in s["url"]] == []
    card = weather_service.card_without_town()
    assert "You skipped your town" in card["town_later"]
    assert "Type your town below" in card["town_later"]
    # Setting it later clears the note.
    home_location.set_home_coords(40.0, -83.0, label="Columbus, Ohio")
    assert home_location.skipped_in_setup() is False


def test_a_town_the_lookup_cannot_find_is_a_plain_gap(machine, monkeypatch,
                                                      capsys):
    monkeypatch.setattr(egress_doors, "_send",
                        lambda method, url, **kw: _Resp({"results": []}))
    code, transcript, _asked = _terminal(GARDEN, "Nowhereville", monkeypatch,
                                         capsys)
    assert code == 0, transcript
    assert home_location.get_home() is None
    gaps = [g for g in _record()["gaps"]
            if g.get("what") == "Your home town was not set."]
    assert gaps and "couldn't find a town called" in gaps[0]["why"]
    assert "weather card" in gaps[0]["why"]


# -- the browser ---------------------------------------------------------------

def test_the_browser_field_rides_with_looks_good(machine, sent):
    os.environ.setdefault("PUFFPAI_API_KEY", "e" * 64)
    from puffpai import api_server
    web = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": GARDEN}}).status_code == 200
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {"age": "40"}}).status_code == 200
    summary = web.post("/wizard/summary", headers=auth, json={}).json
    assert summary["town"]["ask"] is True
    assert summary["town"]["item"] == WEATHER_ID
    bad = web.post("/wizard/approve", headers=auth,
                   json={"changes": {"on": {}, "town": 7}})
    assert bad.status_code == 400
    books = {i["id"]: False for s in summary["sections"]
             if s["id"] == "knowledge" for i in s["items"]
             if proposal.toggleable(i)}
    assert web.post("/wizard/approve", headers=auth, json={"changes": {
        "on": books, "town": "Columbus, Ohio"}}).status_code == 200
    web.post("/wizard/vault/skip", headers=auth, json={})
    run = web.post("/wizard/run", headers=auth, json={}).json
    assert run["complete"] is True, run.get("outstanding")
    assert home_location.get_home()["label"] == "Columbus, Ohio, United States"
    rows = _record()["shared"]["factory"]["items"]
    assert [r for r in rows if r["kind"] == "home_town" and r["ok"]]
    body = web.post("/chat/send", headers=auth, json={
        "message": "what's the weather tomorrow?"}).get_json()
    assert "The weather for tomorrow in Columbus, Ohio" in body["response"]


# -- the weather card on any bot with the weather on --------------------------

def test_any_bot_with_the_weather_on_has_the_card(machine, monkeypatch):
    posture = build_posture.posture_manifest_block(
        build_posture.assess_posture("online", "enumerated", []))
    build_manifest.write_manifest({
        "schema": build_manifest.SCHEMA, "posture": posture,
        "surfaces": {"allowlist": ["knowledge.library"],
                     "derived_from_goal": True}})
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "off")
    egress_doors.reset_cache()
    assert weather_service.has_card() is False
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "on")
    egress_doors.reset_cache()
    assert weather_service.has_card() is True
    assert weather_service.town_words("chat") == (
        "Type your town on the weather card on the home page.")
    os.environ.setdefault("PUFFPAI_API_KEY", "e" * 64)
    from puffpai import api_server
    web = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    assert web.get("/build/self", headers=auth).json["weather_on"] is True
    monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER", "off")
    egress_doors.reset_cache()
    assert web.get("/build/self", headers=auth).json["weather_on"] is False


def test_the_factory_leaves_the_town_alone_when_not_asked(machine):
    ctx = types.SimpleNamespace(shared={"summary": {
        "approved": True, "items": [], "town_asked": False,
        "town": "Columbus"}}, _sections={})
    got = factory.apply_approved(ctx)
    assert got["shared"]["factory"]["items"] == []
    assert home_location.get_home() is None
