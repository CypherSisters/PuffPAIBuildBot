"""The build routes and the run after the yes.

GET never starts anything; one POST /build/yes is one consent record and
queues the waiting jobs; make and prove run one job at a time; a job stays
"building: still being built" while the makers are not in this build.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))
sys.path.insert(0, os.path.dirname(__file__))

from test_build_matrix import GOALS, run_goal  # noqa: E402

from puffpai.build import api, pipeline, schema as S, yes as yes_mod  # noqa: E402
from puffpai.security import consent_tickets  # noqa: E402

DEVICE = "device:apitest12345678"


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    consent_tickets.reset()
    from puffpai import deps
    monkeypatch.setattr(deps, "has_program", lambda name: False)
    from puffpai.build import setup as setup_mod
    monkeypatch.setattr(setup_mod, "_home_set", lambda: False)
    api._QUEUE.clear()
    api._running.clear()
    yield tmp_path
    api._QUEUE.clear()
    api._running.clear()
    consent_tickets.reset()


@pytest.fixture
def client(state, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: DEVICE)
    spawned: list = []
    monkeypatch.setattr(api, "_spawn", lambda: spawned.append(True))
    c = app.test_client()
    c.spawned = spawned
    return c


def test_status_before_any_build_starts_nothing(client):
    got = client.get("/build/status").get_json()
    assert got["ok"] and got["jobs"] == [] and got["running"] is False
    assert set(got) >= {"jobs", "setup", "yes", "dropped", "running", "lines", "words"}
    assert got["words"]["route"]["D"] == "Nothing found yet"
    assert client.spawned == [] and not S.exists()


def test_paired_devices_only(state, monkeypatch):
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    monkeypatch.setattr(api, "_device", lambda: "")
    c = app.test_client()
    assert c.get("/build/status").status_code == 403
    assert c.post("/build/yes").status_code == 403


def test_status_shows_the_plan_in_the_contract_shape(client):
    run_goal("WeddingBot")
    got = client.get("/build/status").get_json()
    assert [j["route"] for j in got["jobs"]] == ["A", "C", "B"]
    assert all(set(r) == {"id", "kind", "label", "detail", "job_ids", "done"}
               for r in got["setup"])
    assert got["yes"]["given"] is False
    assert all(set(d) == {"ref", "name", "why"} for d in got["dropped"])
    assert got["jobs"][1]["how"] == "Puts together a map"
    assert client.spawned == []


def _shown_sha(client) -> str:
    return client.get("/build/status").get_json()["yes"]["sha"]


@pytest.fixture
def no_sites(monkeypatch):
    """The yes's site switching, recorded instead of done."""
    seen: list = []
    monkeypatch.setattr(yes_mod, "switch_sites", lambda device: seen.append(device) or [])
    return seen


def test_one_yes_queues_the_waiting_jobs(client, monkeypatch, no_sites):
    run_goal("RecipeBot")
    minted: list = []
    real = consent_tickets.mint
    monkeypatch.setattr(consent_tickets, "mint",
                        lambda *a, **k: minted.append(a[:2]) or real(*a, **k))
    r = client.post("/build/yes", json={"sha": _shown_sha(client)})
    assert r.status_code == 202
    body = r.get_json()
    assert body["started"] is True and len(body["queued"]) == 2
    assert minted == [("add_on", f"build:{S.load_build()['yes']['sha'][:16]}")]
    assert client.spawned == [True] and no_sites == [DEVICE]
    assert yes_mod.covers(S.load_build())
    assert client.get("/build/status").get_json()["yes"]["given"] is True


def test_yes_needs_the_list_she_saw(client, no_sites):
    run_goal("RecipeBot")
    for body in ({}, {"sha": ""}, {"sha": "abc"}, {"sha": "Z" * 64}):
        r = client.post("/build/yes", json=body)
        assert r.status_code == 409, body
    assert not S.load_build()["yes"]["given"] and no_sites == []


def test_yes_refused_for_a_changed_list(client, no_sites):
    run_goal("RecipeBot")
    r = client.post("/build/yes", json={"sha": "0" * 64})
    assert r.status_code == 409 and not S.load_build()["yes"]["given"]
    assert no_sites == []


