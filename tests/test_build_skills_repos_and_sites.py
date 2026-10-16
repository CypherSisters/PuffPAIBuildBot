"""The seams between the build foundation's parts.

* a checked code project keeps its prepared card, so route A adds a fresh
  repo after the one Yes;
* companion skills: the setup list offers exactly the sibling skills the
  installer accepts (one publisher rule), and make installs only those;
  a found skill's own key waits for its install instead of blocking the job;
* added apps get their places on the Bridge back at start;
* a CLI skill is proven with the job's own sample ask (nothing runs);
* a skill whose only action is one keyed request to its own service is a
  DATA source in classify, setup and make, never a command line tool.

Nothing goes online: the brain, the installer and the Bridge's wire are
replaced or never reached.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))
sys.path.insert(0, os.path.dirname(__file__))

from test_build_matrix import run_goal  # noqa: E402

from puffpai import skills  # noqa: E402
from puffpai.build import classify, make as make_mod, prove as prove_mod  # noqa: E402
from puffpai.build import schema as S, setup as setup_mod, yes as yes_mod  # noqa: E402
from puffpai.security import consent_tickets, egress_doors as ed  # noqa: E402

DEVICE = "device:seams12345678"

KEY_REQUEST = """---
name: music-memory
homepage: https://bluecolumn.ai
description: A practice journal. Requires an API key.
---
```bash
curl -X POST https://abc.supabase.co/functions/v1/agent-remember \\
  -H "Authorization: Bearer $BLUECOLUMN_API_KEY"
```
"""

TWO_COMMANDS = KEY_REQUEST.replace("```\n", "```\n```bash\nffmpeg -i in.wav out.mp3\n```\n", 1)

UNDECLARED_HOST = """---
name: elsewhere
description: Posts somewhere.
---
```bash
curl -s https://collect.other-site.net/x
```
"""

CLI = """---
name: wttr
description: Get the weather with curl.
metadata: {"clawdbot":{"requires":{"bins":["curl"]}}}
---
```bash
curl -s "wttr.in/London?format=3"
```
"""

WITH_SIBLING = """---
name: song-maker
description: Generate songs with a local model.
metadata: {"openclaw": {"requires": {"bins": ["ffmpeg"]}}}
---
# Song Maker

Mix the result with the `song-mixer` skill.

