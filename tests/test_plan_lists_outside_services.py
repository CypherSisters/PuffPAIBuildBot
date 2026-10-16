"""The plan and "What leaves your computer" give the same list.

In one wedding-planner build the plan said nothing leaves, then only GitHub;
after the build every page said 12 outside services were on, 11 of them "as
BuildBot comes" and none named on the plan, and 99 searches had gone out
before the plan was shown. The rule: the plan names every outside
service that will be on for this bot, with what the looking already sent,
and anything it does not name is off once the bot is made. The plan, the
"What leaves" page, the sidebar count and the home page agree.

No network: the outside-traffic log lines are written by hand.
"""
from __future__ import annotations

import json
import os
import time
import types

import pytest

from puffpai import front_page, outside_services, plan_outside, zone_bridge
from puffpai.config import get_config
from puffpai.security import (bridge_guard, egress_doors, egress_ledger,
                              outside_switches, privacy_explainer)
from puffpai.wizard import factory, proposal, wording


def _manifest(bridge="fenced", run_egress="enumerated", switches=()):
    cfg = get_config()
    os.makedirs(cfg.CONFIG_DIR, exist_ok=True)
    data = {"posture": {"build_network": "online", "run_egress": run_egress,
                        "egress_switches": {k: True for k in switches}},
            "bridge": {"posture": bridge}}
    with open(os.path.join(cfg.CONFIG_DIR, "build_manifest.json"), "w",
              encoding="utf-8") as fh:
        json.dump(data, fh)
    # A real install has its settings file; without one every built-in
    # service stays shut (fail closed).
    with open(os.path.join(cfg.CONFIG_DIR, "puffpai_config.json"), "w",
              encoding="utf-8") as fh:
        json.dump({}, fh)
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()


@pytest.fixture(autouse=True)
def _forget():
    yield
    bridge_guard.forget_enforced()
    egress_doors.reset_cache()
    outside_switches.forget()


def _looked(n_code=3, n_pictures=2):
    """What the looking step sent, as the real exits log it."""
    for i in range(n_code):
        egress_ledger.record("code_search", host=("api.github.com", "registry.npmjs.org",
                                                  "codeberg.org")[i % 3],
                             outcome="done", what="code_search", bytes_in=100)
    egress_ledger.record("code_search", host="huggingface.co", outcome="refused",
                         code="switched_off", what="code_search")
    for i in range(n_pictures):
        egress_ledger.record("picture_search", host=("commons.wikimedia.org",
                                                     "api.openverse.org")[i % 2],
                             outcome="done", what="picture_search", bytes_in=100)


SHARED = {"needs_look": "online"}

APP = {"id": "apps_code:bunny", "section": "apps_code", "label": "Bunny's Big Day",
       "apply": {"kind": "code_project", "ref": "bunny"},
       "outside_host": "codeload.github.com", "on": True}
WEATHER = {"id": "outside_services:door:weather", "section": "outside_services",
           "label": "Weather forecast", "apply": {"kind": "door", "ref": "weather"},
           "outside_host": "api.open-meteo.com", "on": True}


def _shared():
    return dict(SHARED, needs_look_at=time.time() - 60)


def _on_ids():
    return sorted(r["id"] for r in outside_services.rows() if r["state"] == "on")


# -- the list itself ------------------------------------------------------------

def test_the_looking_tools_that_ran_are_named_with_what_they_sent():
    _manifest()
    _looked()
    shared = _shared()
    sent = {s["id"]: s for s in plan_outside.already_sent(shared)}
    assert sent["code_search"]["sent"] == 3          # the stopped one went nowhere
    assert set(sent["code_search"]["sites"]) == {"GitHub", "npm", "Codeberg"}
    assert set(sent["picture_search"]["sites"]) == {"Wikimedia Commons", "Openverse"}
    kept = [e["id"] for e in plan_outside.stay_on(shared, [], "only_listed")]
    assert kept == ["code_search", "picture_search"]


def test_only_look_on_this_computer_keeps_no_looking_tool():
    _manifest()
    _looked()
    shared = {"needs_look": "here", "needs_look_at": time.time() - 60}
    assert plan_outside.already_sent(shared) == []
    assert plan_outside.stay_on(shared, [], "only_listed") == []


def test_a_look_before_this_run_is_not_counted():
    _manifest()
    _looked()
    shared = {"needs_look": "online", "needs_look_at": time.time() + 3600}
    assert plan_outside.already_sent(shared) == []


def test_keeping_the_bot_off_the_internet_keeps_nothing_on():
    _manifest()
    _looked()
    assert plan_outside.stay_on(_shared(), [APP, WEATHER], "offline") == []


def test_with_the_bridge_up_nothing_unswitched_is_named():
    _manifest(bridge="drawbridge_up")
    _looked()
    kept = plan_outside.stay_on(_shared(), [APP], "only_listed")
    assert kept == []


# -- the plan, the build, and every count ------------------------------------

