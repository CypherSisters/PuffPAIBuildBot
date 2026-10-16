"""Home page tiles for what Toolsmith added, and the Finish card.

A tile says ``<name>. <purpose>. <what the bot can do there>`` with the
abilities taken only from checked actions ("Opens inside your bot." when
none), opens ``/dashboard/apps/<id>``, and carries the line a phone or another
screen shows instead. Only ready integrations get a tile. The Finish card
shows when things wait for the locked box. Placement kind ``app``.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import front_page, home_placement, plain_words  # noqa: E402
from puffpai.integrate import records, wording  # noqa: E402


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = sup.setup_chat(monkeypatch, tmp_path)
    rec = records.get(sup.IID)
    rec.update({"purpose": "Wedding planner app.", "mode": "static_web",
                "app_id": "big-day-appappap"})
    records.save(rec)
    return w


def test_a_ready_app_gets_a_tile_from_its_checked_actions(world):
    tiles = front_page.app_tiles()
    assert len(tiles) == 1
    tile = tiles[0]
    assert tile["line"].startswith("Big Day. Wedding planner app. Your bot can ")
    assert "read the guest list" in tile["line"] and tile["line"].endswith(" here.")
    assert tile["href"] == "/dashboard/apps/big-day-appappap"
    assert tile["elsewhere"] == "Open this on the computer BuildBot runs on."
    for text in (tile["line"], tile["elsewhere"]):
        assert plain_words.check(text) == [], text


def test_no_checked_actions_says_it_opens(monkeypatch, tmp_path):
    sup.setup_chat(monkeypatch, tmp_path, acts=[])
    rec = records.blank(records.new_id("Timer"), name="Timer", purpose="A kitchen timer")
    rec.update({"status": "ready", "mode": "static_web", "app_id": "timer-abcdefgh"})
    records.save(rec)
    tile = next(t for t in front_page.app_tiles() if t["name"] == "Timer")
    assert tile["line"] == "Timer. A kitchen timer. Opens inside your bot."


@pytest.mark.parametrize("status", ["held", "refused", "failed", "waiting_box", "building"])
def test_only_ready_things_get_a_tile(world, status):
    rec = records.get(sup.IID)
    rec["status"] = status
    records.save(rec)
    assert front_page.app_tiles() == []


def test_the_mood_board_tile_asks_for_a_folder(monkeypatch, tmp_path):
    sup.setup_chat(monkeypatch, tmp_path, acts=[])
    from puffpai.integrate import palette
    out = palette.apply_palette({"id": "x", "need": ""}, sup.Ctx())
    tile = next(t for t in front_page.app_tiles() if t["id"] == out["integration_id"])
    assert tile["ask_folder"] is True and tile["ask_label"] == "Choose a pictures folder"
    # Its own page.
    assert tile["href"] == f"/dashboard/mood-board/{out['integration_id']}"
    assert tile["themes"] == []


def test_the_front_page_carries_the_tiles_and_no_finish_card(world):
    page = front_page.front()
    assert [t["id"] for t in page["apps"]] == [sup.IID]
    assert page["finish"] is None
    assert plain_words.check(front_page.owner_text(page)) == []


def test_the_finish_card_counts_what_waits(world, monkeypatch):
    driver = types.ModuleType("puffpai.box.driver")
    driver.waiting = lambda: [{"integration": "a"}, {"integration": "b"}]
    monkeypatch.setitem(sys.modules, "puffpai.box.driver", driver)
    card = front_page.finish_card()
    assert card == {"line": "Finish adding 2 things that were waiting for the locked box",
                    "label": "Finish", "post": "/box/finish"}


def test_the_app_placement_kind(world):
    assert home_placement.PLACE_AS["integration"] == "app"
    assert home_placement.for_undo({"kind": "integration", "ref": sup.IID}) == ("app", sup.IID)
    assert home_placement.place("app", sup.IID, "Big Day")
    rows = home_placement.front_rows([])
    assert rows == [{"kind": "app", "id": sup.IID, "name": "Big Day",
                     "href": "/dashboard/apps/big-day-appappap"}]
    rec = records.get(sup.IID)
    rec["status"] = "held"
    records.save(rec)
    assert home_placement.front_rows([]) == []


def test_ability_rows_come_from_checked_actions(world):
    from puffpai.integrate import abilities
    rows = abilities.ability_rows()
    row = next(r for r in rows if r["id"] == f"app:{sup.IID}")
    assert row["status"] == "available"
    assert row["label"].startswith("Use Big Day: read the guest list")
    assert wording.ABILITY_USE.split(":")[0].startswith("Use")


# -- the way to the Home Assistant voice key page -----

def test_the_voice_key_page_is_linked_when_home_assistant_is_set_up(world, monkeypatch):
    from puffpai import setup_ops
    from puffpai.integrate import voice_key
    voice_key.forget_cache()
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: False)
    assert front_page.voice_key_link() is None          # no Home Assistant, no mention of it
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: True)
    link = front_page.voice_key_link()
    assert link == {"label": wording.VOICE_KEY_LINK_LABEL, "line": wording.VOICE_KEY_LINK_OFF,
                    "href": "/dashboard/keys/voice", "on": False}
    assert front_page.front()["voice_key"] == link


def test_the_voice_key_link_stays_while_a_key_is_on_and_never_holds_it(world, monkeypatch):
    from puffpai import setup_ops
    from puffpai.integrate import voice_key
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: False)
    key = voice_key.new()
    try:
        link = front_page.voice_key_link()
        assert link["line"] == wording.VOICE_KEY_LINK_ON and link["on"] is True
        assert key not in repr(front_page.front())
    finally:
        voice_key.off()
    assert front_page.voice_key_link() is None


def test_no_voice_key_link_without_app_actions(monkeypatch, tmp_path):
    from puffpai import setup_ops
    from puffpai.integrate import actions
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(actions, "_SOURCES", {})
    monkeypatch.setattr(setup_ops, "ha_configured", lambda: True)
    assert front_page.voice_key_link() is None