```bash
python scripts/run.py "a calm song"
```
"""


@pytest.fixture
def state(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("PUFFPAI_DATA_DIR", str(tmp_path / "data"))
    os.makedirs(tmp_path / "state", exist_ok=True)
    consent_tickets.reset()
    from puffpai import deps
    monkeypatch.setattr(deps, "has_program", lambda name: False)
    monkeypatch.setattr(setup_mod, "_home_set", lambda: False)
    make_mod._reset_for_tests()
    ed.clear_web_app_doors()
    yield tmp_path
    ed.clear_web_app_doors()
    consent_tickets.reset()


def put_skill(folder: str, text: str) -> str:
    path = os.path.join(skills.skills_dir(create=True), folder)
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


# ── 2: route A for a fresh repo ─────────────────────────────────────────────

def test_a_checked_repo_keeps_its_prepared_card_and_route_a_adds_it(state):
    card = {"integration_id": "rsvp-manager-abcdefgh", "code_sha": "c" * 64,
            "host_sha": "", "mode": "static_web", "lines": ["It is a web page."]}

    def check(c):
        if c.get("type") in ("server_app", "browser_app"):
            return {"ok": True, "reason": "", "prepared": dict(card, junk="x" * 10)}
        return {"ok": True, "reason": ""}
    build, _brain = run_goal("WeddingBot", check=check)
    job = next(j for j in build["jobs"] if j["route"] == "A")
    part = build["parts"][job["pick"]["ref"]]
    assert part["prepared"] == card, "check's card is kept (ids and hashes only)"
    assert S.load_build()["parts"][job["pick"]["ref"]]["prepared"] == card
    yes_mod.give(DEVICE)
    added: list = []

    def finish(iid, *, code_sha, host_sha, progress):
        added.append((iid, code_sha))
        return {"status": "added", "app_id": "rsvp-manager-abcdefgh"}
    got = make_mod.make(job, S.load_build(), hooks={
        "finish": finish, "status": lambda i: {"can_run": False}, "start": lambda i: {}})
    assert added == [("rsvp-manager-abcdefgh", "c" * 64)]
    assert got["ok"] and got["screen"]["app_id"] == "rsvp-manager-abcdefgh"


def test_a_failed_check_keeps_no_card(state):
    def check(c):
        return {"ok": False, "reason": "no", "prepared": {"integration_id": "x", "code_sha": "y"}}
    build, _brain = run_goal("RecipeBot", check=check)
    assert all("prepared" not in p for p in build["parts"].values())


# ── 5: companion skills ─────────────────────────────────────────────────────

def _skill_job(ref: str, md: str) -> tuple[dict, dict]:
    job = S.new_job("Make songs", action="make", subject=["song"], output="sound")
    job.update(route="B", ingredients=[ref],
               pick={"ref": ref, "class": S.TOOL, "score": 0.9, "why": ""})
    c = {"ref": ref, "name": "Song Maker", "type": "skill", "class": S.TOOL, "skill_md": md,
         "files": ["SKILL.md", "scripts/run.py"]}
    return job, c


def test_the_list_offers_exactly_the_siblings_the_installer_accepts(state):
    from puffpai import deps
    job, c = _skill_job("clawhub:acme/song-maker", WITH_SIBLING)
    rows = setup_mod.rows_for([job], {c["ref"]: c})
    sib = [r for r in rows if r.get("step") == "skill"]
    assert [(r["kind"], r["name"]) for r in sib] == [("package", "song-mixer")]
    assert sib[0]["label"] == "The song-mixer skill, from the same publisher"
    # The same rule the installer uses, for the lock entry it will write.
    lock = {"source": "clawhub", "owner": "acme", "slug": "song-maker"}
    assert deps.siblings_offered(WITH_SIBLING, "song-maker", lock) == ["song-mixer"]
    # A ClawHub find with no owner in its id: no publisher known, none offered.
    job2, c2 = _skill_job("clawhub:song-maker", WITH_SIBLING)
    assert not [r for r in setup_mod.rows_for([job2], {c2["ref"]: c2}) if r.get("step") == "skill"]
    # Companion rows need no input of hers: the job waits only for the yes.
    assert setup_mod.job_status(job, rows) == S.WAITING_YES


def test_make_installs_the_found_skill_with_only_the_listed_siblings(state, monkeypatch):
    job, c = _skill_job("clawhub:acme/song-maker", WITH_SIBLING)
    b = S.empty_build("songs")
    b["jobs"] = [job]
    b["parts"] = {c["ref"]: c}
    b["setup"] = setup_mod.rows_for([job], {c["ref"]: c})
    S.save_build(b)
    calls: list = []

    def install(ref, only):
        calls.append((ref, list(only)))
        put_skill("song-maker", WITH_SIBLING)
        return {"ok": True, "folder": "song-maker"}
    monkeypatch.setattr(skills, "has_program", lambda name: True)
    folder, why = make_mod._ensure_skill(c, S.load_build(), {"install": install})
    assert (folder, why) == ("song-maker", "")
    assert calls == [("clawhub:acme/song-maker", ["song-mixer"])]
    assert S.load_build()["parts"][c["ref"]]["folder"] == "song-maker"
    # Installed now: no second install.
    assert make_mod._ensure_skill({**c, "folder": "song-maker"}, S.load_build(),
                                  {"install": install}) == ("song-maker", "")
    assert len(calls) == 1


def test_the_installer_installs_only_the_names_it_is_given(state, monkeypatch):
    from puffpai import deps
    from puffpai.finding import install
    monkeypatch.setattr(deps, "siblings_offered", lambda text, own, origin: ["a-one", "b-two"])
    monkeypatch.setattr(install, "_read_lock", lambda: {"parent": {"source": "clawhub"}})
    tried: list = []
    monkeypatch.setattr(install, "install_companion",
                        lambda parent_folder, name, say, **k: tried.append(name)
                        or {"ok": True, "folder": name})
    install._install_companions("parent", "text", lambda line: None, only=["b-two"])
    assert tried == ["b-two"]
    tried.clear()
    install._install_companions("parent", "text", lambda line: None)
    assert tried == ["a-one", "b-two"], "no list: everything the rule offers, as before"


def test_a_found_skills_key_waits_for_its_install_then_is_asked(state):
    job, c = _skill_job("clawhub:acme/memory", KEY_REQUEST)
    c["class"] = classify.classify({k: v for k, v in c.items() if k != "class"})
    assert c["class"] == S.DATA
    job["pick"]["class"] = S.DATA
    job["route"] = S.C
    rows = setup_mod.rows_for([job], {c["ref"]: c})
    key = next(r for r in rows if r["kind"] == "key")
    assert key["skill_key"] and key["skill"] == "" and key["name"] == "BLUECOLUMN_API_KEY"
    assert setup_mod.job_status(job, rows) == S.WAITING_YES, "not a dead end before the yes"
    b = S.empty_build()
    b["jobs"], b["parts"], b["setup"] = [job], {c["ref"]: c}, rows
    S.save_build(b)
    make_mod._note_skill_folder(c["ref"], "music-memory")
    after = S.load_build()
    key = next(r for r in after["setup"] if r["kind"] == "key")
    assert key["skill"] == "music-memory"
    assert setup_mod.job_status(job, after["setup"]) == S.NEEDS_SETUP
    # A new plan keeps the folder it learned.
    merged = setup_mod.merge(after["setup"], setup_mod.rows_for([job], {c["ref"]: c}))
    assert next(r for r in merged if r["kind"] == "key")["skill"] == "music-memory"


# ── 10: places on the Bridge after a restart ────────────────────────────────

def test_added_apps_get_their_sites_back_at_start(state, monkeypatch):
    from puffpai.webapps import host as webhost
    app = "space-encyclopedia-islim6jk"
    rec = {"status": "installed", "name": "Space Encyclopedia",
           "scan": {"hosts": [{"host": "images-api.nasa.gov", "needed_while_running": True,
                               "for": "data"},
                              {"host": "cdn.example-fonts.org", "needed_while_running": False}]}}
    monkeypatch.setattr(webhost, "installed_ids", lambda: [app])
    monkeypatch.setattr(webhost, "read_record", lambda a: rec if a == app else None)
    # She switched the site on before the restart.
    ed.register_web_app_door(app, ["images-api.nasa.gov"])
    t = consent_tickets.mint("app_host_on", consent_tickets.target_app_host(
        app, "images-api.nasa.gov"), "Let it reach images-api.nasa.gov.", DEVICE)
    assert ed.set_app_web_hosts(app, ["images-api.nasa.gov"], by=DEVICE, ticket=t)["ok"]
    ed.clear_web_app_doors()                               # the restart
    assert ed.app_web_hosts(app) == {}
    assert make_mod.restore_web_sites() == [app]
    assert ed.app_web_hosts(app) == {"images-api.nasa.gov": True}
    assert make_mod.restore_web_sites() == [], "an app that has its place keeps it"


def test_the_set_the_build_gave_an_app_wins_at_start(state, monkeypatch):
    from puffpai.webapps import host as webhost
    app = "space-encyclopedia-islim6jk"
    monkeypatch.setattr(webhost, "installed_ids", lambda: [app])
    monkeypatch.setattr(webhost, "read_record", lambda a: {"status": "installed", "scan": {}})
    got = yes_mod.allow_app_host(app, "images-api.nasa.gov", DEVICE)
    assert got["ok"], got
    assert make_mod.kept_sites(app) == ["images-api.nasa.gov"]
    ed.clear_web_app_doors()
    assert make_mod.restore_web_sites() == [app]
    assert ed.app_web_hosts(app) == {"images-api.nasa.gov": True}


def test_the_server_restores_them_when_it_starts():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "src", "puffpai", "api_server.py")
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    start = src.index("def start_background_services")
    assert "restore_web_sites()" in src[start:start + 6000]


def test_a_new_site_never_turns_off_one_she_had_on(state):
    """The Bridge keeps her yeses per WHOLE site set; giving an app one more
    site must not silently turn off the site she already had on, and two
    listed sites for one app both end up on."""
    app = "space-encyclopedia-islim6jk"
    assert yes_mod.allow_app_host(app, "images-api.nasa.gov", DEVICE)["ok"]
    assert ed.app_web_hosts(app) == {"images-api.nasa.gov": True}
    assert yes_mod.allow_app_host(app, "api.nasa.gov", DEVICE)["ok"]
    assert ed.app_web_hosts(app) == {"images-api.nasa.gov": True, "api.nasa.gov": True}
    other = "star-atlas-abcdefgh"
    got = yes_mod.allow_app_hosts(other, ["a.starmaps.org", "b.starmaps.org", "c.starmaps.org"],
                                  DEVICE)
    assert got["a.starmaps.org"]["ok"] and got["b.starmaps.org"]["ok"]
    assert not got["c.starmaps.org"]["ok"] and "at most" in got["c.starmaps.org"]["message"]
    assert ed.app_web_hosts(other) == {"a.starmaps.org": True, "b.starmaps.org": True}


# ── 11: a CLI skill is proven with the job's sample ask ─────────────────────

def test_a_cli_skill_is_working_only_when_the_sample_ask_uses_it(state, monkeypatch):
    put_skill("wttr", CLI)
    monkeypatch.setattr(skills, "has_program", lambda name: True)
    job = {"id": "w", "route": "B", "action": "show", "ask": "What is the weather in London?"}
    made = {"ok": True, "made": {"skills": ["wttr"]}}
    asked: list = []

    def no(a, f):
        asked.append((a, f))
        return False, f"for \"{a}\" the bot did not use {f}"
    got = prove_mod.prove(job, {}, made=made, hooks={"ask_cli": no})
    assert got["state"] == "not_yet" and "did not use wttr" in got["reason"]
    assert asked == [("What is the weather in London?", "wttr")]
    got = prove_mod.prove(job, {}, made=made, hooks={"ask_cli": lambda a, f: (True, "")})
    assert got == {"state": "working", "reason": ""}
    # Programs missing: not asked at all.
    monkeypatch.setattr(skills, "has_program", lambda name: False)
    asked.clear()
    got = prove_mod.prove(job, {}, made=made, hooks={"ask_cli": no})
    assert got["state"] == "not_yet" and "curl" in got["reason"] and asked == []


def test_the_cli_proof_runs_nothing(state, monkeypatch):
    from puffpai import brain_commands, brain_loop, brain_tools
    folder = put_skill("wttr", CLI)
    monkeypatch.setattr(brain_tools, "find", lambda name, ctx=None: object())
    ran: list = []
    monkeypatch.setattr(brain_commands, "run", lambda *a, **k: ran.append(a))
    answers: list = []

    def fake_run(messages, *, ctx, backend, tools, max_steps):
        answers.append(ctx["ask"]({"tool": "run_command", "command": 'curl -s "wttr.in/London"',
                                   "folder": folder}))
        return {"text": "", "used": ["run_command"]}
    monkeypatch.setattr(brain_loop, "run", fake_run)
    assert prove_mod.ask_skill("Weather in London?", "wttr", backend=object()) == (True, "")
    assert answers == [None] and ran == [], "her yes per command is never given for her"

    def other(messages, *, ctx, backend, tools, max_steps):
        ctx["ask"]({"tool": "run_command", "command": "python other.py", "folder": "/elsewhere"})
        return {"text": ""}
    monkeypatch.setattr(brain_loop, "run", other)
    used, why = prove_mod.ask_skill("Weather in London?", "wttr", backend=object())
    assert not used and "did not use wttr" in why


# ── 13: one keyed request to its own service is a source of data ────────────

def test_classify_setup_and_make_agree_on_a_keyed_request_skill(state):
    c = {"ref": "clawhub:bluecolumn/music-memory", "name": "Music Memory", "type": "skill",
         "skill_md": KEY_REQUEST, "files": ["SKILL.md"]}
    assert classify.key_request(c) == {"hosts": ["abc.supabase.co"],
                                       "keys": ["BLUECOLUMN_API_KEY"], "program": "curl"}
    assert classify.classify(c) == S.DATA
    # make: never a command line tool, its own service as the source.
    c["class"] = S.DATA
    assert make_mod._make_one_tool(dict(c), {})["ok"] is False
    part = make_mod._part(dict(c))
    assert part["hosts"] == ["abc.supabase.co"] and part["keys"] == ["BLUECOLUMN_API_KEY"]
    f2 = make_mod.setup_rows_for(dict(c))
    assert {r["what"] for r in f2} == {"outside_address", "key"}
    assert not any(r["kind"] == "program" for r in f2), "no 'Install curl'"
    # setup: its site and its vault key; no Bridge switch named after a find.
    job = S.new_job("Keep a practice journal", action="do", subject=["practice"], output="text")
    job.update(route="C", ingredients=[c["ref"]],
               pick={"ref": c["ref"], "class": S.DATA, "score": 0.8, "why": ""})
    rows = setup_mod.rows_for([job], {c["ref"]: c})
    assert sorted(r["kind"] for r in rows) == ["key", "switch"]
    assert next(r for r in rows if r["kind"] == "switch")["host"] == "abc.supabase.co"


def test_only_a_single_request_to_its_own_service_counts(state):
    assert skills.key_request_service(TWO_COMMANDS) is None
    assert skills.key_request_service(UNDECLARED_HOST) is None, \
        "no key named and the host is not its declared service"
    assert skills.key_request_service(CLI) is None, "declared programs: a command line tool"
    http = KEY_REQUEST.replace("https://abc.supabase.co", "http://abc.supabase.co")
    assert skills.key_request_service(http) is None
    local = KEY_REQUEST.replace("https://abc.supabase.co", "https://127.0.0.1")
    assert skills.key_request_service(local) is None
    folder = put_skill("ships", KEY_REQUEST.replace("music-memory", "ships"))
    os.makedirs(os.path.join(folder, "scripts"))
    with open(os.path.join(folder, "scripts", "run.py"), "w", encoding="utf-8") as fh:
        fh.write("print(1)\n")
    assert skills.key_request_service(KEY_REQUEST, folder) is None, "it ships code"
    # A plain declared-service request with no key is one too.
    plain = ("---\nname: w\nhomepage: https://api.weatherly.org\n---\n```bash\n"
             "curl -s https://api.weatherly.org/v1/now\n```\n")
    assert skills.key_request_service(plain)["hosts"] == ["api.weatherly.org"]


def test_the_chats_round_6_path_is_left_as_she_approved_it(state):
    """In chat, the checked keyed request still runs through
    run_command; only the build treats the skill
    as a data source. Changing the chat path is hers to decide."""
    assert skills.how_it_runs_text(KEY_REQUEST)["kind"] == "cli"


# ── every data source host has a switch her one Yes turns on ────────────────

def _source_build():
    """A job reading a keyed data skill and a public API from another list."""
    skill = {"ref": "clawhub:bluecolumn/music-memory", "name": "Music Memory", "type": "skill",
             "class": S.DATA, "skill_md": KEY_REQUEST, "files": ["SKILL.md"]}
    api = {"ref": "apis_guru:launches", "name": "Launches API", "type": "live_data",
           "class": S.DATA, "hosts": ["api.launches.org"], "service": "apis_guru:launches",
           "url": "https://api.launches.org/v1/next", "needs": [], "auth": "none"}
    job = S.new_job("Keep a launch journal", action="do", subject=["launch"], output="text")
    job.update(route="C", ingredients=[skill["ref"], api["ref"]],
               pick={"ref": api["ref"], "class": S.DATA, "score": 0.8, "why": ""})
    b = S.empty_build("launches")
    b["jobs"], b["parts"] = [job], {skill["ref"]: skill, api["ref"]: api}
    b["setup"] = setup_mod.rows_for([job], b["parts"])
    yes_mod.reset(b)
    setup_mod.apply_statuses(b, only_open=False)
    return S.save_build(b), skill, api


def test_each_data_source_host_gets_its_own_switch_row(state):
    from puffpai.build import sources
    b, skill, api = _source_build()
    switches = {r["host"]: r for r in b["setup"] if r["kind"] == "switch"}
    assert set(switches) == {"abc.supabase.co", "api.launches.org"}
    for host, r in switches.items():
        assert r["service"] == sources.switch_id(host) and r["label"].startswith("Turn on ")
    assert not [r for r in b["setup"] if r["kind"] == "address"], "a switch, not a dead row"
    # make reads each through its own place on the Bridge.
    part = make_mod._part(dict(skill))
    assert part["door"] == sources.door_id("abc.supabase.co")
    assert part["url"] == "https://abc.supabase.co/"
    assert make_mod._part(dict(api))["door"] == sources.door_id("api.launches.org")


def test_the_one_yes_switches_each_source_on_with_its_own_ticket(state, monkeypatch):
    from puffpai import feeds, outside_services
    from puffpai.build import sources
    b, _skill, _api = _source_build()
    monkeypatch.setattr(outside_services, "_posture", lambda: ed.OPEN_POSTURE)
    minted: list = []
    real = consent_tickets.mint
    monkeypatch.setattr(consent_tickets, "mint",
                        lambda *a, **k: minted.append(a[:2]) or real(*a, **k))
    # Listed on "What leaves your computer" as switches, off until her yes.
    row = outside_services.row(sources.switch_id("api.launches.org"))
    assert row and row["state"] == "off" and row["can_switch"] and row["kind"] == "card"
    yes_mod.give(DEVICE)
    sites = yes_mod.switch_sites(DEVICE)
    assert {s["host"]: s["state"] for s in sites} == {"abc.supabase.co": "on",
                                                     "api.launches.org": "on"}
    for host in ("abc.supabase.co", "api.launches.org"):
        assert ("outside_on", sources.switch_id(host)) in minted
        assert ed.app_door_consent(sources.source_id(host)) is True
    feeds.sync_doors()
    assert ed.door(sources.door_id("api.launches.org")).hosts == ("api.launches.org",)
    assert all(r["done"] for r in S.load_build()["setup"] if r["kind"] == "switch")
    # And off again from the Bridge page, like any switch.
    outside_services.switch_off(sources.switch_id("api.launches.org"))
    assert ed.app_door_consent(sources.source_id("api.launches.org")) is False


# ── a website's live card is an ingredient of its job only ──────────────────

def test_a_websites_live_card_is_placed_in_its_job(state, monkeypatch):
    from puffpai import fix_api
    from puffpai.assemble import seed_switch
    from puffpai.build import api
    run_goal("WeddingBot")
    monkeypatch.setattr(seed_switch, "card", lambda sid: {
        "kind": "switch_on", "service": f"seed-{sid}", "name": sid, "label": sid,
        "detail": f"Adds {sid} as a live card."})
    monkeypatch.setattr(seed_switch, "needed_switches", lambda: [])
    monkeypatch.setattr(fix_api, "apply_switch", lambda sid, device, reassemble=True: (
        {"ok": True, "line": "On.", "app_id": "venue_finder"}, 200))
    monkeypatch.setattr(seed_switch, "apply", lambda seed, device, reassemble=True, hosts=None, **_k: (
        {"ok": True, "line": "On.", "app_id": "venue_finder"}, 200))
    yes_mod.give(DEVICE)
    yes_mod.switch_sites(DEVICE)
    b = S.load_build()
    assert b["parts"]["seed:venues"]["live_card"] == "venue_finder"
    assert yes_mod.covers(b), "placing the card never changes the list her yes covers"
    parts = {p["ref"]: p for p in api.status_view()["parts"]}
    assert parts["seed:venues"]["things"] == ["feed-venue_finder"]
    # make reads it through the live card's own switch.
    assert make_mod._part(dict(b["parts"]["seed:venues"]))["door"] == ed.app_door_id("venue_finder")


# ── the console path's ticked jobs are planned exactly ──────────────────────

def test_jobs_she_already_has_are_planned_exactly():
    import json
    from puffpai.build import interpret
    given = [{"name": "Show pictures of Earth", "does": "Photos of Earth."},
             {"name": "Remind me to water the plants", "does": ""}]
    jobs, how = interpret.interpret_given(given, "a bot", use_brain=False)
    assert how == "given" and [j["name"] for j in jobs] == [g["name"] for g in given]
    assert jobs[1]["action"] == "remind"

    def brain(messages, fmt):
        return json.dumps({"jobs": [
            {"name": "Earth photos", "does": "x", "action": "show", "output": "pictures",
             "needs": ["none"], "subject": ["earth"], "ask": "Show me Earth"},
            {"name": "Watering", "does": "y", "action": "remind", "output": "action",
             "needs": ["time"], "subject": ["plant"], "ask": "Remind me"}]})
    jobs, how = interpret.interpret_given(given, "a bot", brain=brain)
    assert how == "brain+given"
    assert [j["name"] for j in jobs] == [g["name"] for g in given], "her names, always"
    assert jobs[0]["output"] == "pictures" and jobs[1]["needs"] == ["time"]

    def merges(messages, fmt):
        return json.dumps({"jobs": [{"name": "Everything", "does": "", "action": "show",
                                     "output": "text", "needs": ["none"],
                                     "subject": ["all"], "ask": ""}]})
    jobs, how = interpret.interpret_given(given, "a bot", brain=merges)
    assert how == "given" and len(jobs) == 2, "a brain that merges her jobs is not used"


# ── 7: book and guide picks answer from them ────────────────────────────────

def test_route_words_for_book_and_guide_picks():
    job = {"route": "B", "pick": {"ref": "book:x", "class": S.TEXT}}
    assert S.how_words(job, {"book:x": {"name": "A Field Book of the Stars"}}) == \
        "Answers from A Field Book of the Stars"
    assert S.how_words(job) == "Answers from a book"
    guide = {"route": "B", "pick": {"ref": "g", "class": S.GUIDE}}
    assert S.how_words(guide) == "Answers from a guide"
    tool = {"route": "B", "pick": {"ref": "t", "class": S.TOOL}}
    assert S.how_words(tool) == "Connects a tool"
    assert S.how_words({"route": "D", "pick": {"ref": "", "class": S.TEXT}}) == "Nothing found yet"
    assert S.WORDS["answers"] == "Answers from <name>"
