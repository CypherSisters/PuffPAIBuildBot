"""Your first apps: the five shipped apps start off, and turn on per bot.

A new bot has none on; a bot made before the change keeps the ones it uses
(saved data, or its page on the build); the Apps page turns them on and off
(paired device only); the build plan lists the ones that fit the goal on its
one Yes; an app that is off is not offered to chat, the fast paths or the
router, and its data routes say "turned off".

Copyright (c) Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))
sys.path.insert(0, os.path.dirname(__file__))

from puffpai import api_server, app_data as app_data_mod, app_registry, first_apps  # noqa: E402

FIVE = {"alarmflow", "calendar", "fitfam", "freshtable", "trivia"}
AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}
PAIRED = {**AUTH, "X-Puff-Device-Id": "deviceaaaa000001"}


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """A new bot: a state folder with no on-set, no saved app data, no build."""
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "no_checkout_data"))
    from puffpai import build_manifest
    monkeypatch.setattr(build_manifest, "read_manifest", lambda path=None: None)
    # Drop the cache without reading: the first read is the test's own.
    monkeypatch.setattr(app_registry, "_cached_manifests", None)
    yield state
    app_registry.rescan()


def _on_file(state) -> list:
    with open(os.path.join(str(state), first_apps.STATE_NAME), encoding="utf-8") as fh:
        return json.load(fh)["on"]


def _ids(manifests) -> set:
    return {str(m["id"]) for m in manifests}


# -- off by default ------------------------------------------------------------

def test_the_five_are_marked_first_apps_on_disk():
    on_disk = app_registry.load_manifests(include_off=True)
    assert {str(m["id"]) for m in on_disk if app_registry.is_first_app(m)} == FIVE


def test_a_new_bot_has_none_of_them_on(fresh):
    assert not (FIVE & _ids(app_registry.load_manifests()))
    assert FIVE <= _ids(app_registry.load_manifests(include_off=True))
    assert not (fresh / first_apps.STATE_NAME).exists(), "reading writes nothing"
    first_apps.settle()
    assert _on_file(fresh) == []
    assert not (FIVE & {r["id"] for r in app_registry.ui_records()})
    assert all(not a["on"] for a in first_apps.listing())


def test_an_owner_app_is_never_off(fresh):
    folder = fresh / "apps" / "mylists"
    folder.mkdir(parents=True)
    manifest = json.load(open(os.path.join(app_registry._apps_dir(), "trivia",
                                           "app.json"), encoding="utf-8"))
    manifest.update(id="mylists", name="My lists", first_app=True,
                    ui={"route": "/dashboard/apps/mylists"})
    (folder / "app.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert "mylists" in _ids(app_registry.load_manifests())


def test_first_app_must_be_true_or_false():
    manifest = json.load(open(os.path.join(app_registry._apps_dir(), "trivia", "app.json"),
                              encoding="utf-8"))
    manifest["first_app"] = "yes"
    assert "true or false" in (app_registry.validate_manifest(manifest) or "")


# -- bots made before the change ---------------------------------------------

def test_an_existing_bot_with_saved_data_keeps_that_app(fresh):
    data = fresh / "app_data"
    data.mkdir()
    (data / "fitfam.json").write_text('{"users": {}}', encoding="utf-8")
    assert "fitfam" in _ids(app_registry.load_manifests())
    first_apps.settle()
    assert _on_file(fresh) == ["fitfam"]
    # Kept once: the rule never runs again for this bot.
    (data / "calendar.json").write_text('{"events": []}', encoding="utf-8")
    assert "calendar" not in _ids(app_registry.load_manifests())


def test_an_existing_bot_whose_build_shows_the_app_keeps_it(fresh, monkeypatch):
    from puffpai import build_manifest
    monkeypatch.setattr(build_manifest, "read_manifest", lambda path=None: {
        "surfaces": {"allowlist": ["daily.kitchen", "comms.timers"]}})
    assert _ids(app_registry.load_manifests()) & FIVE == {"freshtable", "alarmflow"}
    first_apps.settle()
    assert sorted(_on_file(fresh)) == ["alarmflow", "freshtable"]


def test_an_existing_bot_with_the_app_on_its_home_page_keeps_it(fresh):
    (fresh / "things").mkdir()
    (fresh / "things" / "home.json").write_text('{"builtin-trivia": true}', encoding="utf-8")
    assert _ids(app_registry.load_manifests()) & FIVE == {"trivia"}


def test_the_set_is_settled_before_the_build_is_written(fresh, monkeypatch, tmp_path):
    """A new bot's own plan (its page list) is never read as old use."""
    from puffpai import build_manifest
    monkeypatch.setattr(build_manifest, "manifest_path",
                        lambda: str(tmp_path / "config" / "build_manifest.json"))
    build_manifest.write_manifest({"surfaces": {"allowlist": ["daily.fitness"]}})
    assert _on_file(fresh) == []
    monkeypatch.setattr(build_manifest, "read_manifest", lambda path=None: {
        "surfaces": {"allowlist": ["daily.fitness"]}})
    assert "fitfam" not in _ids(app_registry.load_manifests())


