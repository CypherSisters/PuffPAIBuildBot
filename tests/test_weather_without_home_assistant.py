"""Home Assistant is optional; the weather is not.

Most people do not have Home Assistant, and there are other ways to get
the weather. A plan said some parts could not be done because they need
Home Assistant, but they do not. A space bot's plan said the weather
card "needs something you have not switched on: Home Assistant, and the
weather service switched on", offered "Your weather patterns: connect through
Home Assistant", and the running bot had its weather service off.

These tests hold the fix: weather comes from the built-in weather service and
the town-level home, Home Assistant is offered only for home gadgets and only
as an "if", and the widget log lands in the state folder.

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from puffpai import (build_manifest, build_posture, dashboard_widgets,
                     home_location, plain_words, weather_service)
from puffpai.config import get_config
from puffpai.security import egress_doors

GARDEN = ("I want a garden helper for my vegetable beds in Ohio that warns me "
          "about frost and tells me when to plant")
MUSIC = ("A music bot for a 15 year old that helps me learn guitar chords and "
         "finds new songs I would like")
SPACE = ("I would like a bot that knows everything about space. I also like "
         "weather and how our weather patterns are changing.")
LIGHTS = "A bot that turns my lights and plugs on and off and runs my thermostat"

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ── a build, on disk, in the pinned test roots ───────────────────────────────

def _build(run_egress: str = "enumerated", *, weather: bool | None = None,
           town: bool = False, monkeypatch=None) -> None:
    posture = build_posture.posture_manifest_block(
        build_posture.assess_posture("online", run_egress, []))
    build_manifest.write_manifest({"schema": build_manifest.SCHEMA,
                                   "posture": posture})
    if monkeypatch is not None:
        if weather is None:
            monkeypatch.delenv("PUFFPAI_EGRESS_WEATHER", raising=False)
        else:
            monkeypatch.setenv("PUFFPAI_EGRESS_WEATHER",
                               "on" if weather else "off")
    egress_doors.reset_cache()
    if town:
        home_location.set_home_coords(
            40.04, -83.02, label="Columbus, Ohio, United States",
            timezone_name="America/New_York")
    dashboard_widgets._env_cache.clear()


class _Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


_DAILY = {"daily": {
    "time": ["2026-09-26", "2026-09-27", "2026-09-28"],
    "weather_code": [2, 61, 0],
    "temperature_2m_max": [71.6, 64.2, 75.0],
    "temperature_2m_min": [55.1, 50.4, 57.9],
    "precipitation_probability_max": [10, 80, 0]}}


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
        seen.append({"method": method, "url": url,
                     "params": dict(kwargs.get("params") or {})})
        if "air-quality" in url:
            return _Resp({"current": {"us_aqi": 30}})
        if "precipitation_probability_max" in str(kwargs.get("params")):
            return _Resp(_DAILY)
        return _Resp({"daily": {"temperature_2m_max": [72.0],
                                "temperature_2m_min": [55.0],
                                "precipitation_sum": [0.0]},
                      "current": {"relative_humidity_2m": 50,
                                  "temperature_2m": 63.4,
                                  "apparent_temperature": 61.0,
                                  "weather_code": 3}})

    monkeypatch.setattr(egress_doors, "_send", _fake)
    return seen


NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


# ── chat: "what's the weather tomorrow?" ─────────────────────────────────────

@pytest.mark.parametrize("question", [
    "what's the weather tomorrow?", "will it rain today", "what's the forecast",
    "do I need an umbrella today?", "how cold is it outside",
    "what's the weather like this weekend?"])
def test_weather_questions_are_recognised(question):
    assert weather_service.is_weather_question(question)


@pytest.mark.parametrize("question", [
    "what is space weather?", "how are our weather patterns changing?",
    "why does it rain?", "what is the weather on Mars today?",
    "weather in Paris tomorrow", "how cold is space", "tell me about rain forests",
    "how hot is the sun today"])
def test_other_questions_are_left_to_the_library(question):
    assert not weather_service.is_weather_question(question)
    assert weather_service.chat_answer(question) is None


def test_switched_off_says_so_and_where_to_switch_it_on(monkeypatch, sent):
    _build(weather=None, town=True, monkeypatch=monkeypatch)
    reply = weather_service.chat_answer("what's the weather tomorrow?")
    assert "switched off" in reply
    assert "What leaves your computer" in reply
    assert "Home Assistant" not in reply
    assert "api.open-meteo.com" in reply
    assert sent == []
    assert plain_words.is_plain(reply), plain_words.check(reply)


def test_no_town_asks_for_it_and_sends_nothing(monkeypatch, sent):
    _build(weather=True, town=False, monkeypatch=monkeypatch)
    reply = weather_service.chat_answer("what's the weather tomorrow?")
    assert "home town" in reply and "Home Assistant" not in reply
    assert sent == []


def test_both_missing_names_both(monkeypatch, sent):
    _build(weather=None, town=False, monkeypatch=monkeypatch)
    reply = weather_service.chat_answer("will it rain today?")
    assert "switched off" in reply and "home town" in reply
    assert sent == []


def test_a_bot_kept_on_the_computer_does_not_check(monkeypatch, sent):
    _build("island", weather=True, town=True, monkeypatch=monkeypatch)
    reply = weather_service.chat_answer("what's the weather today?")
    assert "keep everything on your computer" in reply
    assert sent == []


def test_tomorrow_is_one_get_with_the_rough_town_only(monkeypatch, sent):
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    reply = weather_service.chat_answer("what's the weather tomorrow?", now=NOW)
    assert len(sent) == 1
    call = sent[0]
    assert call["url"].startswith(weather_service.FORECAST_URL)
    # One decimal (about 11 km): never the typed position, never a name.
    assert call["params"]["latitude"] == 40.0
    assert call["params"]["longitude"] == -83.0
    assert set(call["params"]) == {"latitude", "longitude", "daily",
                                   "temperature_unit", "timezone",
                                   "forecast_days"}
    assert call["params"]["temperature_unit"] == "fahrenheit"
    assert reply.startswith("The weather for tomorrow in Columbus, Ohio")
    assert "rain" in reply and "high 64°F" in reply and "low 50°F" in reply
    assert "80% chance of rain" in reply
    assert "Home Assistant" not in reply


def test_the_weekend_lists_each_day(monkeypatch, sent):
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    reply = weather_service.chat_answer("what's the weather this weekend?",
                                        now=NOW)
    # 2026-09-26 is a Saturday.
    assert "- Today:" in reply and "- Tomorrow:" in reply


def test_a_service_that_does_not_answer_is_said_plainly(monkeypatch):
    _build(weather=True, town=True, monkeypatch=monkeypatch)

    def _down(method, url, **kwargs):
        raise ConnectionError("nobody home")
    monkeypatch.setattr(egress_doors, "_send", _down)
    reply = weather_service.chat_answer("what's the weather today?", now=NOW)
    assert reply == ("The weather service did not answer just now. Please try "
                     "again in a minute.")


# ── the home page weather card ───────────────────────────────────────────────

@pytest.fixture
def client():
    from puffpai import api_server
    api_server.app.config.update(TESTING=True)
    return api_server.app.test_client(), {
        "Authorization": f"Bearer {api_server._API_KEY}"}


def test_the_card_asks_for_the_town_instead_of_showing_greenwich(
        monkeypatch, sent, client):
    _build(weather=True, town=False, monkeypatch=monkeypatch)
    http, auth = client
    body = http.get("/dashboard/weather", headers=auth).get_json()
    assert body["needs_home"] is True and body["available"] is False
    assert body["weather_on"] is True
    assert "home town" in body["message"]
    assert "geocoding-api.open-meteo.com" in body["town_lookup_sends"]
    assert sent == []


def test_the_card_reads_the_owners_town(monkeypatch, sent, client):
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    http, auth = client
    body = http.get("/dashboard/weather", headers=auth).get_json()
    assert body["location"] == "Columbus, Ohio, United States"
    assert body["high"] == 72 and body["available"] is True
    # No hub: the temperature now comes from the same one request.
    assert body["temp"] == 63 and body["feels_like"] == 61
    assert body["condition"] == "cloudy" and "ha_error" not in body
    assert "now" not in body
    forecast = [c for c in sent if "air-quality" not in c["url"]]
    assert forecast and forecast[0]["params"]["latitude"] == 40.0


def test_chat_answers_the_weather_before_the_model(monkeypatch, sent, client):
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    from puffpai import api_server

    def _no_model(*a, **k):
        raise AssertionError("the model was asked about the weather")
    monkeypatch.setattr(api_server, "_query_ollama_direct", _no_model)
    http, auth = client
    body = http.post("/chat/send", headers=auth,
                     json={"message": "what's the weather tomorrow?"}).get_json()
    assert body["success"] is True
    assert "The weather for tomorrow in Columbus" in body["response"]
    assert len(sent) == 1


# ── the wizard: weather when it fits, Home Assistant only as an "if" ─────────

def _sctx(goal):
    from puffpai.wizard import proposal
    return proposal.SummaryContext(
        goal=goal, terms=proposal.goal_terms(goal), name="Sprout",
        mode="standard", shared={}, previews={},
        args=SimpleNamespace(egress_door=None, skip_ha=False, ha_url=None,
                             ha_token=None, skip_vault=False),
        cfg=None, internet=True)


def test_the_weather_fits_a_garden_bot_and_not_a_music_bot():
    assert weather_service.goal_fits(GARDEN)
    assert weather_service.goal_fits(SPACE)
    assert not weather_service.goal_fits(MUSIC)


def test_the_plan_proposes_the_weather_on_with_what_leaves():
    from puffpai.wizard import proposal
    garden = {i["id"]: i for i in proposal._outside(_sctx(GARDEN))}
    item = garden["outside_services:door:weather"]
    assert item["default_on"] is True
    assert item["label"] == "Weather forecast"
    assert "What leaves your computer" in item["detail"]
    assert "api.open-meteo.com" in item["detail"]
    assert "Home Assistant" not in item["detail"]
    music = {i["id"]: i for i in proposal._outside(_sctx(MUSIC))}
    assert music["outside_services:door:weather"]["default_on"] is False


def test_home_assistant_is_offered_only_for_home_gadgets():
    from puffpai.wizard import proposal
    for goal in (GARDEN, MUSIC, SPACE):
        assert proposal._devices(_sctx(goal)) == [], goal
    lights = proposal._devices(_sctx(LIGHTS))
    assert len(lights) == 1
    # never "If you have Home Assistant" where it is not in use
    assert lights[0]["label"] == "Home Assistant, a smart home hub"
    assert "nothing else needs it" in lights[0]["detail"]


def test_a_live_card_on_the_same_website_is_not_said_twice():
    from puffpai.wizard import proposal
    raw = [{"id": "outside_services:door:weather", "section": "outside_services",
            "outside_host": "api.open-meteo.com", "default_on": True,
            "apply": {"kind": "door", "ref": "weather"}},
           {"id": "live_info:api_seeds:seedopen_meteo", "section": "live_info",
            "outside_host": "api.open-meteo.com", "default_on": True,
            "apply": {"kind": "live_feed", "ref": "seed:open_meteo"}},
           {"id": "live_info:api_seeds:seednasa", "section": "live_info",
            "outside_host": "images-api.nasa.gov", "default_on": True,
            "apply": {"kind": "live_feed", "ref": "seed:nasa"}}]
    kept = [i["id"] for i in proposal._one_line_per_website(raw)]
    assert kept == ["outside_services:door:weather",
                    "live_info:api_seeds:seednasa"]
    raw[0]["default_on"] = False  # weather left off: the card may stay
    assert len(proposal._one_line_per_website(raw)) == 3


def test_the_wizard_says_the_weather_needs_its_service_not_a_hub():
    from puffpai.wizard.packages.tools import skill
    need = {"id": "integration:weather", "label": "Weather card"}
    shut = skill._row_status(need, ha_configured=False, apps=set(),
                             services_on=set())
    assert "Home Assistant" not in shut["door"]
    assert "What leaves your computer" in shut["door"]
    gap = skill._gaps([shut])[0]
    assert "Home Assistant" not in gap["why"]
    assert gap["fix"] == {"kind": "command", "id": "outside.on",
                          "args": {"service": "weather"}}
    from puffpai.wizard import fixes
    assert fixes.render(gap["fix"]) == "puffpai outside on weather"
    on = skill._row_status(need, ha_configured=False, apps=set(),
                           services_on={"weather"})
    assert on["status"] == skill.AVAILABLE


def test_the_summary_yes_counts_as_the_weather_being_on():
    from puffpai.wizard.packages.tools import skill
    ctx = SimpleNamespace(shared={"summary": {"approved": True, "items": [
        {"id": "outside_services:door:weather", "on": True,
         "apply": {"kind": "door", "ref": "weather"}}]}})
    assert "weather" in skill._services_on(ctx)


def test_nothing_here_says_home_assistant_is_needed_for_weather_time_or_updates():
    """Every owner line here, for the three stranger goals."""
    from puffpai.wizard import proposal
    from puffpai.wizard.packages.tools import skill
    lines: list[str] = []
    for goal in (GARDEN, MUSIC, SPACE):
        for item in proposal._outside(_sctx(goal)) + proposal._devices(_sctx(goal)):
            lines += [item["label"], item["detail"]]
    for spec in skill.INTEGRATION_NEEDS.values():
        lines += [spec["door"], spec["without"]]
    for act in ("outside.on", "home.set", "home.set_card"):
        args = {"name": "Weather forecast"} if act == "outside.on" else {}
        lines += list(plain_words.renderings({"id": act, "args": args}).values())
    for line in lines:
        low = line.lower()
        if "home assistant" in low and any(
                w in low for w in ("weather", "time", "update", "forecast")):
            raise AssertionError(line)
        assert plain_words.is_plain(line), plain_words.check(line)


# ── the widget log belongs to the state folder ───────────────────────────────

def test_the_widget_log_lands_under_the_state_dir(monkeypatch, sent):
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    monkeypatch.setattr(dashboard_widgets, "_EGRESS_LOG", None)
    monkeypatch.setattr(dashboard_widgets, "_LEGACY_EGRESS_LOG",
                        os.path.join(get_config().DATA_DIR, "legacy",
                                     "security_logs", "widget_egress.jsonl"))
    dashboard_widgets.fetch_environment(40.0, -83.0)
    state = os.environ["PUFFPAI_STATE_DIR"]
    log = os.path.join(state, "security_logs", "widget_egress.jsonl")
    assert os.path.isfile(log)
    rows = [json.loads(x) for x in open(log, encoding="utf-8") if x.strip()]
    assert {r["door"] for r in rows} == {"weather", "air_quality"}


def test_an_old_log_in_the_install_folder_is_moved_over(monkeypatch, tmp_path,
                                                        sent):
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    legacy = tmp_path / "install" / "security_logs" / "widget_egress.jsonl"
    legacy.parent.mkdir(parents=True)
    legacy.write_text('{"ts": "old", "door": "weather"}\n', encoding="utf-8")
    monkeypatch.setattr(dashboard_widgets, "_EGRESS_LOG", None)
    monkeypatch.setattr(dashboard_widgets, "_LEGACY_EGRESS_LOG", str(legacy))
    dashboard_widgets.fetch_environment(40.0, -83.0)
    assert not legacy.exists() and not legacy.parent.exists()
    log = os.path.join(os.environ["PUFFPAI_STATE_DIR"], "security_logs",
                       "widget_egress.jsonl")
    text = open(log, encoding="utf-8").read()
    assert text.startswith('{"ts": "old"') and text.count("\n") == 3


def _tree(root: str) -> dict[str, tuple[int, int]]:
    """Every file under the checkout, skipping what git and tools own."""
    skip = {".git", "node_modules", ".next", "__pycache__", ".pytest_cache",
            ".proofout", ".mypy_cache", "venv", ".venv"}
    out: dict[str, tuple[int, int]] = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in skip
                   and not os.path.islink(os.path.join(base, d))]
        for name in files:
            path = os.path.join(base, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out[path] = (st.st_size, st.st_mtime_ns)
    return out


def test_a_scratch_run_writes_nothing_outside_the_state_and_data_dirs(
        monkeypatch, sent, client):
    """Leftover files: security_logs/widget_egress.jsonl (and a
    consent ticket's audit line under src/) landed in the install folder."""
    before = _tree(REPO)
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    monkeypatch.setattr(dashboard_widgets, "_EGRESS_LOG", None)
    monkeypatch.setattr(dashboard_widgets, "_REGISTRY_PATH", None)
    http, auth = client
    assert http.get("/dashboard/weather", headers=auth).status_code == 200
    http.post("/chat/send", headers=auth,
              json={"message": "what's the weather tomorrow?"})
    ok, _msg, _w = dashboard_widgets.add_widget("calendar", {})
    assert ok
    from puffpai.security import consent_tickets
    device = consent_tickets.terminal_device_id()
    ticket = consent_tickets.mint("outside_on", "weather", "a sentence", device)
    assert consent_tickets.redeem(ticket, device, "outside_on", "weather")[0]
    after = _tree(REPO)
    changed = sorted(p for p in after if before.get(p) != after[p])
    assert changed == [], changed
    state = os.environ["PUFFPAI_STATE_DIR"]
    assert os.path.isfile(os.path.join(state, "security_logs",
                                       "widget_egress.jsonl"))
    assert os.path.isfile(os.path.join(state, "security_logs", "audit.jsonl"))
    assert os.path.isfile(os.path.join(get_config().DATA_DIR, "data",
                                       "dashboard_widgets.json"))


# ── a live card with the town in its address keeps asking for the town ──────

def test_a_town_card_made_before_the_town_is_set_keeps_the_town_slot(monkeypatch):
    """Her Open-Meteo card was stored with the sample's own place (a town in
    Ohio) and would have shown that town's weather for good."""
    from puffpai import feed_generator, feeds
    cand = {"card_url": "https://api.open-meteo.com/v1/forecast?latitude="
                        "{lat_round}&longitude={lon_round}&current=temperature_2m",
            "sample_endpoint": "https://api.open-meteo.com/v1/forecast?latitude="
                               "40.0&longitude=-83.0&current=temperature_2m"}
    monkeypatch.setattr(feeds, "home_block", lambda: {})
    pattern, params, look = feed_generator.card_address(cand, None)
    assert "{lat_round}" in pattern and "{lon_round}" in pattern
    assert look == cand["sample_endpoint"] and params is None
    monkeypatch.setattr(feeds, "home_block", lambda: {"lat": 39.9, "lon": -82.9})
    _pattern, _params, look = feed_generator.card_address(cand, None)
    assert "latitude=39.9" in look and "longitude=-82.9" in look


def test_the_forecast_is_reused_while_air_quality_stays_off(monkeypatch, sent):
    """The card and the top bar both read it; with air quality off it used to
    be asked again on every load (19 forecasts in one scratch session)."""
    _build(weather=True, town=True, monkeypatch=monkeypatch)
    dashboard_widgets.fetch_environment(40.0, -83.0)
    dashboard_widgets.fetch_environment(40.0, -83.0)
    forecasts = [c for c in sent if "air-quality" not in c["url"]]
    assert len(forecasts) == 1
    # Switching air quality on takes effect at once, not in thirty minutes.
    monkeypatch.setenv("PUFFPAI_EGRESS_AIR_QUALITY", "on")
    egress_doors.reset_cache()
    out = dashboard_widgets.fetch_environment(40.0, -83.0)
    assert out["doors"]["air_quality"]["open"] is True
    assert any("air-quality" in c["url"] for c in sent)


def test_an_app_that_declares_a_hub_never_says_it_needs_home_assistant():
    from puffpai import app_registry
    manifest = {"id": "cam", "name": "Cameras",
                "requires": {"home_assistant": True}}
    for probe in (False, None):
        line = app_registry.degraded_reason(manifest, {"home_assistant": probe})
        assert line.startswith("Cameras works only with a Home Assistant hub, if you have one")
        assert "needs Home Assistant" not in line
        assert "\u2014" not in line
    for text in (list(app_registry.REQUIREMENT_REASONS.values())
                 + list(app_registry.REQUIREMENT_UNVERIFIED.values())):
        assert "needs Home Assistant" not in text