def test_without_the_makers_a_job_stays_still_being_built(state, monkeypatch):
    # The makers are in this build now: take them away for this test.
    monkeypatch.setattr(pipeline, "_seam", lambda mod, fn: None)
    run_goal("RecipeBot")
    yes_mod.give(DEVICE)
    out = pipeline.run_after_yes()
    assert {o["state"] for o in out} == {S.BUILDING}
    for j in S.load_build()["jobs"]:
        assert j["status"] == S.BUILDING and j["reason"] == S.STILL_BUILDING


def test_make_and_prove_in_order(state, monkeypatch):
    run_goal("RecipeBot")
    yes_mod.give(DEVICE)
    seen: list = []

    def make(job, build):
        seen.append(("make", job["id"]))
        return {"ok": True, "page": f"/dashboard/jobs/{job['id']}", "reason": ""}

    def prove(job, build):
        seen.append(("prove", job["id"]))
        return {"state": "working" if job["route"] == "A" else "not_yet",
                "reason": "" if job["route"] == "A" else "no rows came back"}
    monkeypatch.setattr(pipeline, "_seam",
                        lambda mod, fn: {"make": make, "prove": prove}[fn])
    pipeline.run_after_yes()
    ids = [j["id"] for j in S.load_build()["jobs"]]
    assert seen == [("make", ids[0]), ("prove", ids[0]), ("make", ids[1]), ("prove", ids[1])]
    by = {j["route"]: j for j in S.load_build()["jobs"]}
    assert by["A"]["status"] == S.WORKING and by["C"]["status"] == S.NOT_YET
    assert by["C"]["reason"] == "no rows came back"


def test_run_job_needs_her_yes(state):
    run_goal("RecipeBot")
    jid = S.load_build()["jobs"][0]["id"]
    assert pipeline.run_job(jid)["state"] == S.WAITING_YES


def test_check_again_queues_one_job(client, no_sites):
    run_goal("RecipeBot")
    b = S.load_build()
    jid = next(j["id"] for j in b["jobs"] if j["status"] == S.WAITING_YES)
    r = client.post(f"/build/check/{jid}")
    assert r.status_code == 409 and "yes" in r.get_json()["message"].lower(),         "Check again never claims to check a job her yes does not cover"
    assert api.queued() == []
    api._running.set()               # nothing runs here: only the queue is looked at
    try:
        assert client.post("/build/yes", json={"sha": _shown_sha(client)}).status_code == 202
        api._QUEUE.clear()
        r = client.post(f"/build/check/{jid}")
        assert r.status_code == 202 and api.queued() == [jid]
        assert S.find_job(S.load_build(), jid)["status"] == S.BUILDING, "progress shows at once"
    finally:
        api._QUEUE.clear()
        api._running.clear()
    assert client.post("/build/check/nope").status_code == 404


def test_setup_row_for_the_town_then_the_job_is_built(client, monkeypatch, no_sites):
    run_goal("WeddingBot")
    assert client.post("/build/yes", json={"sha": _shown_sha(client)}).status_code == 202
    venues = next(j for j in S.load_build()["jobs"] if j["route"] == "C")
    assert venues["status"] == S.NEEDS_SETUP
    from puffpai import fix_api
    calls: list = []
    monkeypatch.setattr(fix_api, "apply_setting",
                        lambda data, device: calls.append((data, device)) or
                        ({"ok": True, "line": "Saved your town."}, 200))
    api._QUEUE.clear()
    api._running.clear()
    r = client.post("/build/setup/setting-home-town", json={"town": "Springfield"})
    assert r.status_code == 200, r.get_json()
    assert calls[0][0]["value"] == "Springfield" and calls[0][1] == DEVICE
    b = S.load_build()
    assert next(x for x in b["setup"] if x["id"] == "setting-home-town")["done"]
    # covered by her yes and queued: "Waiting its turn", never "waiting for your yes"
    assert S.find_job(b, venues["id"])["status"] == S.QUEUED
    assert venues["id"] in api.queued()


@pytest.fixture
def seeds(monkeypatch):
    """BuildBot's own website list and the Bridge's switches, recorded."""
    from puffpai import fix_api
    from puffpai.assemble import seed_switch
    calls: list = []
    monkeypatch.setattr(seed_switch, "card", lambda sid: {
        "kind": "switch_on", "service": f"seed-{sid}", "name": sid,
        "label": f"Switch on {sid}", "detail": f"Adds {sid} as a live card."})
    monkeypatch.setattr(seed_switch, "needed_switches", lambda: [])

    def apply_switch(sid, device, reassemble=True):
        calls.append((sid, device, reassemble))
        return {"ok": True, "line": "On."}, 200
    monkeypatch.setattr(fix_api, "apply_switch", apply_switch)
    # A website from our own list: its card's own path, with the sites the
    # list showed for it (f-sec: never wider).
    monkeypatch.setattr(seed_switch, "apply", lambda seed, device, reassemble=True, hosts=None, **_k:
                        apply_switch(seed_switch.PREFIX + seed, device, reassemble))
    return calls