# -- turning them on and off -------------------------------------------------

def test_the_apps_page_lists_and_turns_them_on_and_off(fresh):
    client = api_server.app.test_client()
    assert client.get("/apps/first", headers=AUTH).status_code == 403
    got = client.get("/apps/first", headers=PAIRED).get_json()
    assert got["ok"] and {a["id"] for a in got["apps"]} == FIVE
    fit = next(a for a in got["apps"] if a["id"] == "fitfam")
    assert fit["name"] == "FitFam" and fit["blurb"] and fit["on"] is False

    r = client.post("/apps/first/turn", json={"id": "fitfam", "on": True}, headers=PAIRED)
    assert r.status_code == 200 and r.get_json()["app"]["on"] is True
    assert "fitfam" in _ids(app_registry.current_manifests())
    assert "fitfam" in {a["id"] for a in client.get("/build/self", headers=AUTH)
                        .get_json().get("apps") or []}

    r = client.post("/apps/first/turn", json={"id": "fitfam", "on": False}, headers=PAIRED)
    assert r.status_code == 200 and "saved data stays" in r.get_json()["line"]
    assert "fitfam" not in _ids(app_registry.current_manifests())


def test_turning_on_needs_a_paired_device_and_a_first_app(fresh):
    client = api_server.app.test_client()
    assert client.post("/apps/first/turn", json={"id": "fitfam", "on": True},
                       headers=AUTH).status_code == 403
    assert client.post("/apps/first/turn", json={"id": "nosuch", "on": True},
                       headers=PAIRED).status_code == 404
    assert client.post("/apps/first/turn", json={"id": "fitfam"},
                       headers=PAIRED).status_code == 400
    assert "fitfam" not in _ids(app_registry.load_manifests())


def test_an_off_app_answers_turned_off_and_keeps_its_data(fresh):
    client = api_server.app.test_client()
    for method, path in (("get", "/apps/fitfam/data"), ("get", "/apps/fitfam/updated"),
                         ("put", "/apps/fitfam/data"), ("post", "/apps/fitfam/action")):
        r = getattr(client, method)(path, json={"action": "fit_get_status", "params": {}},
                                    headers=AUTH)
        assert r.status_code == 409, path
        assert r.get_json()["turned_off"] is True
        assert "FitFam is turned off" in r.get_json()["error"]
    first_apps.set_on(["fitfam"], True)
    assert client.get("/apps/fitfam/data", headers=AUTH).status_code == 200


# -- not offered while off -----------------------------------------------------

def test_an_off_app_is_not_offered_to_chat_or_the_router(fresh):
    from puffpai import home_things, rulepacks
    assert not (FIVE & {a["id"] for a in home_things.builtin_apps()})
    apps_pack = next(p for p in rulepacks.load_packs() if p.get("surface") == "apps")
    assert "workout" not in apps_pack["keywords"]

    first_apps.set_on(["fitfam"], True)
    assert "fitfam" in {a["id"] for a in home_things.builtin_apps()}
    apps_pack = next(p for p in rulepacks.load_packs() if p.get("surface") == "apps")
    assert "workout" in apps_pack["keywords"]


def test_an_off_apps_words_are_not_answered_by_the_list_fast_path(fresh):
    """A persona's lists are answered here; FreshTable's sentence is not, on
    or off, and an off app never gets a fast path of its own."""
    from puffpai import app_fastpath, persona_scaffold
    persona_scaffold.scaffold("gardenbot", name="GardenBot", lists="plantings,harvest log")
    app_registry.rescan()
    pairs = app_fastpath._candidates(app_registry.current_manifests())
    assert {str(m["id"]) for m, _ in pairs} == {"gardenbot"}
    store = app_data_mod.AppData()
    assert app_fastpath.handle("add milk to the shopping list", store) is None


# -- the wizard suggests them --------------------------------------------------

def test_a_fitness_goal_suggests_fitfam(fresh):
    got = first_apps.suggest("a bot to track my workouts and fitness")
    assert [g["id"] for g in got] == ["fitfam"]
    assert got[0]["line"] == "Turn on: FitFam (one of your first apps)"


