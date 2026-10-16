"""The one yes per assembled app (before this, 6 app servers started with no
yes, and a plain GET /assemble/status started a run).

Until the owner taps Start on an app's card: nothing of it is fetched,
written, run or started, and the status says "Waiting for your yes". Her yes
is a consent ticket minted and spent for her paired device and exactly that
card; a changed card needs a new yes.

Nothing goes online: the Bridge getter, Toolsmith and the app server are
recorders that fail the test when reached.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import brain_tools, fsutil
from puffpai.assemble import PLAN_SCHEMA, api, consent, plan
from puffpai.security import consent_tickets

APP = "see_earth_from_space"
CAP = "cap-see-earth-from-space"
DEVICE = "device:abcdefgh1234"
ROW = {"cap": CAP, "name": "See Earth from space", "does": "Shows Earth.",
       "decision": "assemble", "app_id": APP, "uses": [], "why": "Parts.",
       "parts": [{"id": "seed-epic", "kind": "seed", "name": "NASA EPIC Earth photos",
                  "door": "app:epic:feed", "service": "nasa_epic",
                  "host": "epic.gsfc.nasa.gov",
                  "url": "https://epic.gsfc.nasa.gov/api/natural",
                  "sample_url": "https://epic.gsfc.nasa.gov/api/natural"}]}


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    consent_tickets.reset()
    fsutil.atomic_write_json(plan.plan_path(), {"schema": PLAN_SCHEMA, "made_at": 1,
                                                "capabilities": [dict(ROW)]})
    # The run re-plans; here it keeps the plan above.
    monkeypatch.setattr(plan, "build_plan", lambda *a, **k: plan.load_plan())
    reached: list = []

    def forbidden(name):
        def f(*a, **k):
            reached.append(name)
            raise AssertionError(f"{name} ran before her yes")
        return f
    monkeypatch.setattr(api, "start_assembled", forbidden("start_assembled"))
    monkeypatch.setattr(api, "run_toolsmith", forbidden("run_toolsmith"))
    from puffpai.assemble import shape
    monkeypatch.setattr(shape, "fetch_sample", forbidden("fetch_sample"))
    yield reached
    brain_tools.unregister_provider(api.PROVIDER)
    consent_tickets.reset()


@pytest.fixture
def client(state, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: DEVICE)
    spawned: list = []
    monkeypatch.setattr(api, "_spawn", lambda kw: spawned.append(kw))
    c = app.test_client()
    c.spawned = spawned
    return c


# ── before her yes ───────────────────────────────────────────────────────────

def test_before_her_yes_nothing_is_fetched_written_run_or_started(state):
    data = api.run_all()
    assert state == []
    row = data["capabilities"][0]
    assert row["state"] == "not_yet" and row["reason"] == consent.WAITING
    assert row["fix"] == {"kind": "assemble_yes", "app_id": APP}
    st = api.read_status()
    assert st["state"] == "waiting_yes"
    assert st["line"].startswith("Waiting for your yes: 1 app to start")
    assert not os.path.exists(os.path.join(consent.yes_path()))


def test_a_plain_get_of_the_status_never_starts_anything(client, monkeypatch):
    started: list = []
    monkeypatch.setattr(api, "start_background", lambda **kw: started.append(kw) or True)
    api.mark_waiting()
    got = client.get("/assemble/status").get_json()
    assert started == [] and client.spawned == []
    assert got["state"] == "waiting"
    assert [c["app_id"] for c in got["waiting_yes"]] == [APP]


def test_the_card_says_what_it_uses_and_has_one_start_button(client):
    cards = client.get("/assemble/yes").get_json()["cards"]
    assert len(cards) == 1
    card = cards[0]
    assert card["app_id"] == APP and card["button"] == "Start"
    assert card["sites"] == ["epic.gsfc.nasa.gov"]
    assert card["lines"][0] == "Start See Earth from space?"
    assert "Sites it uses: epic.gsfc.nasa.gov." in card["lines"]
    assert consent.WRITES_CODE in card["lines"]
    for line in card["lines"]:
        for bad in ("—", "door", "beta"):
            assert bad not in line.lower()


def test_outside_code_on_the_card_says_where_it_comes_from_and_its_language(state):
    os.makedirs(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "assemble", APP), exist_ok=True)
    fsutil.atomic_write_json(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "assemble", APP,
                                          "needs.json"), {"packages": ["skyfield"]})
    card = consent.card(APP)
    assert card["packages"] == [{"name": "skyfield",
                                 "origin": "the Python Package Index (pypi.org)",
                                 "language": "Python"}]
    assert any("skyfield (from the Python Package Index (pypi.org), written in Python)" in x
               for x in card["lines"])


def test_every_way_in_is_refused_without_her_yes(state):
    from puffpai.assemble import toolsmith_loop as tl
    from puffpai.assemble.server import gateway, host
    hid = host.host_id(APP)
    with pytest.raises(host.AssembleError) as err:
        host.prepare_start(hid, {"assembled": {"app_id": APP}})
    assert err.value.code == "waiting_yes"
    got = host.call_action(APP, "get_s1", {})
    assert got["ok"] is False and got["code"] == "waiting_yes"
    assert got["fix"]["kind"] == "assemble_yes" and got["fix"]["key"] == APP
    assert tl.run_glue(APP, "pics", {})["line"].startswith("Waiting for your yes")
    assert tl.run_toolsmith(APP, {"id": CAP}, [], None,
                            model=lambda m: "")["reason"] == consent.WAITING
    token = gateway.new_run_token(APP, 30)
    try:
        assert gateway.authorised(APP, "fetch", token) is False
    finally:
        gateway.end_run_token(token)


def test_routes_need_a_paired_device(state, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "")
    c = app.test_client()
    assert c.get("/assemble/yes").status_code == 403
    assert c.post("/assemble/yes", json={"app_id": APP}).status_code == 403
    assert consent.has_yes(APP) is False


# ── her yes ──────────────────────────────────────────────────────────────────

def test_her_start_is_a_ticket_for_this_device_and_this_card_then_it_runs(client, monkeypatch):
    seen: list = []
    real_mint, real_redeem = consent_tickets.mint, consent_tickets.redeem
    monkeypatch.setattr(consent_tickets, "mint", lambda *a, **k: seen.append(("mint", a[:2], a[3]))
                        or real_mint(*a, **k))
    monkeypatch.setattr(consent_tickets, "redeem", lambda *a, **k: seen.append(
        ("redeem", a[2:4])) or real_redeem(*a, **k))
    card = client.get("/assemble/yes").get_json()["cards"][0]
    r = client.post("/assemble/yes", json={"app_id": APP, "sha": card["sha"]})
    assert r.status_code == 202, r.get_json()
    assert r.get_json()["line"] == "Putting See Earth from space together now."
    target = f"assemble:{APP}:{card['sha'][:16]}"
    assert seen == [("mint", ("add_on", target), DEVICE), ("redeem", ("add_on", target))]
    assert consent.has_yes(APP) is True
    assert client.spawned == [{"cap": CAP}]
    with open(consent.yes_path(), encoding="utf-8") as fh:
        assert json.load(fh)[APP]["sha"] == card["sha"]
    assert client.get("/assemble/yes").get_json()["cards"] == []


def test_a_card_that_changed_since_she_saw_it_is_not_said_yes_to(client):
    card = client.get("/assemble/yes").get_json()["cards"][0]
    r = client.post("/assemble/yes", json={"app_id": APP, "sha": "0" * 64})
    assert r.status_code == 409 and consent.has_yes(APP) is False
    assert client.spawned == []
    assert card["said_yes"] is False


def test_new_outside_code_after_her_yes_needs_a_new_yes(client):
    card = client.get("/assemble/yes").get_json()["cards"][0]
    assert client.post("/assemble/yes", json={"app_id": APP, "sha": card["sha"]}).status_code == 202
    assert consent.has_yes(APP)
    fsutil.atomic_write_json(os.path.join(os.environ["PUFFPAI_STATE_DIR"], "assemble", APP,
                                          "needs.json"), {"packages": ["skyfield"]})
    assert consent.has_yes(APP) is False
    from puffpai.assemble.server import host
    with pytest.raises(host.AssembleError):
        host.prepare_start(host.host_id(APP), {"assembled": {"app_id": APP}})


def test_a_yes_while_a_run_is_going_runs_right_after_it(client):
    api._running.set()
    try:
        card = client.get("/assemble/yes").get_json()["cards"][0]
        r = client.post("/assemble/yes", json={"app_id": APP, "sha": card["sha"]})
        assert r.status_code == 202 and r.get_json()["started"] is False
        assert api._QUEUED == [CAP] and client.spawned == []
    finally:
        api._running.clear()
        api._QUEUED.clear()


def test_after_her_yes_the_run_goes_past_the_gate(state, monkeypatch):
    consent.say_yes(APP, DEVICE)
    state.clear()
    data = api.run_all()
    # Past the gate: the sample fetch is the first thing it does (the recorder).
    assert "fetch_sample" in state
    assert data["capabilities"][0]["reason"] != consent.WAITING


# ── the chat's card ──────────────────────────────────────────────────────────

def test_the_chat_shows_the_same_card_and_her_tap_is_the_yes(state, monkeypatch):
    from puffpai import fix_api
    spawned: list = []
    monkeypatch.setattr(api, "_spawn", lambda kw: spawned.append(kw))
    card = fix_api.clean({"kind": "assemble_yes", "app_id": APP})
    assert card["label"] == "Start See Earth from space?" and card["key"] == APP
    assert consent.WRITES_CODE in card["detail"]
    shown = fix_api.public(card)
    assert shown["kind"] == "assemble_yes" and shown["key"] == APP
    assert fix_api.apply({"kind": "assemble_yes", "key": APP}, "")[1] == 403
    got, status = fix_api.apply({"kind": "assemble_yes", "key": APP}, DEVICE)
    assert status == 202 and got["ok"] and consent.has_yes(APP)
    assert spawned == [{"cap": CAP}]
    assert fix_api.clean({"kind": "assemble_yes", "app_id": APP}) is None   # nothing left


def test_the_brain_has_one_tool_that_brings_the_cards_while_apps_wait(state):
    tools = [t for t in api.provide({}) if t.name == "assembled_apps_waiting"]
    assert len(tools) == 1
    got = tools[0].fn({}, None)
    assert got["error"].startswith("Waiting for your yes: See Earth from space.")
    assert got["fix"] == [{"kind": "assemble_yes", "app_id": APP}]
    consent.say_yes(APP, DEVICE)
    assert not [t for t in api.provide({}) if t.name == "assembled_apps_waiting"]