def test_switch_row_goes_the_fix_card_way(client, seeds):
    run_goal("WeddingBot")
    row = next(r for r in S.load_build()["setup"] if r.get("service") == "seed:venues")
    assert client.post(f"/build/setup/{row['id']}").status_code == 200
    # Our own website list's id becomes its fix card's switch, without the
    # old re-assemble run (the build's queue makes the job).
    assert seeds == [("seed-venues", DEVICE, False)]
    assert next(r for r in S.load_build()["setup"] if r["id"] == row["id"])["done"]


def test_the_one_yes_switches_on_exactly_the_listed_sites(client, seeds, monkeypatch):
    run_goal("WeddingBot")
    minted: list = []
    real = consent_tickets.mint
    monkeypatch.setattr(consent_tickets, "mint",
                        lambda *a, **k: minted.append(a[:2]) or real(*a, **k))
    b = S.load_build()
    listed = [r for r in b["setup"] if r["kind"] in ("switch", "address")]
    assert listed, "the WeddingBot list shows outside sites"
    r = client.post("/build/yes", json={"sha": _shown_sha(client)})
    assert r.status_code == 202, r.get_json()
    after = S.load_build()
    sites = after["yes"]["sites"]
    # One record per listed row, nothing else.
    assert sorted(s["row"] for s in sites) == sorted(x["id"] for x in listed)
    switched = {sid for sid, _d, _re in seeds}
    assert switched <= {f"seed-{x['service'].split(':', 1)[1]}" if x["service"].startswith("seed:")
                        else x["service"] for x in listed if x["kind"] == "switch"}
    # Each website got its own ticket for exactly it (plus the one for the list).
    assert ("add_on", "seed-venues") in minted
    assert minted[0][0] == "add_on" and minted[0][1].startswith("build:")
    for s in sites:
        assert s["state"] in (yes_mod.SITE_ON, yes_mod.SITE_WHEN_BUILT, yes_mod.SITE_NOT_ON)
        if s["state"] == yes_mod.SITE_ON:
            assert next(x for x in after["setup"] if x["id"] == s["row"])["done"]
    # The page sees which sites are on; never the device or a ticket.
    shown = client.get("/build/status").get_json()
    assert shown["sites"] and all(set(s) == {"row", "host", "state", "reason"}
                                  for s in shown["sites"])
    assert DEVICE not in str(shown)
    # Rows only she can do (the town) keep their job waiting.
    venues = next(j for j in after["jobs"] if j["route"] == "C")
    assert venues["status"] == S.NEEDS_SETUP


def test_nothing_off_the_list_is_switched_on(client, seeds, monkeypatch):
    """A website whose look switch is not on the list is NOT added: the one
    Yes never turns on anything the list did not show."""
    from puffpai.assemble import seed_switch
    run_goal("WeddingBot")

    def drop(b):
        b["setup"] = [r for r in b["setup"] if r.get("service") != "sample_look"]
    S.update_build(drop)
    monkeypatch.setattr(seed_switch, "needed_switches", lambda: [
        {"id": "sample_look", "words": "looking at services before adding them", "row": {}}])
    client.post("/build/yes", json={"sha": _shown_sha(client)})
    assert seeds == []
    site = next(s for s in S.load_build()["yes"]["sites"] if s["service"] == "seed:venues")
    assert site["state"] == yes_mod.SITE_NOT_ON and "not on your list" in site["reason"]