def test_a_recipe_goal_suggests_freshtable(fresh):
    got = first_apps.suggest("a recipe bot for meals and my pantry")
    assert "freshtable" in [g["id"] for g in got]
    assert "fitfam" not in [g["id"] for g in got]


def test_a_goal_that_fits_none_lists_nothing(fresh):
    assert first_apps.suggest("a bot that shows me pictures of the earth") == []


def test_an_app_already_on_is_not_suggested(fresh):
    first_apps.set_on(["fitfam"], True)
    assert first_apps.suggest("a bot to track my workouts and fitness") == []


@pytest.fixture
def build_client(fresh, monkeypatch):
    from flask import Flask

    from puffpai.build import api, setup as setup_mod, yes as yes_mod
    from puffpai.security import consent_tickets
    consent_tickets.reset()
    from puffpai import deps
    monkeypatch.setattr(deps, "has_program", lambda name: False)
    monkeypatch.setattr(setup_mod, "_home_set", lambda: False)
    monkeypatch.setattr(yes_mod, "switch_sites", lambda device: [])
    api._QUEUE.clear()
    api._running.clear()
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "device:firstapps12345678")
    monkeypatch.setattr(api, "_spawn", lambda: None)
    yield app.test_client()
    api._QUEUE.clear()
    api._running.clear()
    consent_tickets.reset()


def test_the_plans_yes_lists_and_turns_on_the_app(build_client):
    from test_build_matrix import run_goal

    from puffpai.build import schema as S, yes as yes_mod
    run_goal("RecipeBot")
    status = build_client.get("/build/status").get_json()
    assert status["first_apps"] == [{"id": "freshtable", "name": "FreshTable",
                                     "line": "Turn on: FreshTable (one of your first apps)"}]
    assert "Turn on: FreshTable (one of your first apps)" in yes_mod.sentence(S.load_build())
    assert "freshtable" not in _ids(app_registry.load_manifests())

    r = build_client.post("/build/yes", json={"sha": status["yes"]["sha"]})
    assert r.status_code == 202
    assert "Turned on: FreshTable." in r.get_json()["line"]
    assert "freshtable" in _ids(app_registry.load_manifests())
    assert yes_mod.covers(S.load_build())


def test_a_plan_with_no_fitting_app_lists_none(build_client):
    from test_build_matrix import run_goal
    run_goal("StargazerBot")
    assert build_client.get("/build/status").get_json()["first_apps"] == []


def test_the_first_apps_are_part_of_what_the_yes_covers():
    from puffpai.build import yes as yes_mod
    build = {"jobs": [], "setup": [], "first_apps": []}
    before = yes_mod.sha(build)
    build["first_apps"] = [{"id": "fitfam", "name": "FitFam",
                            "line": "Turn on: FitFam (one of your first apps)"}]
    assert yes_mod.sha(build) != before


# -- widgets and a failed save -------------------------------------------------

def test_the_calendar_and_alarm_widgets_follow_the_switch(fresh, monkeypatch):
    from puffpai import dashboard_widgets as dw
    saved = [{"id": "w_1", "type": "calendar", "props": {"title": "Upcoming"}},
             {"id": "w_2", "type": "alarmflow", "props": {}},
             {"id": "w_3", "type": "news", "props": {"url": "https://x.example/rss"}}]
    monkeypatch.setattr(dw, "_load", lambda: [dict(w) for w in saved])
    monkeypatch.setattr(dw, "_save", lambda items: saved.__init__(items))
    assert [w["id"] for w in dw.list_widgets()] == ["w_3"]
    ok, line, _ = dw.add_widget("calendar", {})
    assert not ok and "Calendar is turned off" in line
    first_apps.set_on(["calendar"], True)
    assert [w["id"] for w in dw.list_widgets()] == ["w_1", "w_3"]
    first_apps.set_on(["calendar"], False)
    assert [w["id"] for w in dw.list_widgets()] == ["w_3"]
    assert len(saved) == 3, "turning the app off keeps the widget for later"


def test_a_turn_that_cannot_be_saved_says_so(fresh, monkeypatch):
    from puffpai import fsutil

    def refuse(*_a, **_k):
        raise PermissionError("read-only")
    monkeypatch.setattr(fsutil, "atomic_write_json", refuse)
    client = api_server.app.test_client()
    r = client.post("/apps/first/turn", json={"id": "fitfam", "on": True}, headers=PAIRED)
    assert r.status_code == 503 and r.get_json()["ok"] is False
    assert "nothing changed" in r.get_json()["message"]
    assert "fitfam" not in _ids(app_registry.load_manifests())
