"""An assembled app's fixes are the chat's cards.

puffpai.assemble says what would fix a part: ``{"kind":
"switch_on", "door", "service"}`` or ``{"kind": "setting", "app_id", "key"}``.
puffpai.fix_api owns the card she sees and POST /fix/apply. Here:
the assembled fixes become fix_api's cards (in chat and on the app's own page), an app's
home town is the bot's one home town, and the app's page can only ask its
dashboard page for a card for a switch its own sources use.
No test goes online.

Copyright (c) 2026 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import pytest

from puffpai import brain_tools, fix_api, home_location, outside_services
from puffpai.assemble import api, spec as spec_mod
from puffpai.assemble.server import host

APP = "see_earth"
FEED = "app:nasa_epic:feed"
ROW = {"id": "card-nasa_epic", "name": "NASA EPIC", "state": "off", "can_switch": True}
SENTENCE = ("Switch on NASA EPIC. What leaves your computer: the date. "
            "It goes to epic.gsfc.nasa.gov.")


def good_spec() -> dict:
    return {"schema": "assembled_app/v1", "id": APP, "title": "Earth from space",
            "caps": ["cap-see-earth"],
            "settings": [{"key": "home_town", "kind": "place", "use": "home_location"},
                         {"key": "size", "kind": "text"}],
            "sources": [{"id": "pics", "kind": "http_json", "service": "nasa_epic",
                         "door": FEED, "url": "https://epic.gsfc.nasa.gov/api/natural",
                         "items": "$[*]", "fields": {"title": "caption"},
                         "args": {"lat": "setting:home_town.lat"}}],
            "views": [{"type": "gallery", "source": "pics"},
                      {"type": "settings", "source": ""}],
            "actions": [{"id": "get_pics", "source": "pics", "input": {}}],
            "checks": [{"cap": "cap-see-earth", "ask": "Show me Earth today",
                        "action": "get_pics", "input": {},
                        "expect": {"min_items": 1, "fields": ["title"]}}]}


@pytest.fixture
def app(monkeypatch):
    assert spec_mod.validate(good_spec()) == []
    api.save_spec(good_spec())
    rows = {ROW["id"]: dict(ROW), "weather": {"id": "weather", "name": "Weather forecast",
                                              "state": "off", "can_switch": True}}
    monkeypatch.setattr(outside_services, "row", lambda sid: rows.get(sid))
    monkeypatch.setattr(outside_services, "proposal", lambda sid: SENTENCE)
    yield rows
    brain_tools.unregister_provider(api.PROVIDER)


# -- one card shape ---------------------------------------------------------------

def test_a_parts_switch_becomes_the_outside_lists_card(app):
    card = fix_api.clean({"kind": "switch_on", "door": FEED, "service": "nasa_epic"})
    assert card["kind"] == "switch_on" and card["service"] == "card-nasa_epic"
    assert card["label"] == "Switch on NASA EPIC now?" and card["detail"] == SENTENCE
    # A built-in switch is its own row; an unknown one gives no card.
    assert fix_api.clean({"kind": "switch_on", "door": "weather"})["service"] == "weather"
    assert fix_api.clean({"kind": "switch_on", "door": "app:other:feed"}) is None
    assert fix_api.clean({"kind": "switch_on", "door": "x\ny"}) is None
    # The card is the same whichever way it is read again.
    assert fix_api.clean(fix_api.public(card)) == card


def test_an_apps_home_town_setting_is_the_one_town_card(app):
    card = fix_api.clean({"kind": "setting", "app_id": APP, "key": "home_town"})
    assert card is not None and card["key"] == "home" and card["input"] == "place"
    assert fix_api.clean({"kind": "setting", "app_id": APP, "key": "size"}) is None
    assert fix_api.clean({"kind": "setting", "app_id": "../x", "key": "home_town"}) is None


def test_the_brain_tool_carries_the_fix_to_a_chat_card(app, monkeypatch):
    monkeypatch.setattr(api, "call_action", lambda a, b, c=None: {
        "ok": False, "rows": [], "reason": "the Bridge switch for nasa_epic is off",
        "fix": {"kind": "switch_on", "door": FEED, "service": "nasa_epic"}})
    tool = api.app_tools(APP, good_spec())[0]
    got = {"ok": True, "result": tool.fn({}, {}), "asked": False}
    cards = fix_api.fixes_for(got)
    assert [c["service"] for c in cards] == ["card-nasa_epic"]
    assert "fix" not in got["result"], "the switch id never reaches the model"
    assert brain_tools.result_text(got["result"]).count(FEED) == 0


def test_a_missing_home_town_in_an_app_offers_the_town_card(app):
    got = api.fetch_source(APP, "pics", {})
    assert got["fix"] == {"kind": "setting", "app_id": APP, "key": "home_town"}
    assert fix_api.clean(got["fix"])["key"] == "home"


# -- one home town ----------------------------------------------------------------

def test_saving_home_town_in_the_app_saves_the_bots_home_town(app):
    assert home_location.get_home() is None
    got = host.save_setting(APP, "home_town", {"name": "Hove", "lat": 50.83, "lon": -0.17})
    assert got["ok"] is True
    home = home_location.get_home()
    assert home["label"] == "Hove" and home["latitude"] == pytest.approx(50.8, abs=0.1)
    assert "home_town" not in spec_mod.setting_values(APP), "never a second copy"
    st = good_spec()["settings"][0]
    assert spec_mod.resolve_place(APP, st)["name"] == "Hove"


def test_the_bots_home_town_wins_over_an_old_app_copy(app):
    spec_mod.save_setting(APP, "home_town", {"lat": 10, "lon": 10, "name": "Old"})
    st = good_spec()["settings"][0]
    assert spec_mod.resolve_place(APP, st) is None
    home_location.set_home_coords(51.5, -0.1, label="London")
    assert spec_mod.resolve_place(APP, st)["name"] == "London"


def test_a_town_name_is_not_looked_up_while_the_lookup_is_off(app, monkeypatch):
    def never(*a, **kw):
        raise AssertionError("no outside call from an app's page")
    from puffpai.security import egress_doors
    monkeypatch.setattr(egress_doors, "egress_get", never)
    got = host.save_setting(APP, "home_town", {"name": "Hove"})
    assert "latitude and longitude" in got["error"] and home_location.get_home() is None


# -- the app's page asks its dashboard page -----------------------------------------

def test_a_card_for_the_apps_page_needs_a_paired_device_and_its_own_switch(app):
    ask = {"app_id": APP, "kind": "switch_on", "service": "card-nasa_epic"}
    assert fix_api.card_for_app(ask, "")[1] == 403
    got, status = fix_api.card_for_app(ask, "device:abc")
    assert status == 200 and got["fix"]["label"] == "Switch on NASA EPIC now?"
    assert got["fix"]["detail"] == SENTENCE
    # A switch this app's sources do not use: no card, whatever the page says.
    other, status = fix_api.card_for_app({**ask, "service": "weather"}, "device:abc")
    assert status == 404 and "fix" not in other
    assert fix_api.card_for_app({**ask, "app_id": "nope"}, "device:abc")[1] == 404
    # A key card takes a name, never a link from the page.
    key, status = fix_api.card_for_app({"app_id": APP, "kind": "key", "name": "NASA",
                                        "get_url": "https://evil.example/"}, "device:abc")
    assert status == 200 and key["fix"]["get_url"] == ""
    assert fix_api.card_for_app({"app_id": APP, "kind": "setting"}, "device:abc")[1] == 404


def test_the_route_is_registered_and_refuses_an_unpaired_caller():
    from puffpai import api_server
    rules = {r.rule for r in api_server.app.url_map.iter_rules()}
    assert {"/fix/apply", "/fix/card"} <= rules
    client = api_server.app.test_client()
    got = client.post("/fix/card", json={"app_id": APP, "kind": "switch_on"},
                      headers={"Authorization": f"Bearer {api_server._API_KEY}"})
    assert got.status_code == 403