def test_an_app_added_after_the_yes_gets_its_listed_site_then(state, monkeypatch):
    from puffpai.security import egress_doors as ed
    ed.clear_web_app_doors()
    run_goal("WeddingBot")
    b = S.load_build()
    job = next(j for j in b["jobs"] if j["route"] == "A")
    ref = job["pick"]["ref"]
    row = {"id": "address-rsvp-api", "kind": "address", "label": "Allow RSVP to reach x",
           "detail": "", "job_ids": [job["id"]], "done": False, "host": "api.rsvpdemo.org",
           "app_id": "", "ref": ref}

    def add(b):
        b["setup"].append(row)
    S.update_build(add)
    yes_mod.give(DEVICE)
    sites = yes_mod.switch_sites(DEVICE)
    mine = next(s for s in sites if s["row"] == row["id"])
    assert mine["state"] == yes_mod.SITE_WHEN_BUILT
    assert S.load_build()["yes"]["device"] == DEVICE
    # make added the app: its listed site is switched on with a ticket for
    # the same device; nothing else.
    ed.register_web_app_door("rsvp-abcdefgh", ["api.rsvpdemo.org"])
    on = yes_mod.switch_app_sites(ref, "rsvp-abcdefgh", S.load_build())
    assert on == ["api.rsvpdemo.org"]
    assert ed.app_web_hosts("rsvp-abcdefgh") == {"api.rsvpdemo.org": True}
    assert next(x for x in S.load_build()["setup"] if x["id"] == row["id"])["done"]
    assert yes_mod.switch_app_sites("someone-else", "rsvp-abcdefgh", S.load_build()) == []
    ed.unregister_web_app_door("rsvp-abcdefgh")
    ed.clear_web_app_doors()


def test_status_waits_for_the_start_up_migration(client, monkeypatch):
    """While the server's start-up migration (with the brain) runs, GET
    says "getting ready" and migrates nothing itself."""
    import sys
    import threading
    import types
    done = threading.Event()
    fake = types.SimpleNamespace(BUILD_MIGRATION_DONE=done, _background_services_started=True)
    monkeypatch.setitem(sys.modules, "puffpai.api_server", fake)
    monkeypatch.setattr(api, "READY_WAIT_S", 0.01)
    from puffpai.build import migrate
    calls: list = []
    monkeypatch.setattr(migrate, "ensure", lambda ctx=None: calls.append(ctx) or S.empty_build())
    got = client.get("/build/status").get_json()
    assert got["ok"] and got["ready"] is False and got["jobs"] == [] and calls == []
    assert client.get("/build/job/x").status_code == 503
    assert client.post("/build/setup/x").status_code == 503
    done.set()
    got = client.get("/build/status").get_json()
    assert got["ready"] is True and calls == [{"use_brain": False}]
    assert client.spawned == []


def test_status_names_the_parts_and_book_jobs_answer_from_them(client):
    run_goal("MusicBot")
    got = client.get("/build/status").get_json()
    names = {p["ref"]: p for p in got["parts"]}
    for j in got["jobs"]:
        for ref in j["ingredients"]:
            assert ref in names and names[ref]["name"]
    theory = next(j for j in got["jobs"] if j["name"] == "Learn music theory")
    assert theory["how"] == "Answers from Music Theory for Beginners"
    songs = next(j for j in got["jobs"] if j["name"] == "Make songs")
    assert songs["how"] == "Connects a tool"


def test_job_view(client):
    run_goal("RecipeBot")
    b = S.load_build()
    jid = next(j["id"] for j in b["jobs"] if j["route"] == "C")
    got = client.get(f"/build/job/{jid}").get_json()
    assert got["job"]["page"] == f"job-{jid}"
    names = [p["name"] for p in got["parts"]]
    assert names[0] == "TheMealDB" and "Cookbook" in names
    assert client.get("/build/job/nope").status_code == 404


def test_blueprint_is_registered_with_the_others():
    from puffpai import api_server
    assert "puffpai.build.api" in api_server._ROUND3_BLUEPRINTS  # noqa: SLF001


def test_goals_cover_every_route():
    routes = {r for g in GOALS.values() for r in g["routes"].values()}
    assert routes == set(S.ROUTES)


def test_capabilities_map_reads_the_build(state):
    from puffpai.finding import capabilities
    run_goal("GardenBot")
    rows = capabilities.coverage()
    assert [r["name"] for r in rows] == [j["name"] for j in S.load_build()["jobs"]]
    assert [r["covered"] for r in rows] == [True, True, False]
    block = capabilities.prompt_block()
    assert "Check the weather: Puts together a chart (Open-Meteo)" in block
    assert f"Identify plants from a photo: {capabilities.PROMPT_GAP}" in block