def _settle_after_build(items, reach="only_listed", switches=("weather",)):
    # What the build leaves behind: the ticked built-in service is on.
    _manifest(switches=switches)
    return plan_outside.settle(_shared(), items, reach, {"items": []})


def test_after_the_build_what_leaves_is_exactly_the_plans_list():
    _manifest()
    _looked()
    before = _on_ids()
    assert len(before) > 5           # as built: BuildBot's own tools are on
    plan = plan_outside.footer(shared=_shared(), items=[APP, WEATHER],
                               reach="only_listed", name="WeddingBot",
                               mode=wording.STANDARD,
                               hosts_text="GitHub, where the apps come from")
    named = sorted(e["id"] for e in plan["stay_on"])
    assert named == sorted(["code_search", "picture_search", "code_download",
                            "app_parts", "weather"])
    done = _settle_after_build([APP, WEATHER])
    assert done["could_not"] == [] and done["named_but_off"] == []
    after = _on_ids()
    assert after == named
    # Every place that counts them says the same number.
    count = plan["count"]
    assert count == len(after)
    assert len(privacy_explainer.open_services()) == count
    assert front_page._outside_on() == count
    assert f"{count} outside services are switched on" in outside_services.page()["line"]
    # The ones the plan did not name read as plainly switched off.
    for row in outside_services.rows():
        if row["kind"] == "as_built" and row["id"] not in named:
            assert row["state"] == "off" and row["state_line"] == "Switched off."


def test_the_plan_footer_names_the_list_and_says_the_consent_rule():
    _manifest()
    _looked()
    plan = plan_outside.footer(shared=_shared(), items=[APP], reach="only_listed",
                               name="WeddingBot", mode=wording.STANDARD,
                               hosts_text="GitHub, where the apps come from")
    line = plan["line"]
    assert line.startswith("What leaves your computer: only what you say yes to.")
    assert "Looking for apps and code, 3 sent to" in line
    assert "Looking for pictures, 2 sent to" in line
    assert "After WeddingBot is made, these stay on:" in line
    for e in plan["stay_on"]:
        assert e["name"] in line
    assert "nothing" not in line.lower() and "stays off the internet" not in line


def test_a_plan_with_nothing_kept_says_so_with_consent_words():
    _manifest()
    plan = plan_outside.footer(shared={}, items=[], reach="only_listed",
                               name="WeddingBot", mode=wording.SIMPLE)
    assert plan["count"] == 0
    assert "Only what you say yes to leaves your computer." in plan["line"]
    assert "everything outside is off until you switch it on" in plan["line"]
    plan_outside.settle({}, [], "only_listed", {"items": []})
    assert _on_ids() == []


def test_settle_never_switches_anything_on():
    _manifest()
    outside_switches.set_switch("code_search", False, by="owner")
    _looked()
    plan_outside.settle(_shared(), [APP], "only_listed", {"items": []})
    assert outside_switches.get("code_search") is False
    assert "code_search" not in _on_ids()


def test_what_she_switched_on_herself_stays_on_and_is_named():
    _manifest()
    outside_switches.set_switch("library", True, by="owner")
    kept = [e["id"] for e in plan_outside.stay_on({}, [], "only_listed")]
    assert kept == ["library"]
    plan_outside.settle({}, [], "only_listed", {"items": []})
    assert _on_ids() == ["library"]


def test_a_live_card_line_is_mapped_to_the_card_it_made():
    kept = [{"id": plan_outside.CARD_PLACEHOLDER + "live_info:x", "item_id": "live_info:x",
             "name": "Sky", "websites": [], "why": ""}]
    ids = plan_outside._expected_ids(kept, {"items": [
        {"id": "live_info:x", "ok": True, "app_id": "sky-abc"}]})
    assert ids == {"card-sky-abc"}


# -- through the real plan and the real build step --------------------------

def _brain_preview():
    return {"model": "qwen2.5:7b-instruct", "gates": {"may_download_models": True},
            "menu": {"installed_models": ["qwen2.5:7b-instruct"],
                     "options": [{"id": "qwen2.5:7b-instruct", "label": "Qwen2.5 7B",
                                  "disk_gb": 5, "fit": "comfortable",
                                  "installed": True}]}}


@pytest.fixture
def no_catalog_lines(monkeypatch):
    monkeypatch.setattr(factory, "PROVIDERS", tuple(
        (name, lambda sctx: []) for name, _fn in factory.PROVIDERS))


