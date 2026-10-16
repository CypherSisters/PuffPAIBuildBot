"""Security fixes found in live runs.

ONE setup list and ONE Yes cover exactly what the list shows;
nothing else is switched on or built.

1. No site that is not on the list is switched on: a switch never opens
   more sites than its row shows; a site a part turns out to need (a picture
   host) becomes a new row "<job> also needs <host>" for the next yes.
2. make refuses every install, tool connection, app add and app start that
   is not a row on the list her Yes covers.
3. The switch host is what the part CALLS (declared API base, URLs in its
   examples and code, its catalog row, its tool address), never a homepage
   on its own; every host it calls is listed.
4. A plain command aimed at a remote-API skill the build uses is refused in
   run_command, pointing to that skill's tool; the approved single
   keyed request is left as it is.
5. One Yes over many sites: one consent record (the batch), each site still
   its own single-use ticket and audit line.

Nothing goes online: the Bridge's wire, the installer and the brain are
replaced or never reached.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "build"))
sys.path.insert(0, os.path.dirname(__file__))

from test_build_make import KEY_REQUEST, OPEN_DATA, SESE  # noqa: E402
from test_build_matrix import GOALS, run_goal  # noqa: E402

from puffpai import brain_commands, skills  # noqa: E402
from puffpai.build import make as make_mod, schema as S  # noqa: E402
from puffpai.build import setup as setup_mod, sources, yes as yes_mod  # noqa: E402
from puffpai.security import consent_tickets, egress_doors as ed  # noqa: E402

DEVICE = "device:fsec12345678"

NEWS_HOMEPAGE_ONLY = """---
name: News
slug: news
homepage: https://clawic.com/skills/news
description: Personalized news briefings that learn your interests.
---
## Security & Privacy