def test_a_site_that_gives_nothing_to_show_leaves_its_job(client, monkeypatch):
    """After the Yes, a website whose one look found nothing to show drops
    out of its job and the list, and is listed under Not used with why."""
    run_goal("RecipeBot")
    b = S.load_build()
    row = next(r for r in b["setup"] if r["kind"] == "switch" and r.get("ref"))
    why = (f"{row['label']}: BuildBot could not find a picture, a number or a list worth "
           f"showing in what it sends. Nothing was added.")
    monkeypatch.setattr(yes_mod, "switch_sites", lambda device: [
        {"row": row["id"], "state": yes_mod.SITE_NOT_ON, "reason": why}])
    api._running.set()
    try:
        assert client.post("/build/yes", json={"sha": _shown_sha(client)}).status_code == 202
    finally:
        api._QUEUE.clear()
        api._running.clear()
    got = client.get("/build/status").get_json()
    assert row["id"] not in {r["id"] for r in got["setup"]}
    assert all(row["ref"] not in [i if isinstance(i, str) else i.get("ref")
                                  for i in j["ingredients"]] for j in got["jobs"])
    gone = next(d for d in got["dropped"] if d["ref"] == row["ref"])
    assert "worth showing" in gone["why"] and "Nothing was added" not in gone["why"]
    assert yes_mod.covers(S.load_build()), "dropping a part never asks for a new yes"


def test_one_jobs_crash_or_hang_never_stops_the_next(client, monkeypatch):
    """Each job runs on its own with a time limit: a crash or a hang ends as
    "Not yet" with a reason, and the jobs after it still run."""
    import threading
    from puffpai.build import pipeline
    run_goal("RecipeBot")
    jobs = [j["id"] for j in S.load_build()["jobs"]]
    ran: list = []
    hold = threading.Event()

    def fake(jid):
        pipeline._set_job(jid, status=S.BUILDING, reason="")  # noqa: SLF001
        ran.append(jid)
        if jid == jobs[0]:
            raise RuntimeError("database is locked")
        if len(jobs) > 2 and jid == jobs[1]:
            hold.wait(5)                  # hangs past the limit
        return {"state": S.WORKING}
    monkeypatch.setattr(pipeline, "run_job", fake)
    monkeypatch.setattr(api, "JOB_LIMIT_S", 0.3)
    for jid in jobs:
        api._run_one(jid)  # noqa: SLF001
    hold.set()
    b = S.load_build()
    assert ran == jobs, "every job got its turn"
    first = S.find_job(b, jobs[0])
    assert first["status"] == S.NOT_YET and "database is locked" in first["reason"]
    if len(jobs) > 2:
        assert S.find_job(b, jobs[1])["status"] == S.NOT_YET
        assert "20 minutes" in S.find_job(b, jobs[1])["reason"]


def test_a_job_left_building_after_a_restart_says_so(client):
    run_goal("RecipeBot")
    jid = S.load_build()["jobs"][0]["id"]
    S.update_build(lambda b: S.find_job(b, jid).update(status=S.BUILDING))
    api._QUEUE.clear()
    api._running.clear()
    got = client.get("/build/status").get_json()
    job = next(j for j in got["jobs"] if j["id"] == jid)
    assert job["status"] == S.NOT_YET and "restarted" in job["reason"]


def test_a_town_that_was_not_looked_up_is_kept_with_why(client, monkeypatch):
    run_goal("WeddingBot")
    from puffpai import fix_api
    S.update_build(lambda b: [r.update(town="Springfield", pending=True)
                              for r in b["setup"] if r["id"] == "setting-home-town"])
    monkeypatch.setattr(fix_api, "apply_setting", lambda data, device: (
        {"ok": False, "message": "The town lookup did not answer."}, 502))
    api.lookup_pending(DEVICE)
    row = next(r for r in client.get("/build/status").get_json()["setup"]
               if r["id"] == "setting-home-town")
    assert row["town"] == "Springfield" and not row["done"]
    assert "Springfield" in row["said"] and "retry" in row["said"]


def test_a_time_zone_this_computer_cannot_read_is_kept(monkeypatch, tmp_path):
    """Windows without tzdata: a geocoded town with "America/Chicago" is
    saved, not refused."""
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    from puffpai import home_location
    import zoneinfo
    monkeypatch.setattr(zoneinfo, "ZoneInfo", lambda name: (_ for _ in ()).throw(
        zoneinfo.ZoneInfoNotFoundError(name)))
    monkeypatch.setattr(zoneinfo, "available_timezones", lambda: set())
    monkeypatch.setattr(home_location, "_write", lambda block: block)
    got = home_location.set_home_coords(39.7, -105.0, label="Springfield",
                                        timezone_name="America/Chicago")
    assert got["timezone"] == "America/Chicago"
    with pytest.raises(home_location.HomeError):
        home_location.set_home_coords(39.7, -105.0, timezone_name="not a zone;rm")