def test_the_drawn_plan_and_the_build_agree_with_what_leaves(no_catalog_lines, monkeypatch):
    _manifest()
    _looked()
    raw = {"id": "apps_code:bunny", "section": "apps_code", "label": "Bunny's Big Day",
           "detail": "", "size_bytes": 514 * 1024, "outside_host": "codeload.github.com",
           "default_on": True, "needs_yes": True,
           "apply": {"kind": "code_project", "ref": "bunny"}}
    proposal.register_provider("test:devsec-r2", lambda sctx: [raw])
    try:
        sctx = proposal.SummaryContext(
            goal="a wedding planner", terms=["wedding"], name="WeddingBot",
            mode=wording.STANDARD, shared=_shared(), previews={"brain": _brain_preview()},
            args=types.SimpleNamespace(), cfg=None, internet=True)
        summary = proposal.build(sctx)
        state = proposal.decide(summary, changes={"on": {}},
                                args=types.SimpleNamespace())
    finally:
        proposal.unregister_provider("test:devsec-r2")
    leaves = summary["lines"]["leaves"]
    named = sorted(e["id"] for e in summary["outside"]["stay_on"])
    assert "code_download" in named and "code_search" in named
    assert "What leaves your computer: only what you say yes to." in leaves
    # The build step: the line is carried out by a fake applier, then the
    # services the plan did not name are switched off.
    monkeypatch.setitem(factory._PLAN_APPLIERS, "code_project",
                        lambda item, ctx: {"status": "added", "line": "ok"})
    ctx = types.SimpleNamespace(shared=dict(_shared(), summary=state,
                                            interview={"goal": "a wedding planner"}),
                                args=types.SimpleNamespace(), cfg=None,
                                remote=True, console=False)
    monkeypatch.setattr(factory, "build_online", lambda ctx: True)
    result = factory.apply_approved(ctx)
    assert result["shared"]["factory"]["outside"]["could_not"] == []
    assert _on_ids() == named
    assert front_page._outside_on() == summary["outside"]["count"]


# -- the Bridge page says what is true ----------------------------------------

def test_the_bridge_page_no_longer_says_every_service_asks_first():
    entry = zone_bridge.posture_by_id("fenced")
    assert "Each outside service asks you once before it is switched on" \
        not in entry["summary"]
    assert "only the ones your plan named stay on" in entry["summary"]
    assert "Every other outside service asks you once" in entry["summary"]
    with open(os.path.join(get_config().REPO_ROOT, "config", "bridge_postures.json"),
              encoding="utf-8") as fh:
        shipped = {p["id"]: p for p in json.load(fh)["postures"]}
    assert shipped["fenced"]["summary"] == entry["summary"]


def test_the_find_steps_shelves_and_her_tool_servers_stay_on_after_the_build(monkeypatch):
    """The shelves the wizard used and the Wedding Seat tool
    she said yes to read "Switched off" after the build, and chat refused them."""
    from puffpai import mcp_client
    from puffpai.finding import find
    _manifest()
    outside_switches.set_switch("shelf_hermes", False, by="owner")   # her own off
    switched = find.switch_on_shelves(by="wizard")
    assert "shelf_clawhub" in switched and "code_search" in switched
    assert "shelf_hermes" not in switched
    monkeypatch.setattr(mcp_client, "outside_rows", lambda: [{
        "id": "mcp_wedding_seat", "kind": "mcp", "name": "Tool server: Wedding Seat",
        "for": "", "what_leaves": "", "websites": ["wedding-seat.com"], "state": "on",
        "state_line": "Switched on.", "can_switch": True, "turned_on_by": "owner",
        "turned_on_at": ""}])
    kept = {e["id"] for e in plan_outside.stay_on({}, [], "only_listed")}
    assert {"shelf_clawhub", "shelf_skills_sh", "shelf_mcp_registry", "code_search",
            "mcp_wedding_seat"} <= kept
    assert "shelf_hermes" not in kept
    done = plan_outside.settle({}, [], "only_listed", {"items": []})
    assert "shelf_clawhub" not in done["switched_off"]
    assert "mcp_wedding_seat" not in done["switched_off"]
    assert outside_switches.is_off("shelf_hermes")


def test_the_book_libraries_stay_on_when_the_plan_used_them():
    """After the build every book library but
    Wikipedia read "off", so Find more offered only Wikipedia books."""
    from puffpai.finding import find
    libraries = {"library", "wikipedia_books", "gutenberg_books", "openstax_books",
                 "wikibooks_books", "archive_books"}
    _manifest()
    on = {r["id"] for r in outside_services.rows() if r["state"] == "on"}
    assert libraries <= on                       # as BuildBot comes
    # No find step and no book line: they are not named, so they go off.
    kept = {e["id"] for e in plan_outside.stay_on({}, [APP], "only_listed")}
    assert not kept & libraries
    # One ticked book line keeps every library on (Find more searches them all).
    book = {"id": "knowledge:gutenberg:30522", "section": "knowledge", "on": True,
            "label": "The etiquette of engagement and marriage",
            "apply": {"kind": "knowledge.add", "ref": "gutenberg:30522"},
            "outside_host": "www.gutenberg.org"}
    kept = {e["id"] for e in plan_outside.stay_on({}, [APP, book], "only_listed")}
    assert libraries <= kept
    # The find step searched them too, with no book ticked.
    find.switch_on_shelves(by="wizard")
    done = plan_outside.settle({}, [APP], "only_listed", {"items": []})
    assert not libraries & set(done["switched_off"])
    assert libraries <= {r["id"] for r in outside_services.rows() if r["state"] == "on"}