This skill does NOT send data to external services.
"""

WEATHER_TWO_HOSTS = """---
name: open-meteo-weather
description: Fetch current weather and forecasts using the Open-Meteo API.
---
| Action | Endpoint |
|--------|----------|
| Geocode | `GET https://geocoding-api.open-meteo.com/v1/search?name={city}` |
| Forecast | `GET https://api.open-meteo.com/v1/forecast?latitude={lat}` |
"""

REMOTE_HOMEPAGE_ONLY = """---
name: remote-news
homepage: https://newsco-demo.org/skills/news
description: A remote-hosted news service. No local commands.
---
Ask it for the headlines.
"""

HOME_IS_API = """---
name: tides
homepage: https://tides-demo.org
description: Tide times.
---
```bash
curl -s "https://tides-demo.org/now?station=1"
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
    monkeypatch.setattr(deps, "has_package", lambda kind, name: False)
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


def _save(jobs: list[dict], parts: dict) -> dict:
    b = S.empty_build("f-sec")
    b["jobs"], b["parts"] = jobs, parts
    b["setup"] = setup_mod.rows_for(jobs, parts)
    yes_mod.reset(b)
    setup_mod.apply_statuses(b, only_open=False)
    return S.save_build(b)


def _job(name: str, refs: list[str], route: str = "C", cls: str = S.DATA) -> dict:
    j = S.new_job(name, action="show", subject=["space"], output="text")
    j.update(route=route, ingredients=list(refs),
             pick={"ref": refs[0], "class": cls, "score": 0.9, "why": ""})
    return j


# ── 1: no site that is not on the list is switched on ──────────────────────

SEED_REF = "seed:spacenews"
API_HOST = "api.spacenews-demo.org"
PIC_HOST = "assets.pics-demo.net"
PIC_HOST_2 = "cdn.pics2-demo.net"


def _seed_build() -> dict:
    """Two jobs: space news (a website from our own list, whose card turns
    out to need a picture site) and a job reading a book, which needs no
    site at all."""
    part = {"ref": SEED_REF, "name": "Space News", "type": "live_data", "class": S.DATA,
            "hosts": [API_HOST], "service": SEED_REF, "needs": [], "auth": "none",
            "url": f"https://{API_HOST}/v4/articles"}
    other = {"ref": "book:stars", "name": "Star Book", "type": "book", "class": S.TEXT}
    jobs = [_job("Show space news", [SEED_REF]),
            _job("Learn the stars", ["book:stars"], route="B", cls=S.TEXT)]
    jobs[1]["action"] = "learn"
    return _save(jobs, {SEED_REF: part, "book:stars": other})


@pytest.fixture
def seed_card(monkeypatch):
    """BuildBot's own website list, its card and its install, recorded. The
    card needs ``pictures["host"]``, a site that is NOT a picture its results
    point to (so it needs its own row); ``pictures["result_pic"]`` is a
    picture host its own results point to (her row covers it)."""
    from puffpai.assemble import seed_switch
    monkeypatch.setattr(setup_mod, "_seed_needed", lambda: ())
    monkeypatch.setattr(seed_switch, "needed_switches", lambda: [])
    monkeypatch.setattr(seed_switch, "card", lambda sid: {
        "kind": "switch_on", "service": f"seed-{sid}", "name": sid, "label": sid,
        "detail": f"Adds {sid} as a live card."})
    calls: list = []
    pictures = {"host": PIC_HOST, "result_pic": "", "result_pictures": []}

    def apply(seed, device, reassemble=True, hosts=None, result_pictures=False):
        calls.append(sorted(hosts or []))
        pictures["result_pictures"].append(result_pictures)
        allowed = set(hosts or [])
        if result_pictures and pictures["result_pic"]:
            allowed.add(pictures["result_pic"])
        extra = [h for h in (API_HOST, pictures["host"], pictures["result_pic"])
                 if h and h not in allowed]
        if extra:
            return {"ok": False, "needs_hosts": extra,
                    "message": f"it also needs {', '.join(extra)}"}, 409
        return {"ok": True, "line": "On.", "app_id": "space_news"}, 200
    monkeypatch.setattr(seed_switch, "apply", apply)
    calls_and = type("Calls", (list,), {})(calls)
    calls_and.pictures = pictures
    calls_and.log = calls
    return calls_and


def test_a_cards_picture_site_from_its_own_results_comes_with_the_one_yes(state, seed_card):
    seed_card.pictures.update(host="", result_pic=PIC_HOST)
    b = _seed_build()
    row = next(r for r in b["setup"] if r.get("service") == SEED_REF)
    assert row["detail"] == f"{API_HOST}, and the picture sites its results point to."
    yes_mod.give(DEVICE)
    sites = yes_mod.switch_sites(DEVICE)
    assert [s["state"] for s in sites] == [yes_mod.SITE_ON]
    assert seed_card.pictures["result_pictures"] == [True]
    after = S.load_build()
    assert not [r for r in after["setup"] if r.get("also")], "no second ask for its pictures"
    assert [j["status"] for j in after["jobs"]] == [S.WAITING_YES, S.WAITING_YES]


def _book_hooks() -> dict:
    return {"check": lambda c: {"ok": True}, "add_routine": lambda kind, **k: {"id": "r"}}


def test_a_picture_site_not_on_the_list_pauses_only_its_own_job(state, seed_card):
    b = _seed_build()
    row = next(r for r in b["setup"] if r.get("service") == SEED_REF)
    assert row["hosts"] == [API_HOST], "nothing known about its pictures before the yes"
    yes_mod.give(DEVICE)
    sites = yes_mod.switch_sites(DEVICE)
    # The card was offered ONLY the listed site, and was not added.
    assert seed_card.log == [[API_HOST]]
    assert [(s["row"], s["state"]) for s in sites] == [(row["id"], yes_mod.SITE_NOT_ON)]
    after = S.load_build()
    also = [r for r in after["setup"] if r.get("also")]
    assert len(also) == 1 and also[0]["hosts"] == [PIC_HOST] and not also[0]["done"]
    assert also[0]["label"] == "Show space news also needs " + PIC_HOST
    assert not next(r for r in after["setup"] if r["id"] == row["id"])["done"]
    # Her yes still covers the list: only the news job waits, with a plain reason.
    assert yes_mod.covers(after)
    news, stars = after["jobs"]
    assert news["status"] == S.NEEDS_SETUP and PIC_HOST in news["reason"]
    assert news["reason"].startswith("It also needs ")
    assert stars["status"] == S.WAITING_YES
    from puffpai.build import pipeline
    assert pipeline.waiting_jobs(after) == [stars["id"]]
    got = make_mod.make(news, after, hooks=_book_hooks())
    assert got["ok"] is False and got["status"] == S.NEEDS_SETUP and PIC_HOST in got["reason"]
    assert make_mod.make(stars, after, hooks=_book_hooks())["ok"] is True, \
        "the other job keeps building"
    # The queue never builds the paused job.
    made: list = []
    import puffpai.build.make as real_make
    orig = real_make.make
    real_make.make = lambda *a, **k: made.append(a) or orig(*a, **k)
    try:
        assert pipeline.run_job(news["id"])["state"] == S.NEEDS_SETUP
    finally:
        real_make.make = orig
    assert made == [] and S.find_job(S.load_build(), news["id"])["status"] == S.NEEDS_SETUP
    # Kept for the next plan: the row names the picture site before the yes.
    assert setup_mod.known_media_hosts(SEED_REF) == [PIC_HOST]
    replanned = setup_mod.rows_for(after["jobs"], after["parts"])
    assert next(r for r in replanned if r.get("service") == SEED_REF)["hosts"] == \
        [API_HOST, PIC_HOST]


def test_her_tap_on_the_also_row_switches_both_on_and_frees_the_job(state, seed_card):
    _seed_build()
    yes_mod.give(DEVICE)
    yes_mod.switch_sites(DEVICE)
    # Pressing the one Yes again changes nothing: the new row needs its own tap.
    yes_mod.give(DEVICE)
    assert yes_mod.switch_sites(DEVICE) and seed_card.log[-1] == [API_HOST]
    b = S.load_build()
    also = next(r for r in b["setup"] if r.get("also"))
    got = yes_mod._switch_one(also, DEVICE, set(), b)
    assert got["state"] == yes_mod.SITE_ON
    assert seed_card.log[-1] == sorted([API_HOST, PIC_HOST])

    def mark(x: dict) -> None:            # what the setup route does after a tap
        for r in x["setup"]:
            if r["id"] == also["id"]:
                r["done"] = True
        setup_mod.apply_statuses(x)
    after = S.update_build(mark)
    assert all(r["done"] for r in after["setup"] if r["kind"] == "switch")
    assert after["parts"][SEED_REF]["live_card"] == "space_news"
    assert after["jobs"][0]["status"] == S.WAITING_YES and yes_mod.covers(after)


def test_a_source_gets_one_also_row_however_many_sites_it_needs(state, seed_card):
    _seed_build()
    yes_mod.give(DEVICE)
    yes_mod.switch_sites(DEVICE)
    # Its pictures now come from another site: the same row grows.
    seed_card.pictures["host"] = PIC_HOST_2
    b = S.load_build()
    also = next(r for r in b["setup"] if r.get("also"))
    yes_mod._switch_one(also, DEVICE, set(), b)
    after = S.load_build()
    rows = [r for r in after["setup"] if r.get("also")]
    assert len(rows) == 1 and rows[0]["hosts"] == [PIC_HOST, PIC_HOST_2]
    assert "also needs" in rows[0]["label"] and PIC_HOST_2 in rows[0]["label"]
    assert yes_mod.covers(after), "a growing also row never pauses the other jobs"


def test_every_picture_host_in_the_sample_is_listed_once_at_plan_time(state):
    doc = {"results": [
        {"title": "A", "image_url": f"https://{PIC_HOST}/a.jpg"},
        {"title": "B", "image_url": f"https://{PIC_HOST_2}/b.png"},
        {"title": "C", "image_url": f"https://{PIC_HOST}/c.jpg"},
        {"title": "D", "url": "https://news-demo.org/story"}]}
    assert setup_mod.picture_hosts(doc) == [PIC_HOST, PIC_HOST_2]


def test_a_websites_card_with_an_unlisted_site_installs_nothing(state, monkeypatch):
    """The real seed_switch.apply: the draft from the sample reaches a site
    the list did not show, so nothing is installed or switched on."""
    from puffpai import drawbridge, feed_generator
    from puffpai.assemble import seed_switch
    from puffpai.catalogs import api_seeds
    monkeypatch.setattr(seed_switch, "_row", lambda sid: {"id": sid, "name": "Space News",
                                                         "host": API_HOST})
    monkeypatch.setattr(seed_switch, "needed_switches", lambda: [])
    monkeypatch.setattr(api_seeds, "to_candidate", lambda row: {"kind": "live_data"})
    draft = {"id": "space_news", "manifest": {"feed": {"service": {
        "host": API_HOST, "image_host": PIC_HOST}}}}
    monkeypatch.setattr(feed_generator, "draft_feed_app", lambda cand, build_online: draft)
    gone: list = []
    added: list = []
    monkeypatch.setattr(feed_generator, "discard_draft", lambda i: gone.append(i) or True)
    monkeypatch.setattr(feed_generator, "install_draft",
                        lambda i, allow=False: added.append((i, allow)) or {})
    monkeypatch.setattr(drawbridge, "check_addition", lambda d, by: {"verdict": "allow"})
    got, status = seed_switch.apply("spacenews", DEVICE, reassemble=False, hosts=[API_HOST])
    assert (got["ok"], status, got["needs_hosts"]) == (False, 409, [PIC_HOST])
    assert added == [] and gone == ["space_news"]
    got, status = seed_switch.apply("spacenews", DEVICE, reassemble=False,
                                    hosts=[API_HOST, PIC_HOST])
    assert got["ok"] and added == [("space_news", True)]
    # Her row covers "and the picture sites its results point to": the card's
    # picture host came from the listed site's own sample answer.
    got, status = seed_switch.apply("spacenews", DEVICE, reassemble=False,
                                    hosts=[API_HOST], result_pictures=True)
    assert got["ok"] and len(added) == 2
    # ... but never its data host, or any host but that picture host.
    draft["manifest"]["feed"]["service"]["host"] = "elsewhere-demo.org"
    got, status = seed_switch.apply("spacenews", DEVICE, reassemble=False,
                                    hosts=[API_HOST], result_pictures=True)
    assert got["ok"] is False and got["needs_hosts"] == ["elsewhere-demo.org"]


def test_a_live_card_switch_wider_than_its_row_is_refused(state, monkeypatch):
    from puffpai import fix_api
    part = {"ref": "feed:apod", "name": "Picture of the day", "type": "feed", "class": S.DATA,
            "hosts": ["api.apod-demo.org"], "service": "card-apod"}
    monkeypatch.setattr(setup_mod, "_outside_row", lambda sid: None)
    b = _save([_job("Show the picture of the day", ["feed:apod"])], {"feed:apod": part})
    row = next(r for r in b["setup"] if r["kind"] == "switch")
    assert row["hosts"] == ["api.apod-demo.org"]
    # The Bridge's switch for it opens a second site the row does not show.
    monkeypatch.setattr(yes_mod, "_bridge_sites",
                        lambda sid: ["api.apod-demo.org", "images.apod-demo.org"])
    applied: list = []
    monkeypatch.setattr(fix_api, "apply_switch", lambda *a, **k: applied.append(a) or (
        {"ok": True}, 200))
    yes_mod.give(DEVICE)
    sites = yes_mod.switch_sites(DEVICE)
    assert applied == [] and sites[0]["state"] == yes_mod.SITE_NOT_ON
    assert "images.apod-demo.org" in sites[0]["reason"]
    also = [r for r in S.load_build()["setup"] if r.get("also")]
    assert [r["hosts"] for r in also] == [["images.apod-demo.org"]]
    assert yes_mod.covers(S.load_build()), "only its own job waits"
    # Her tap on the "also needs" row: the switch with both sites, its own ticket.
    yes_mod._switch_one(also[0], DEVICE, set(), S.load_build())
    assert applied == [("card-apod", DEVICE)]


def test_a_live_card_row_lists_every_site_its_switch_opens(state, monkeypatch):
    monkeypatch.setattr(setup_mod, "_outside_row", lambda sid: {
        "kind": "card", "websites": ["api.apod-demo.org", "images.apod-demo.org"]})
    part = {"ref": "feed:apod", "name": "Picture of the day", "type": "feed", "class": S.DATA,
            "hosts": ["api.apod-demo.org"], "service": "card-apod"}
    rows = setup_mod.rows_for([_job("Show it", ["feed:apod"])], {"feed:apod": part})
    row = next(r for r in rows if r["kind"] == "switch")
    assert row["hosts"] == ["api.apod-demo.org", "images.apod-demo.org"]
    assert "images.apod-demo.org" in row["detail"]


def test_the_yes_is_for_every_site_a_row_names(state):
    b = _seed_build()
    before = yes_mod.sha(b)
    b["setup"][0]["hosts"] = [API_HOST, PIC_HOST]
    assert yes_mod.sha(b) != before, "a wider row is a new list"
    b["setup"][0]["hosts"] = [API_HOST]
    b["setup"][0]["host"] = "other.spacenews-demo.org"
    assert yes_mod.sha(b) != before
    b["setup"][0]["host"] = API_HOST
    b["setup"][0]["done"] = True
    assert yes_mod.sha(b) == before, "done changes after the yes, not the list"


def test_a_source_door_exists_only_for_a_listed_host(state):
    put_skill("open-meteo-weather", WEATHER_TWO_HOSTS)
    part = {"ref": "skill:wx", "name": "Weather", "type": "skill", "class": S.DATA,
            "folder": "open-meteo-weather"}
    b = _save([_job("Show the weather", ["skill:wx"])], {"skill:wx": part})
    hosts = sorted(e[1]["hosts"][0] for e in sources.door_entries(b))
    assert hosts == ["api.open-meteo.com", "geocoding-api.open-meteo.com"]
    b["setup"] = [r for r in b["setup"] if r.get("host") != "api.open-meteo.com"]
    assert [e[1]["hosts"] for e in sources.door_entries(b)] == [
        ["geocoding-api.open-meteo.com"]]


# ── 2: make refuses anything not on the list ───────────────────────────────

APP_PART = {"ref": "gh:lesson", "name": "Lesson App", "type": "browser_app", "class": S.APP,
            "integration_id": "lesson-app-abcdefgh",
            "prepared": {"integration_id": "lesson-app-abcdefgh", "code_sha": "c" * 64,
                         "host_sha": "h" * 64, "mode": "own_server"}}
MCP_PART = {"ref": "mcp:geo", "name": "Geo", "type": "mcp", "class": S.TOOL,
            "mcp": {"url": "https://mcp.geo-demo.net/mcp"}, "hosts": ["mcp.geo-demo.net"]}
CLI_MD = ("---\nname: song-maker\ndescription: Make songs.\n"
          'metadata: {"openclaw": {"requires": {"bins": ["ffmpeg"]}}}\n---\n'
          '```bash\npython scripts/run.py "a song"\n```\n')
CLI_PART = {"ref": "clawhub:acme/song-maker", "name": "Song Maker", "type": "skill",
            "class": S.TOOL, "skill_md": CLI_MD, "files": ["SKILL.md", "scripts/run.py"]}
GUIDE_PART = {"ref": "clawhub:acme/theory", "name": "Theory", "type": "skill",
              "class": S.GUIDE, "skill_md": "---\nname: theory\ndescription: Chords.\n---\n"
                                            "Explain chords.\n"}


class Recorder:
    """Every side effect make may cause, recorded (nothing real runs)."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.plans: dict[str, list] = {}

    def _make_plan(self, folder):
        from puffpai import deps
        plan = deps.make_plan(folder, thing=f"skill-{folder}")
        self.plans[str(plan.get("id"))] = list(plan.get("steps") or [])
        return plan

    def hooks(self, monkeypatch=None) -> dict:
        def install(ref, only):
            self.calls.append(("install", ref, None))
            folder = ref.rsplit("/", 1)[-1]
            md = next((p["skill_md"] for p in (CLI_PART, GUIDE_PART) if p["ref"] == ref),
                      f"---\nname: {folder}\ndescription: x.\n---\nx\n")
            put_skill(folder, md)
            return {"ok": True, "folder": folder}
        return {
            "check": lambda c: {"ok": True},
            "finish": lambda iid, **k: self.calls.append(("app", iid, None)) or {
                "status": "added"},
            "status": lambda iid: {"can_run": True, "state": "stopped"},
            "start": lambda iid: self.calls.append(("start", iid, None)) or {
                "ok": True, "status": {"state": "running", "url": "http://127.0.0.1:9"}},
            "add_server": lambda name, target, **k: self.calls.append(("mcp", name, target))
            or {"ok": True, "server": {"name": name}},
            "register_mcp": lambda iid, name: self.calls.append(("mcp", iid, None)) or {
                "ok": True},
            "install": install,
            "install_sdk": lambda: self.calls.append(("sdk", None, None)) or {"ok": True},
            "make_plan": self._make_plan,
            "run_plan": lambda pid: self.calls.append(("run_plan", pid, None)) or {"ok": True},
            "add_routine": lambda kind, **k: {"id": k["routine_id"]},
            "assemble": lambda row, **k: {"state": "working"},
        }


def test_make_does_nothing_that_is_not_a_row_on_the_list(state):
    parts = {p["ref"]: dict(p) for p in (APP_PART, MCP_PART, CLI_PART, GUIDE_PART)}
    jobs = [_job("Take lessons", ["gh:lesson"], route="A", cls=S.APP),
            _job("Use tools", ["mcp:geo", CLI_PART["ref"], GUIDE_PART["ref"]], route="B",
                 cls=S.TOOL)]
    b = S.empty_build("f-sec")
    b["jobs"], b["parts"], b["setup"] = jobs, parts, []      # a list that shows nothing
    yes_mod.reset(b)
    S.save_build(b)
    yes_mod.give(DEVICE)
    rec = Recorder()
    for j in S.load_build()["jobs"]:
        got = make_mod.make(j, S.load_build(), hooks=rec.hooks())
        assert got["ok"] is False and "not on your setup list" in got["reason"], got
    assert rec.calls == [], "nothing was added, started, connected or installed"


def test_with_the_rows_listed_make_does_exactly_those(state, monkeypatch):
    from puffpai import mcp_client
    from puffpai.build import classify
    monkeypatch.setattr(classify, "_mcp_sdk", lambda: True)     # the mcp package is here
    monkeypatch.setattr(mcp_client, "sdk_available", lambda: True)
    # ffmpeg's install step as on Windows (winget), on every computer: on
    # Linux (not root) and macOS BuildBot has no installer for it, so there
    # would be no plan to run and the run_plan path would go unchecked.
    from puffpai import deps

    def winget_step(prog):
        row = deps.SYSTEM_PROGRAMS.get(prog) or {}
        if not row.get("winget"):
            return None
        return {"kind": "winget", "name": row["winget"], "version": "", "from": "list",
                "for": prog, "label": row["words"]}
    monkeypatch.setattr(deps, "_system_step", winget_step)
    parts = {p["ref"]: dict(p) for p in (APP_PART, MCP_PART, CLI_PART, GUIDE_PART)}
    jobs = [_job("Take lessons", ["gh:lesson"], route="A", cls=S.APP),
            _job("Use tools", ["mcp:geo", CLI_PART["ref"], GUIDE_PART["ref"]], route="B",
                 cls=S.TOOL)]
    b = _save(jobs, parts)
    makes = {(r["make"], r["ref"]) for r in b["setup"] if r.get("make")}
    assert makes == {("app", "gh:lesson"), ("mcp", "mcp:geo"),
                     ("install", CLI_PART["ref"]), ("install", GUIDE_PART["ref"])}
    yes_mod.give(DEVICE)
    rec = Recorder()
    for j in S.load_build()["jobs"]:
        make_mod.make(j, S.load_build(), hooks=rec.hooks())
    kinds = sorted({c[0] for c in rec.calls})
    assert kinds == ["app", "install", "mcp", "run_plan", "start"], rec.calls
    _assert_every_call_listed(S.load_build(), rec.calls, parts, rec.plans)


def test_a_tool_address_other_than_the_listed_one_is_not_connected(state):
    b = _save([_job("Use tools", ["mcp:geo"], route="B", cls=S.TOOL)],
              {"mcp:geo": dict(MCP_PART)})
    yes_mod.give(DEVICE)
    b = S.load_build()
    moved = {**b["parts"]["mcp:geo"], "mcp": {"url": "https://elsewhere-demo.net/mcp"}}
    rec = Recorder()
    got = make_mod._make_one_tool(moved, rec.hooks(), b)
    assert got["ok"] is False and "not on your setup list" in got["reason"]
    assert rec.calls == []


def test_an_install_step_not_on_the_list_is_not_run(state):
    _save([_job("Use tools", [CLI_PART["ref"]], route="B", cls=S.TOOL)],
          {CLI_PART["ref"]: dict(CLI_PART)})
    yes_mod.give(DEVICE)
    rec = Recorder()
    hooks = {**rec.hooks(), "make_plan": lambda folder: {
        "id": "p", "steps": [{"kind": "pip", "name": "something-else"}]}}
    got = make_mod.make(S.load_build()["jobs"][0], S.load_build(), hooks=hooks)
    assert got["ok"] is False and "something-else" in got["reason"]
    assert [c[0] for c in rec.calls] == ["install"], "the listed install only, no steps run"


def _assert_every_call_listed(build: dict, calls: list, parts: dict,
                              plans: dict | None = None) -> None:
    by_iid = {str((p.get("prepared") or {}).get("integration_id") or p.get("integration_id")
                  or ""): ref for ref, p in parts.items()}
    by_mcp = {}
    for ref, p in parts.items():
        by_mcp[make_mod._mcp_name(p)] = ref
    for kind, ident, target in calls:
        if kind in ("app", "start"):
            ref = by_iid[ident]
            assert not make_mod.not_listed(build, {"ref": ref}, "app"), (kind, ident)
        elif kind == "mcp":
            ref = by_mcp.get(ident) or by_iid.get(ident)
            assert ref and not make_mod.not_listed(build, {"ref": ref}, "mcp", target), \
                (kind, ident, target)
        elif kind == "install":
            assert not make_mod.not_listed(build, {"ref": ident}, "install"), (kind, ident)
        elif kind == "sdk":
            assert not make_mod.not_listed(build, {"ref": ""}, "sdk"), kind
        elif kind == "run_plan":
            steps = (plans or {}).get(str(ident))
            assert steps, ("a plan run that was never made", ident)
            for step in steps:
                assert any(r.get("name") == step.get("name") for r in build["setup"]), step


@pytest.fixture
def no_mcp_package(monkeypatch):
    """This computer has no mcp package (as on some machines): tool
    servers still fit, and the list offers the package."""
    from puffpai import mcp_client
    from puffpai.build import classify
    monkeypatch.setattr(classify, "_mcp_sdk", lambda: False)
    monkeypatch.setattr(mcp_client, "sdk_available", lambda: False)


@pytest.mark.parametrize("name", list(GOALS))
def test_every_make_action_in_the_matrix_has_a_listed_row(state, name, no_mcp_package):
    run_goal(name)
    yes_mod.give(DEVICE)
    build = S.load_build()
    rec = Recorder()
    for j in build["jobs"]:
        make_mod.make(j, S.load_build(), hooks=rec.hooks())
    parts = {**{c["ref"]: c for c in GOALS[name]["catalog"]}, **build["parts"]}
    _assert_every_call_listed(S.load_build(), rec.calls, parts, rec.plans)
    if name == "MusicBot":
        assert ("install", "clawhub:song-maker", None) in rec.calls
    if name == "PianoBot":
        assert any(c[0] == "mcp" for c in rec.calls)
        assert ("sdk", None, None) in rec.calls, "the mcp package, from its listed row"
    # The same list without its make rows: make does none of it.
    b = S.load_build()
    b["setup"] = [r for r in b["setup"] if not r.get("make") and not r.get("step")]
    yes_mod.reset(b)
    S.save_build(b)
    yes_mod.give(DEVICE)
    rec2 = Recorder()
    for j in S.load_build()["jobs"]:
        make_mod.make(j, S.load_build(), hooks=rec2.hooks())
    assert [c for c in rec2.calls if c[0] != "run_plan"] == [], rec2.calls


# ── 3: the switch host is what the part calls ──────────────────────────────

def test_a_skill_that_calls_two_hosts_gets_a_switch_for_each(state):
    part = {"ref": "clawhub:x/open-meteo-weather", "name": "Weather", "type": "skill",
            "class": S.DATA, "skill_md": WEATHER_TWO_HOSTS}
    rows = setup_mod.rows_for([_job("Show the weather", [part["ref"]])], {part["ref"]: part})
    assert sorted(r["host"] for r in rows if r["kind"] == "switch") == [
        "api.open-meteo.com", "geocoding-api.open-meteo.com"]
    assert all(r["service"] == sources.switch_id(r["host"]) for r in rows
               if r["kind"] == "switch")


def test_a_homepage_is_never_the_switch_host(state):
    part = {"ref": "clawhub:clawic/news", "name": "News", "type": "skill", "class": S.DATA,
            "skill_md": NEWS_HOMEPAGE_ONLY, "facts": {"hosts": ["clawic.com"]},
            "url": "https://clawic.com/skills/news"}
    rows = setup_mod.rows_for([_job("Show news", [part["ref"]])], {part["ref"]: part})
    assert not [r for r in rows if r["kind"] in ("switch", "address")]
    assert sources.part_hosts(part) == []
    # A remote service whose only address is its homepage: how_it_runs
    # names the homepage, but it is not a call, so it is not a switch.
    assert skills.how_it_runs_text(REMOTE_HOMEPAGE_ONLY)["hosts"] == ["newsco-demo.org"]
    assert sources.skill_calls(REMOTE_HOMEPAGE_ONLY) == []
    # Without its text nothing is known, so nothing is listed (fail closed).
    assert sources.part_hosts({k: v for k, v in part.items() if k != "skill_md"}) == []


def test_a_homepage_that_is_also_the_api_is_listed(state):
    assert sources.skill_calls(HOME_IS_API) == ["tides-demo.org"]
    # A keyed request: its own service, not its homepage.
    assert sources.skill_calls(KEY_REQUEST) == ["abc.supabase.co"]


def test_a_catalog_api_lists_its_row_host_not_its_page(state):
    part = {"ref": "apis_guru:launches", "type": "live_data", "class": S.DATA,
            "hosts": ["ll.launches-demo.net"], "url": "https://www.launches-demo.net/about"}
    assert sources.part_hosts(part) == ["ll.launches-demo.net"]
    assert sources.part_hosts({"type": "live_data", "url": "https://api.only-demo.org/v1"}) \
        == ["api.only-demo.org"], "a row whose address is its API base"


def test_a_remote_tool_skill_lists_its_tool_address(state):
    part = {"ref": "skills.sh:agentpmt/x/sese", "name": "Space Explorer", "type": "skill",
            "class": S.TOOL, "skill_md": SESE}
    rows = setup_mod.rows_for([_job("Ask space facts", [part["ref"]], route="B", cls=S.TOOL)],
                              {part["ref"]: part})
    addr = [r for r in rows if r["kind"] == "address"]
    assert [(r["host"], r["make"]) for r in addr] == [("api.agentpmt.com", "mcp")]
    assert not [r for r in rows if "agentpmt.com/marketplace" in str(r)]


# ── 4: a plain command to a remote-API skill's host ─────────────────────────

def _in_build(folder: str, cls: str) -> None:
    part = {"ref": f"skill:{folder}", "name": folder, "type": "skill", "class": cls,
            "folder": folder}
    b = S.empty_build("f-sec")
    b["jobs"] = [_job("Use it", [part["ref"]], route="B" if cls == S.TOOL else "C", cls=cls)]
    b["parts"] = {part["ref"]: part}
    S.save_build(b)


def test_a_curl_to_a_remote_tool_skills_host_is_refused(state):
    put_skill("space-earth-science-explorer", SESE)
    _in_build("space-earth-science-explorer", S.TOOL)
    for cmd in ("curl -s https://api.agentpmt.com/v1/query?q=mars",
                "python -c \"import urllib.request;urllib.request.urlopen("
                "'https://api.agentpmt.com')\"",
                "wget -qO- http://www.agentpmt.com/x"):
        with pytest.raises(ValueError) as err:
            brain_commands.question({"command": cmd}, {})
        assert "space-earth-science-explorer" in str(err.value) and "Nothing was run" in \
            str(err.value)
        with pytest.raises(ValueError):
            brain_commands.run({"command": cmd}, {"said_yes": True})


def test_a_curl_to_a_remote_data_skills_host_is_refused(state):
    put_skill("open-data-weather", OPEN_DATA)
    _in_build("open-data-weather", S.DATA)
    with pytest.raises(ValueError) as err:
        brain_commands.question({"command": 'curl -s "https://api.open-meteo.com/v1/x"'}, {})
    assert "open-data-weather" in str(err.value)
    assert brain_commands.remote_api_owner("curl api.open-meteo.com/v1/x") == "open-data-weather"
    assert brain_commands.remote_api_owner("curl -s https://wttr.in/London") == ""


def test_chat_is_unchanged_for_skills_the_build_does_not_use(state):
    put_skill("space-earth-science-explorer", SESE)
    q = brain_commands.question({"command": "curl -s https://api.agentpmt.com/v1/q"}, {})
    assert q["tool"] == "run_command", "no build part: chat asks as before"


def test_round_6s_keyed_request_is_left_as_she_approved_it(state):
    put_skill("music-memory", KEY_REQUEST)
    _in_build("music-memory", S.DATA)
    cmd = ('curl -X POST https://abc.supabase.co/functions/v1/agent-remember '
           '-H "Authorization: Bearer $BLUECOLUMN_API_KEY"')
    assert brain_commands.remote_api_owner(cmd) == ""


# ── 5: many sites under one yes ────────────────────────────────────────────

def test_a_batch_counts_once_and_only_for_its_own_pairs(state):
    pairs = [("outside_on", f"card-src_{n:012x}") for n in range(30)]
    with consent_tickets.yes_batch(DEVICE, pairs, label="build:abc") as n:
        assert n == 30
        for action, target in pairs:
            t = consent_tickets.mint(action, target, "Switch it on.", DEVICE)
            assert consent_tickets.redeem(t, DEVICE, action, target)[0]
        # each pair once: the same target again counts like any mint
        for _ in range(consent_tickets.MINTS_PER_MINUTE - 1):
            consent_tickets.mint(*pairs[0], "Again.", DEVICE)
        with pytest.raises(consent_tickets.TicketRefused):
            consent_tickets.mint(*pairs[1], "Again.", DEVICE)
    consent_tickets.reset()
    # Another device in the same block gets nothing from it.
    other = "device:other12345678"
    with consent_tickets.yes_batch(DEVICE, pairs):
        for n, (action, target) in enumerate(pairs):
            if n < consent_tickets.MINTS_PER_MINUTE:
                consent_tickets.mint(action, target, "x", other)
            else:
                with pytest.raises(consent_tickets.TicketRefused):
                    consent_tickets.mint(action, target, "x", other)
                break


def test_one_yes_switches_on_more_than_twenty_sites(state, monkeypatch):
    from puffpai import outside_services
    monkeypatch.setattr(outside_services, "_posture", lambda: ed.OPEN_POSTURE)
    parts, jobs = {}, []
    for j in range(5):
        refs = []
        for k in range(5):
            ref = f"apis:src{j}{k}"
            parts[ref] = {"ref": ref, "name": f"Source {j}{k}", "type": "live_data",
                          "class": S.DATA, "hosts": [f"api.src{j}{k}-demo.org"],
                          "service": ref, "needs": [], "auth": "none"}
            refs.append(ref)
        jobs.append(_job(f"Show set {j}", refs))
    b = _save(jobs, parts)
    assert sum(1 for r in b["setup"] if r["kind"] == "switch") == 25
    audits: list = []
    monkeypatch.setattr(yes_mod, "_audit", lambda result, details: audits.append(
        (result, details)))
    yes_mod.give(DEVICE)
    sites = yes_mod.switch_sites(DEVICE)
    assert len(sites) == 25 and {s["state"] for s in sites} == {yes_mod.SITE_ON}, \
        [s["reason"] for s in sites if s["state"] != yes_mod.SITE_ON][:2]
    assert sum(1 for r, _d in audits if r == "site") == 25, "one audit line per site"
    for s in sites:
        assert ed.app_door_consent(sources.source_id(s["host"])) is True


# ── the mcp package tool servers need: a row her Yes covers ─────────────────

def test_a_tool_server_fits_and_the_list_offers_the_mcp_package(state, no_mcp_package):
    from puffpai.build import classify
    assert classify.not_usable({**MCP_PART, "what": "Look up places and maps for any town."}) \
        == "", "a missing package is a setup row, not a reason to drop the tool"
    b = _save([_job("Use tools", ["mcp:geo"], route="B", cls=S.TOOL)],
              {"mcp:geo": dict(MCP_PART)})
    sdk = [r for r in b["setup"] if r.get("make") == setup_mod.MAKE_SDK]
    assert len(sdk) == 1 and sdk[0]["id"] == setup_mod.SDK_ROW and sdk[0]["kind"] == "package"
    assert setup_mod.job_status(b["jobs"][0], b["setup"]) == S.WAITING_YES
    yes_mod.give(DEVICE)
    rec = Recorder()
    got = make_mod.make(S.load_build()["jobs"][0], S.load_build(), hooks=rec.hooks())
    assert got["ok"] and [c[0] for c in rec.calls] == ["sdk", "mcp"]


def test_without_its_row_the_mcp_package_is_not_installed(state, no_mcp_package):
    b = _save([_job("Use tools", ["mcp:geo"], route="B", cls=S.TOOL)],
              {"mcp:geo": dict(MCP_PART)})
    b["setup"] = [r for r in b["setup"] if r.get("make") != setup_mod.MAKE_SDK]
    yes_mod.reset(b)
    S.save_build(b)
    yes_mod.give(DEVICE)
    rec = Recorder()
    got = make_mod.make(S.load_build()["jobs"][0], S.load_build(), hooks=rec.hooks())
    assert got["ok"] is False and "mcp package" in got["reason"] and rec.calls == []


def test_the_mcp_package_installs_only_a_checked_1x_wheel_into_its_own_folder(state, monkeypatch):
    from puffpai import deps
    ran: list = []
    monkeypatch.setattr(deps, "_switch_off", lambda: False)
    monkeypatch.setattr(deps, "_run_installer", lambda argv, extra=None: ran.append(argv) or {
        "code": 0, "output": ""})
    monkeypatch.setattr(deps, "_pypi_releases", lambda name: {
        "2.3.0": [{"packagetype": "bdist_wheel"}], "1.25.0": [{"packagetype": "bdist_wheel"}]})
    monkeypatch.setattr(deps, "check_step", lambda step: ({"verdict": "allow"}, "2.0.1"))
    got = deps.install_mcp_sdk()
    assert got["ok"] is False and "1.x" in got["line"] and ran == []
    monkeypatch.setattr(deps, "check_step", lambda step: ({"verdict": "block", "line": "no."},
                                                          "1.25.0"))
    assert deps.install_mcp_sdk()["ok"] is False and ran == []
    monkeypatch.setattr(deps, "check_step", lambda step: ({"verdict": "allow"}, "1.25.0"))
    monkeypatch.setattr(deps, "_switch_off", lambda: True)
    assert deps.install_mcp_sdk()["ok"] is False and ran == [], "registry switch off"
    monkeypatch.setattr(deps, "_switch_off", lambda: False)
    assert deps.install_mcp_sdk()["ok"] is True
    argv = ran[0]
    assert argv[argv.index("--target") + 1] == deps.mcp_sdk_dir() != deps.py_target()
    assert "--only-binary=:all:" in argv and argv[-1] == "mcp==1.25.0"


def test_only_the_mcp_folder_joins_the_import_path_and_last(state, monkeypatch):
    import importlib.util

    from puffpai import deps, mcp_client
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec",
                        lambda name, *a: None if name == "mcp" and deps.mcp_sdk_dir()
                        not in sys.path else real(name, *a))
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert mcp_client.sdk_available() is False and deps.mcp_sdk_dir() not in sys.path
    os.makedirs(os.path.join(deps.py_target(), "mcp"))
    open(os.path.join(deps.py_target(), "mcp", "__init__.py"), "w").close()
    assert mcp_client.sdk_available() is False, "the shared tools folder never counts"
    os.makedirs(os.path.join(deps.mcp_sdk_dir(), "mcp"))
    open(os.path.join(deps.mcp_sdk_dir(), "mcp", "__init__.py"), "w").close()
    assert mcp_client.sdk_available() is True
    assert sys.path[-1] == deps.mcp_sdk_dir() and deps.py_target() not in sys.path


# ── 2: the mcp package's version is the newest 1.x with a wheel ─────────────

def test_the_newest_1x_release_with_a_wheel_is_picked_never_latest(state):
    from puffpai import deps
    wheel, sdist = {"packagetype": "bdist_wheel"}, {"packagetype": "sdist"}
    releases = {"2.3.0": [wheel], "1.26.1": [sdist], "1.26.0": [{**wheel, "yanked": True}],
                "1.25.2": [wheel, sdist], "1.25.10": [wheel], "1.27.0rc1": [wheel],
                "1.23.9": [wheel]}
    assert deps.newest_mcp_1x(releases) == "1.25.10"
    assert deps.newest_mcp_1x({"2.3.0": [wheel]}) == ""


def test_the_install_asks_drawbridge_about_that_1x_version(state, monkeypatch):
    from puffpai import deps
    asked: list = []
    monkeypatch.setattr(deps, "_switch_off", lambda: False)
    monkeypatch.setattr(deps, "_pypi_releases", lambda name: {
        "2.3.0": [{"packagetype": "bdist_wheel"}], "1.25.3": [{"packagetype": "bdist_wheel"}]})
    monkeypatch.setattr(deps, "check_step", lambda step: asked.append(step) or (
        {"verdict": "allow"}, step.get("version") or "2.3.0"))
    monkeypatch.setattr(deps, "_run_installer",
                        lambda argv, extra=None: {"code": 0, "output": ""})
    got = deps.install_mcp_sdk()
    assert got["ok"] and "1.25.3" in got["line"] and asked[0]["version"] == "1.25.3"
    monkeypatch.setattr(deps, "_pypi_releases", lambda name: {
        "2.3.0": [{"packagetype": "bdist_wheel"}]})
    got = deps.install_mcp_sdk()
    assert got["ok"] is False and "1.x" in got["line"]


# ── 1b: pictures a source's results point to, through the Bridge ────────────

SRC_APP = "src_pictest01"
SRC_HOST = "images-api.nasa-demo.gov"
RESULT_HOST = "images-assets.nasa-demo.gov"


class _PicResp:
    def __init__(self, ctype="image/jpeg", body=b"\xff\xd8pic", status=200):
        self.status_code, self.headers, self._body = status, {"Content-Type": ctype}, body

    def iter_content(self, n):
        yield self._body

    def close(self):
        pass


@pytest.fixture
def bridge(monkeypatch):
    """The source's own door on the Bridge (her yes recorded), the network
    replaced: every picture request is recorded, none leaves the box."""
    monkeypatch.setattr(ed, "run_posture", lambda: {"run_egress": ed.OPEN_POSTURE})
    monkeypatch.setattr(ed, "_check_resolved_public",
                        lambda d, host, port, resolver=None: ["93.184.215.14"])
    monkeypatch.setattr(ed, "_verify_peer", lambda d, resp, host: None)
    sent: list = []
    answer = {"resp": _PicResp()}

    def send(method, url, **kw):
        sent.append((method, url, sorted(k for k in kw if k != "pin")))
        return answer["resp"]
    monkeypatch.setattr(ed, "_send", send)
    ed.register_app_doors([(SRC_APP, {"hosts": [SRC_HOST], "name": "NASA demo",
                                      "purpose": "x", "data_sent": "x"})])
    ed.set_app_door_consent(SRC_APP, True)
    audits: list = []
    from puffpai.security import audit_log
    monkeypatch.setattr(audit_log.AuditLog, "log", lambda self, **k: audits.append(k))
    ed._PICTURE_AUDITED.clear()
    yield {"sent": sent, "answer": answer, "audits": audits, "door": ed.app_door_id(SRC_APP)}
    ed.register_app_doors([])


def test_a_picture_from_the_sources_results_comes_through_and_is_audited(state, bridge):
    url = f"https://{RESULT_HOST}/image/a~thumb.jpg"
    status, headers, body = ed.result_picture_get(bridge["door"], url, {RESULT_HOST})
    assert (status, headers["content-type"], body) == (200, "image/jpeg", b"\xff\xd8pic")
    assert [(m, u) for m, u, _k in bridge["sent"]] == [("GET", url)]
    assert "params" not in bridge["sent"][0][2] and "headers" not in bridge["sent"][0][2]
    ed.result_picture_get(bridge["door"], url, {RESULT_HOST})
    lines = [a for a in bridge["audits"] if a.get("action") == "result_picture"]
    assert len(lines) == 1 and RESULT_HOST in lines[0]["details"], "one line per site"


@pytest.mark.parametrize("why", ["shut", "not_in_results", "svg", "html", "too_big", "env_off"])
def test_anything_else_is_refused_without_a_picture(state, bridge, monkeypatch, why):
    url = f"https://{RESULT_HOST}/image/a.jpg"
    hosts = {RESULT_HOST}
    if why == "shut":
        ed.set_app_door_consent(SRC_APP, False)
    elif why == "not_in_results":
        hosts = {"other-demo.net"}
    elif why == "svg":
        bridge["answer"]["resp"] = _PicResp("image/svg+xml", b"<svg/>")
    elif why == "html":
        bridge["answer"]["resp"] = _PicResp("text/html", b"<html>")
    elif why == "too_big":
        bridge["answer"]["resp"] = _PicResp(body=b"x" * 64)
    elif why == "env_off":
        monkeypatch.setenv("PUFFPAI_EGRESS_RESULT_PICTURES", "off")
    with pytest.raises(ed.EgressBlocked):
        ed.result_picture_get(bridge["door"], url, hosts,
                              max_bytes=32 if why == "too_big" else ed.RESULT_PICTURE_MAX_BYTES)
    if why in ("shut", "not_in_results", "env_off"):
        assert bridge["sent"] == [], "no socket for a refused picture"


def test_the_picture_door_never_opens_any_other_way(state, bridge, monkeypatch):
    url = f"https://{RESULT_HOST}/a.jpg"
    # Even if a config line or a hand-edited switch said "on" for the door
    # itself, it opens only through result_picture_get's scope.
    real_switch = ed._switch
    monkeypatch.setattr(ed, "_switch", lambda d: (True, "forced on") if d.id ==
                        ed.RESULT_PICTURES else real_switch(d))
    for call in (lambda: ed.egress_get(ed.RESULT_PICTURES, url),
                 lambda: ed.egress_request(ed.RESULT_PICTURES, "POST", url),
                 lambda: ed.egress_get(ed.RESULT_PICTURES, url, picture_scope=object())):
        with pytest.raises(ed.EgressBlocked):
            call()
    scope = ed._PictureScope(bridge["door"], frozenset({RESULT_HOST}), ed._SCOPE_KEY)
    for extra in ({"params": {"q": "her words"}}, {"headers": {"Accept": "image/*"}}):
        with pytest.raises(ed.EgressBlocked):
            ed.egress_get(ed.RESULT_PICTURES, url, picture_scope=scope, **extra)
    with pytest.raises(ed.EgressBlocked):     # a scope never widens another door
        ed.egress_get(bridge["door"], url, picture_scope=scope)
    assert bridge["sent"] == []
    assert real_switch(ed.door(ed.RESULT_PICTURES))[0] is not True, "no switch of its own"
    from puffpai import outside_services
    row = next(r for r in outside_services.rows() if r["id"] == ed.RESULT_PICTURES)
    assert row["can_switch"] is False, "listed, switched with each data source"


def test_the_app_shows_only_pictures_its_sources_results_named(state, bridge, monkeypatch):
    from puffpai.assemble.server import host as host_mod
    part = {"ref": "apis:nasa", "name": "NASA demo", "type": "live_data", "class": S.DATA,
            "hosts": [SRC_HOST], "service": "apis:nasa", "needs": [], "auth": "none"}
    other = {"ref": "book:stars", "name": "Star Book", "type": "book", "class": S.TEXT}
    jobs = [_job("Show Earth views", ["apis:nasa"]),
            _job("Learn the stars", ["book:stars"], route="B", cls=S.TEXT)]
    jobs[1]["action"] = "learn"
    b = _save(jobs, {"apis:nasa": part, "book:stars": other})
    row = next(r for r in b["setup"] if r["kind"] == "switch")
    assert row["detail"] == f"{SRC_HOST}, and the picture sites its results point to."
    yes_mod.give(DEVICE)
    app_id = make_mod.app_id_of(b["jobs"][0])
    monkeypatch.setattr(host_mod, "load_spec", lambda a: {"sources": [
        {"id": "s", "kind": "http_json", "url": f"https://{SRC_HOST}/search",
         "door": bridge["door"]}]})
    got: list = []
    monkeypatch.setattr(host_mod, "_picture_get", lambda door, url, hosts, **k: got.append(
        (door, url, sorted(hosts))) or (200, {"content-type": "image/jpeg"}, b"pic"))
    yes_mod._reset_runtime_for_tests()
    pic = f"https://{RESULT_HOST}/image/a~thumb.jpg"
    assert host_mod.note_result_pictures(app_id, bridge["door"], [
        {"title": "Earth", "links": [{"href": pic}],
         "page": "https://www.nasa-demo.gov/x"}]) == 1
    status, headers, body = host_mod.media(app_id, pic)
    assert (status, body) == (200, b"pic") and got == [(bridge["door"], pic, [RESULT_HOST])]
    # A site its results never named (a page can ask for anything): refused,
    # and NO row: a page never puts a site on her list.
    status, _h, _b = host_mod.media(app_id, "https://tracker-demo.net/p.gif")
    assert status == 403 and len(got) == 1
    after = S.load_build()
    assert not [r for r in after["setup"] if r.get("also")]
    assert all(j["status"] != S.NEEDS_SETUP for j in after["jobs"])
    # The source's OWN result pointed at a site that did not answer with a
    # picture: that one may become this job's "also needs" row, only this job.
    def not_a_picture(door, url, hosts, **k):
        raise ed.EgressBlocked(ed.RESULT_PICTURES, "html", "not a picture",
                               code="not_a_picture")
    monkeypatch.setattr(host_mod, "_picture_get", not_a_picture)
    page = f"https://{RESULT_HOST}/image/b~orig.jpg"
    host_mod.note_result_pictures(app_id, bridge["door"], [{"image": page}])
    assert host_mod.media(app_id, page)[0] == 502
    after = S.load_build()
    assert [r["hosts"] for r in after["setup"] if r.get("also")] == [[RESULT_HOST]]
    news, stars = after["jobs"]
    assert news["status"] == S.NEEDS_SETUP and stars["status"] != S.NEEDS_SETUP
    assert yes_mod.covers(after)


def test_a_forged_host_can_never_reach_her_list(state, bridge, monkeypatch):
    from puffpai.assemble.server import host as host_mod
    part = {"ref": "apis:nasa", "name": "NASA demo", "type": "live_data", "class": S.DATA,
            "hosts": [SRC_HOST], "service": "apis:nasa", "needs": [], "auth": "none"}
    b = _save([_job("Show Earth views", ["apis:nasa"])], {"apis:nasa": part})
    yes_mod.give(DEVICE)
    app_id = make_mod.app_id_of(b["jobs"][0])
    yes_mod._reset_runtime_for_tests()
    monkeypatch.setattr(host_mod, "load_spec", lambda a: {"sources": []})
    host_mod.note_result_pictures(app_id, bridge["door"],
                                  [{"image": f"https://{RESULT_HOST}/a.jpg"}])
    # Straight at the seam, with the source's own door named: still refused,
    # because the source never named that site.
    assert yes_mod.note_runtime_need(app_id, "evil-forged-demo.net", bridge["door"]) is None
    assert yes_mod.note_runtime_need(app_id, RESULT_HOST, "") is None, "no door, no row"
    for url in ("https://evil-forged-demo.net/x.jpg",
                f"https://evil-forged-demo.net/{RESULT_HOST}.jpg",
                f"https://{RESULT_HOST}.evil-forged-demo.net/a.jpg"):
        assert host_mod.media(app_id, url)[0] == 403
    after = S.load_build()
    assert not [r for r in after["setup"] if r.get("also")]
    assert after["jobs"][0]["status"] != S.NEEDS_SETUP


# ── a card's picture site is pictures-only, everywhere ──────────────────────

CARD_APP = "space_news_card"
DATA_HOST = "api.spacenews-demo.org"
CARD_PIC = "images.pics-demo.net"


@pytest.fixture
def card_door(bridge):
    ed.register_app_doors([(CARD_APP, {"hosts": [DATA_HOST], "picture_hosts": [CARD_PIC],
                                       "name": "Space News", "purpose": "x",
                                       "data_sent": "x"})])
    ed.set_app_door_consent(CARD_APP, True)
    return bridge


def test_a_card_picture_site_answers_pictures_only(state, card_door):
    d = ed.app_door(CARD_APP)
    assert d.hosts == (DATA_HOST,) and d.picture_hosts == (CARD_PIC,)
    # Her yes for the old full-host set still counts (same sites, same flag).
    assert d.config_flag == ed.app_door_flag(CARD_APP, [DATA_HOST, CARD_PIC])
    got = ed.app_egress_get(CARD_APP, f"https://{CARD_PIC}/a.jpg", timeout=5)
    assert got.status_code == 200 and got.content == _PicResp()._body
    for bad in (lambda: ed.egress_request(ed.app_door_id(CARD_APP), "POST",
                                          f"https://{CARD_PIC}/upload"),
                lambda: ed.app_egress_get(CARD_APP, f"https://{CARD_PIC}/a.jpg",
                                          params={"q": "x"})):
        with pytest.raises(ed.EgressBlocked):
            bad()
    sent = len(card_door["sent"])
    card_door["answer"]["resp"] = _PicResp("text/html", b"<html>")
    with pytest.raises(ed.EgressBlocked) as err:
        ed.app_egress_get(CARD_APP, f"https://{CARD_PIC}/page", timeout=5)
    assert err.value.code == "not_a_picture"
    card_door["answer"]["resp"] = _PicResp("image/svg+xml", b"<svg/>")
    with pytest.raises(ed.EgressBlocked):
        ed.app_egress_get(CARD_APP, f"https://{CARD_PIC}/a.svg", timeout=5)
    card_door["answer"]["resp"] = _PicResp(body=b"x" * (ed.RESULT_PICTURE_MAX_BYTES + 1))
    with pytest.raises(ed.EgressBlocked):
        ed.app_egress_get(CARD_APP, f"https://{CARD_PIC}/huge.jpg", timeout=5)
    # Its data site is a full site, as before.
    card_door["answer"]["resp"] = _PicResp("application/json", b"{}")
    assert ed.app_egress_get(CARD_APP, f"https://{DATA_HOST}/v4", timeout=5)         .status_code == 200
    assert len(card_door["sent"]) == sent + 4


def test_feeds_register_a_cards_picture_site_as_pictures_only(state, monkeypatch):
    from puffpai import feeds
    manifest = {"id": CARD_APP, "name": "Space News"}
    feed = {"service": {"name": "Space News", "host": DATA_HOST, "image_host": CARD_PIC}}
    monkeypatch.setattr(feeds, "feed_apps", lambda manifests=None: [(manifest, feed)])
    feeds.sync_doors()
    d = ed.app_door(CARD_APP)
    assert d.hosts == (DATA_HOST,) and d.picture_hosts == (CARD_PIC,)
    ed.register_app_doors([])