def test_after_the_yes_jobs_not_started_wait_their_turn(client, no_sites):
    run_goal("RecipeBot")
    api._running.set()                 # a job is already being built
    try:
        assert client.post("/build/yes", json={"sha": _shown_sha(client)}).status_code == 202
        got = client.get("/build/status").get_json()
        waiting = [j for j in got["jobs"] if j["route"] != "D"]
        assert waiting and all(j["status"] == S.QUEUED for j in waiting)
        assert got["words"]["status"][S.QUEUED] == "Waiting its turn"
    finally:
        api._QUEUE.clear()
        api._running.clear()


def test_a_slow_service_is_checked_again_by_itself(client, monkeypatch, no_sites):
    from puffpai.build import pipeline
    run_goal("RecipeBot")
    jid = S.load_build()["jobs"][0]["id"]
    planned: list = []
    monkeypatch.setattr(pipeline, "_recheck_scheduler", lambda j, s: planned.append((j, s)))
    reason = pipeline._slow_or(jid, "7Timer! stargazing forecast: it did not answer (ConnectTimeout)")  # noqa: SLF001
    assert reason == ("7Timer! stargazing forecast was slow to answer, so BuildBot tries again "
                      "in 5 minutes.")
    assert planned == [(jid, 300)]
    assert pipeline._slow_or(jid, "it shows only a name, nothing more") == \
        "it shows only a name, nothing more", "a real verdict is not retried"
    for _ in range(3):
        last = pipeline._slow_or(jid, "X: it answered 503")  # noqa: SLF001
    assert "after several tries" in last and len(planned) == 3


def test_build_apps_lists_toolsmiths_apps_and_whether_each_answers(client, monkeypatch):
    from puffpai.build import toolsmith_run
    monkeypatch.setattr(toolsmith_run, "registry", lambda: {
        "news-abc123": {"name": "News", "url": "http://127.0.0.1:5999/", "port": 5999,
                        "folder": "C:/somewhere/news"}})
    monkeypatch.setattr(toolsmith_run, "port_open", lambda port: True)
    got = client.get("/build/apps").get_json()
    assert got == {"ok": True, "apps": [{"id": "news-abc123", "name": "News",
                                         "url": "http://127.0.0.1:5999/", "running": True}]}


def test_a_pasted_github_address_with_github_access_off_offers_the_switch(client, monkeypatch):
    """Not "could not read that project": GitHub access is off, and its
    switch is offered as a card (POST /fix/apply), not refused."""
    run_goal("GardenBot")
    job = S.load_build()["jobs"][0]
    from puffpai.build import apps
    monkeypatch.setattr(apps, "from_url", lambda url: None)       # GitHub access is off
    monkeypatch.setattr(pipeline, "_switched_on", lambda sid: False)
    from puffpai import fix_api
    monkeypatch.setattr(fix_api, "clean", lambda fix: {
        "kind": "switch_on", "service": fix["service"], "name": "Looking for apps and code",
        "label": "Switch on Looking for apps and code now?", "detail": "It sends search words."})
    r = client.post(f"/build/more/{job['id']}", json={"url": "https://github.com/her/plant-id"})
    got = r.get_json()
    assert r.status_code == 409 and got["ok"] is False and got["switch"] == "code_search"
    assert "GitHub access is switched off" in got["message"]
    assert "could not read" not in got["message"] and "—" not in got["message"]
    assert got["fix"]["kind"] == "switch_on" and got["fix"]["service"] == "code_search"
    # Switched on, a project it still cannot read gets the plain line.
    monkeypatch.setattr(pipeline, "_switched_on", lambda sid: True)
    r = client.post(f"/build/more/{job['id']}", json={"url": "https://github.com/her/plant-id"})
    assert r.status_code == 422 and "could not read that project" in r.get_json()["message"]
    # A non-GitHub address is not about the switch.
    monkeypatch.setattr(pipeline, "_switched_on", lambda sid: False)
    r = client.post(f"/build/more/{job['id']}", json={"url": "https://example.org/x"})
    assert r.status_code == 422


def test_a_project_from_the_add_page_gets_a_job_and_waits_for_the_yes(client, monkeypatch):
    """POST /build/project: ``ask`` says where it would go; then ONE new job
    named after it (no job fits), her project first, waiting for her Yes."""
    from test_build_matrix import ISS, _project_ctx
    run_goal("GardenBot")
    body = {"url": ISS["url"], "name": ISS["name"], "what": ISS["what"]}
    got = client.post("/build/project", json={**body, "ask": True}).get_json()
    assert got == {"ok": True, "fits": None,
                   "new_name": "one-small-step: ISS tracker and NASA API web app"}
    real = pipeline.plan_project
    monkeypatch.setattr(pipeline, "plan_project", lambda url, name, what, jid="", ctx=None: real(
        url, name, what, jid, ctx={**_project_ctx(ISS), **(ctx or {})}))
    r = client.post("/build/project", json=body)
    got = r.get_json()
    assert r.status_code == 200 and got["ok"] and got["new"] is True
    jid = got["job"]["id"]
    assert got["page"] == f"/dashboard/jobs/{jid}"
    assert got["line"] == ("Added to job one-small-step: ISS tracker and NASA API web app; "
                           "it starts after your Yes.")
    job = S.find_job(S.load_build(), jid)
    assert job["code"][0]["ref"] == ISS["ref"] and job["status"] == S.WAITING_YES
    assert client.post("/build/project", json={**body, "job": "nope"}).status_code == 404
    # The bot's own page lists every job of build.json, this one too.
    listed = {j["id"]: j for j in client.get("/build/status").get_json()["jobs"]}
    assert set(listed) == {j["id"] for j in S.load_build()["jobs"]} and jid in listed
    assert listed[jid]["status"] == S.WAITING_YES
    assert listed[jid]["code"][0]["name"] == ISS["name"]


def test_check_again_from_the_top_keeps_the_old_reasons(client, no_sites):
    run_goal("RecipeBot")
    api._running.set()               # nothing runs here: only the queue is looked at
    try:
        assert client.post("/build/yes", json={"sha": _shown_sha(client)}).status_code == 202
        api._QUEUE.clear()
        jid = S.load_build()["jobs"][0]["id"]
        old = [{"ref": "github:o/iss", "name": "ISS Tracker", "why": "its page never answered"}]

        def tried(b):
            S.find_job(b, jid)["tried"] = [dict(t) for t in old]
        S.update_build(tried)
        # Try next app keeps its meaning: the tried list stays.
        assert client.post(f"/build/check/{jid}").status_code == 202
        assert S.find_job(S.load_build(), jid)["tried"] == old
        api._QUEUE.clear()
        r = client.post(f"/build/check/{jid}", json={"again": True})
        assert r.status_code == 202
        j = S.find_job(S.load_build(), jid)
        assert j["tried"] == [] and j["tried_before"] == old and api.queued() == [jid]
        # Shown on the page as it was.
        got = client.get(f"/build/job/{jid}").get_json()
        assert (got.get("job") or got)["tried_before"] == old
    finally:
        api._QUEUE.clear()
        api._running.clear()


def test_check_again_needs_her_yes_like_any_run(client, no_sites):
    run_goal("RecipeBot")
    jid = S.load_build()["jobs"][0]["id"]
    r = client.post(f"/build/check/{jid}", json={"again": True})
    assert r.status_code == 409 and api.queued() == []
    assert "tried_before" not in S.find_job(S.load_build(), jid)


# ── her project: Run it first, or Add to the list ────────────────────────────

def _pasted_ctx():
    from test_build_matrix import ScriptedBrain, app, concept_embed
    pasted = app("github:her/plant-id", "Plant ID", "Identify a plant from a photo.")
    return {"brain": ScriptedBrain({}, [pasted]), "embed": concept_embed, "check": None,
            "from_url": lambda url: dict(pasted)}


@pytest.mark.parametrize("order", ["first", "list"])
def test_her_project_run_first_stops_the_jobs_try_add_to_list_does_not(client, monkeypatch,
                                                                       order):
    from puffpai.build import toolsmith_run
    run_goal("GardenBot")
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    other = next(j for j in S.load_build()["jobs"] if j["id"] != plant["id"])
    real = pipeline.plan_more
    monkeypatch.setattr(pipeline, "plan_more", lambda jid, ctx=None: real(
        jid, {**_pasted_ctx(), **(ctx or {})}))
    stopped: list = []
    monkeypatch.setattr(toolsmith_run, "stop_for",
                        lambda jid, why: stopped.append(jid) or jid == plant["id"])
    body = {"url": "https://github.com/her/plant-id"}
    if order == "list":
        body["order"] = "list"
    r = client.post(f"/build/more/{plant['id']}", json=body)
    assert r.status_code == 200
    job = S.find_job(S.load_build(), plant["id"])
    assert job["her_order"] == order
    api._running.set()               # nothing runs here: only the queue is looked at
    try:
        api.enqueue([other["id"], plant["id"]])
        if order == "first":
            assert stopped == [plant["id"]] and "yours runs next" in r.get_json()["line"]
            assert api.queued() == [plant["id"], other["id"]], "hers runs next"
        else:
            assert stopped == [] and "say yes" in r.get_json()["line"]
            assert api.queued() == [other["id"], plant["id"]]
    finally:
        api._QUEUE.clear()
        api._running.clear()
        api._FIRST.clear()


def test_her_project_for_another_job_just_queues(client, monkeypatch):
    from puffpai.build import toolsmith_run
    run_goal("GardenBot")
    plant = next(j for j in S.load_build()["jobs"] if j["route"] == "D")
    real = pipeline.plan_more
    monkeypatch.setattr(pipeline, "plan_more", lambda jid, ctx=None: real(
        jid, {**_pasted_ctx(), **(ctx or {})}))
    # A try runs for a different job: stop_for finds none for this one.
    monkeypatch.setattr(toolsmith_run, "_ACTIVE", {"another-job": object()})
    r = client.post(f"/build/more/{plant['id']}", json={"url": "https://github.com/her/plant-id"})
    assert r.status_code == 200 and "say yes" in r.get_json()["line"]
    assert api._FIRST == set()


# -- she picked Anthropic in the wizard, its key is not saved (2026-10-14) ----------

@pytest.fixture
def wished(monkeypatch):
    """Her wish for Anthropic, Toolsmith still local; no release threads."""
    from puffpai.security import toolsmith_cloud as tc
    monkeypatch.setattr(tc, "_resumers", [])
    tc.record_wish()
    yield tc
    tc.clear_wish()


def test_the_yes_holds_the_build_while_she_wished_anthropic(client, no_sites, wished):
    run_goal("RecipeBot")
    tc = wished
    assert client.get("/build/status").get_json()["toolsmith_hold"] == tc.HOLD_LINE
    r = client.post("/build/yes", json={"sha": _shown_sha(client)})
    body = r.get_json()
    assert r.status_code == 202 and body["held"] is True and body["started"] is False
    assert body["queued"] == [] and body["line"] == tc.HOLD_LINE
    assert client.spawned == [] and api.queued() == []
    jobs = [j for j in S.load_build()["jobs"] if j.get("reason") == tc.HOLD_LINE]
    assert len(jobs) == 2 and all(j["status"] == S.WAITING_YES for j in jobs)


@pytest.mark.parametrize("button", ["use_this_computer", "key_saved"])
def test_either_button_releases_the_held_build(client, no_sites, wished, monkeypatch, button):
    run_goal("RecipeBot")
    tc = wished
    client.post("/build/yes", json={"sha": _shown_sha(client)})
    assert client.spawned == []
    released: list = []
    monkeypatch.setattr(tc, "_resumers", [lambda: released.append(True)])
    if button == "use_this_computer":
        tc.use_local()                              # POST /toolsmith/model {where: local}
    else:
        monkeypatch.setattr(tc, "in_use", lambda: True)   # the key saved: cloud on
        tc._wish_met()                              # noqa: SLF001 - what choose_cloud does
    deadline = time.time() + 5
    while not released and time.time() < deadline:
        time.sleep(0.02)
    assert released == [True] and not tc.held()
    api._resume_vault_waits()                       # noqa: SLF001 - the release callback
    assert client.spawned == [True] and len(api.queued()) == 2
    assert client.get("/build/status").get_json()["toolsmith_hold"] == ""


def test_a_queued_job_never_starts_toolsmith_while_held(state, wished, monkeypatch):
    run_goal("RecipeBot")
    tc = wished
    jid = S.load_build()["jobs"][0]["id"]
    monkeypatch.setattr(api, "_run_one", lambda j: pytest.fail("Toolsmith started"))
    api._QUEUE.append(jid)
    api._running.set()
    api._work()                                     # noqa: SLF001
    assert not api._running.is_set() and api.queued() == []
    assert S.find_job(S.load_build(), jid)["reason"] == tc.HOLD_LINE
