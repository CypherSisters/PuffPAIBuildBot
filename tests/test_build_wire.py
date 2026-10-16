"""The one build foundation, wired in; old paths gone.

1. The wizard plans through ``build.pipeline.plan(goal, ctx)`` and makes no
   live card before (or instead of) it.
2. Old ``feed-*`` / ``app-*`` / ``data-*`` thing pages send the owner to the
   job that uses them, else to the "Not used" list.
3. A live card's row links are web pages, never the API record the row came
   from (StargazerBot: "Read" on a launch showed raw data from ll.thespacedevs.com).
4. One publisher rule: what a skill's plan offers is exactly what the
   installer accepts (News: "One yes installs: summarizer, scrape, reading"
   next to "not from the same publisher, so BuildBot did not install it").

Nothing here reaches the network.
"""
from __future__ import annotations

import json
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_factory_wiring import G4, garden_machine, _installed_and_allowed, _record  # noqa: E402,F401

from puffpai import deps, feeds, skills, things  # noqa: E402
from puffpai.config import get_state_dir  # noqa: E402
from puffpai.finding import install  # noqa: E402


# ── 1. the wizard plans through the one pipeline ────────────────────────────

def _fake_pipeline(monkeypatch, calls: list):
    """A stand-in ``puffpai.build.pipeline`` that notes what was
    already made when it was asked to plan."""
    pkg = types.ModuleType("puffpai.build")
    pkg.__path__ = []
    mod = types.ModuleType("puffpai.build.pipeline")

    def plan(goal, ctx):
        calls.append({"goal": goal, "feeds_made": [m.get("id") for m, _f in feeds.feed_apps()],
                      "apps_open": _installed_and_allowed()})
        record = {"schema": "build/v1",
                  "jobs": [{"id": "garden_weather", "name": "Weather for the beds",
                            "status": "needs_setup", "ingredients": ["seed:open_meteo"]}],
                  "setup": [{"kind": "setting", "name": "Your home town"}]}
        os.makedirs(os.path.join(get_state_dir(), "build"), exist_ok=True)
        with open(os.path.join(get_state_dir(), "build", "build.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(record, fh)
        return record
    mod.plan = plan
    pkg.pipeline = mod
    monkeypatch.setitem(sys.modules, "puffpai.build", pkg)
    monkeypatch.setitem(sys.modules, "puffpai.build.pipeline", mod)


def test_the_wizard_plans_through_the_pipeline_and_makes_no_live_card_first(
        garden_machine, monkeypatch):
    from puffpai import api_server
    from puffpai.assemble import plan as old_plan
    calls: list = []
    _fake_pipeline(monkeypatch, calls)
    monkeypatch.setattr(old_plan, "build_plan",
                        lambda *a, **k: pytest.fail("the older Assemble plan ran"))
    os.environ.setdefault("PUFFPAI_API_KEY", "e" * 64)
    web = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:goal", "answers": {"goal": G4}}).status_code == 200
    assert web.post("/wizard/apply", headers=auth, json={
        "skill": "purpose:identity", "answers": {"age": "40"}}).status_code == 200
    summary = web.post("/wizard/summary", headers=auth, json={}).json
    assert not [i for s in summary["sections"] if s["id"] == "live_info" for i in s["items"]]
    assert web.post("/wizard/approve", headers=auth, json={}).status_code == 200
    web.post("/wizard/vault/skip", headers=auth, json={})
    run = web.post("/wizard/run", headers=auth, json={}).json
    assert run["complete"] is True, run.get("outstanding")
    assert len(calls) == 1 and calls[0]["goal"] == G4
    # Nothing live was made before the plan, and nothing after it either.
    assert calls[0]["feeds_made"] == [] and calls[0]["apps_open"] == []
    assert garden_machine == [] and _installed_and_allowed() == []
    record = _record()
    assert record["shared"]["build_plan"] == {"jobs": 1, "setup": 1}
    assert not [r for r in record["shared"]["factory"]["items"] if r["kind"] == "live_feed"]
    assert record["entries"]["assemble.plan"]["outcome"] == "done"


def test_the_factory_and_summary_have_no_live_card_path():
    from puffpai.wizard import factory, proposal
    assert "factory:live_info" not in dict(factory.PROVIDERS)
    assert "factory:live_info" not in dict(proposal._BUILTIN)  # noqa: SLF001
    for gone in ("live_items", "live_candidates", "_add_live"):
        assert not hasattr(factory, gone), gone


# ── 2. old pages go to the job that uses them ───────────────────────────────

FEED = {"id": "feed-ll2", "kind": "feed", "name": "Launch Library 2", "icon": "x",
        "blurb": "Launches.", "from": "https://ll.thespacedevs.com/", "ref": "ll2"}
APP = {"id": "app-space-encyclopedia-rckieu5r", "kind": "app", "name": "Space Encyclopedia",
       "icon": "x", "blurb": "Search.", "from": "", "ref": "space-encyclopedia-rckieu5r"}
LOOSE = {"id": "feed-horoscope", "kind": "feed", "name": "Daily Horoscope", "icon": "x",
         "blurb": "Stars.", "from": "", "ref": "horoscope"}


@pytest.fixture
def space_things(monkeypatch):
    from puffpai.integrate import records
    monkeypatch.setattr(things, "_feeds", lambda: [dict(FEED), dict(LOOSE)])
    monkeypatch.setattr(things, "_integrations", lambda: [dict(APP)])
    monkeypatch.setattr(records, "get", lambda ref: {
        "app_id": "space-encyclopedia-islim6jk"} if ref == APP["ref"] else None)


def _write_build(jobs):
    os.makedirs(os.path.join(get_state_dir(), "build"), exist_ok=True)
    with open(os.path.join(get_state_dir(), "build", "build.json"), "w", encoding="utf-8") as fh:
        json.dump({"schema": "build/v1", "jobs": jobs, "setup": [], "dropped": []}, fh)


def test_an_old_page_goes_to_the_job_that_uses_it(space_things):
    _write_build([
        {"id": "launches", "name": "Upcoming launches", "ingredients": ["feed:ll2"]},
        # The web app named by its own app id (the second of its two ids).
        {"id": "space_facts", "name": "Space facts",
         "ingredients": [{"ref": "webapp:space-encyclopedia-islim6jk"}]}])
    assert things.page("feed-ll2") == {"id": "feed-ll2", "redirect": "/dashboard/jobs/launches"}
    assert things.page(APP["id"])["redirect"] == "/dashboard/jobs/space_facts"
    listed = {t["id"]: t["page"] for t in things.listing()}
    assert listed["feed-ll2"] == "/dashboard/jobs/launches"
    assert things.ingredient_row("feed-ll2")["job"] == "launches"


def test_a_thing_no_job_uses_goes_to_not_used(space_things):
    _write_build([{"id": "launches", "ingredients": ["feed:ll2"]}])
    assert things.page("feed-horoscope")["redirect"] == things.NOT_USED_PAGE
    assert things.ingredient_row("feed-horoscope")["job"] == ""
    assert things.page("feed-nothing-here") is None     # not there: 404, no redirect


def test_skills_books_and_bots_without_a_build_record_keep_their_pages(space_things):
    assert things.redirect_for("feed-ll2") is None      # no build.json yet: old pages
    _write_build([{"id": "launches", "ingredients": ["feed:ll2", "skill:news"]}])
    assert things.redirect_for("skill-news") is None    # a skill still has its page
    assert things.redirect_for("book-x") is None


def test_after_an_install_the_page_address_is_the_job(space_things, monkeypatch):
    _write_build([{"id": "launches", "ingredients": ["integration:space-encyclopedia-rckieu5r"]}])
    monkeypatch.setattr(things, "make_recipe", lambda tid: pytest.fail("no page is made"))
    got = things.after_install({"kind": "app", "integration": APP["ref"]})
    assert got == "/dashboard/jobs/launches"


# ── 3. row links are web pages ──────────────────────────────────────────────

LL2_CARD = {"kind": "list", "title": "Launches", "items": "results", "item_title": "name",
            "max_items": 5}


def _ll2_feed(card=None):
    return {"service": {"name": "Launch Library 2", "host": "ll.thespacedevs.com"},
            "url": "https://ll.thespacedevs.com/2.3.0/launches/upcoming/?limit=5&mode=list",
            "refresh_minutes": 60, "max_age_minutes": 1440, "card": card or LL2_CARD,
            "credit": "The Space Devs", "license": "terms on its website"}


def test_a_row_whose_address_is_the_api_record_gets_no_link():
    rows = {"results": [{"name": "Electron", "url":
                         "https://ll.thespacedevs.com/2.3.0/launches/9f2b0c/"}]}
    item = feeds.extract(rows, _ll2_feed())["items"][0]
    assert item["title"] == "Electron" and "link" not in item


def test_a_seed_declared_page_on_the_services_site_is_the_link():
    card = dict(LL2_CARD, item_link="https://thespacedevs.com/launch/{slug}")
    feed = _ll2_feed(card)
    assert feeds.validate_feed("x", feed) is None
    item = feeds.extract({"results": [{"name": "Electron", "slug": "electron-1"}]}, feed)["items"][0]
    assert item["link"] == "https://thespacedevs.com/launch/electron-1"
    api = _ll2_feed(dict(LL2_CARD, item_link="https://ll.thespacedevs.com/2.3.0/launches/{id}/"))
    assert feeds.validate_feed("x", api) is not None


@pytest.mark.parametrize("url,is_api", [
    ("https://ll.thespacedevs.com/2.3.0/launches/abc/", True),
    ("https://api.deezer.com/album/302127", True),
    ("https://images-api.nasa.gov/search?q=mars", True),
    ("https://musicbrainz.org/ws/2/release-group/1", True),
    ("https://example.org/data/latest.json", True),
    ("https://example.org/list?format=json", True),
    ("https://www.deezer.com/album/302127", False),
    ("https://musicbrainz.org/release-group/1b022e01", False),
    ("https://example.org/2024/10/a-story", False),
])
def test_an_address_for_programs_is_told_apart_from_a_page(url, is_api):
    assert feeds.api_address(url) is is_api


def test_every_seed_link_is_a_web_page():
    from puffpai.catalogs import api_seeds
    seen = 0
    for row in api_seeds.load_rows():
        link = (row.get("card") or {}).get("item_link")
        if isinstance(link, str) and "{" in link:
            seen += 1
            assert feeds.link_problem(link, [row["host"]]) is None, row["id"]
            assert not feeds.api_address(link.replace("{", "").replace("}", "")), row["id"]
    assert seen, "a seed declares a page template"


# ── 4. one publisher rule ───────────────────────────────────────────────────

NEWS = """---
name: news
description: Read the news.
---
## Related skills

`summarizer`, `scrape`; `reading` (read it aloud).
"""
ORIGINS = [
    {"source": "clawhub", "owner": "newsdesk"},
    {"source": "clawhub", "owner": ""},                 # a short id, locked with no owner
    {"source": "github", "repo": "acme/skills", "folder": "skills/news"},
    {"source": "elsewhere"},
    {},
]


@pytest.mark.parametrize("origin", ORIGINS, ids=["owner", "no-owner", "github", "other", "none"])
def test_the_plan_offers_exactly_what_the_installer_accepts(origin):
    offered = [s["name"] for s in deps.read(NEWS, origin=origin)["skills"]]
    accepted = [n for n in deps.siblings_in(NEWS, "news")
                if install._companion_find(origin, n) is not None]  # noqa: SLF001
    assert offered == accepted
    assert bool(offered) is (deps.publisher(origin) is not None)


@pytest.mark.parametrize("owner", ["newsdesk", ""])
def test_a_skill_page_plan_and_its_install_agree(owner, monkeypatch):
    folder = os.path.join(skills.skills_dir(), "news")
    os.makedirs(folder)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write(NEWS)
    with open(os.path.join(skills.skills_dir(), ".lock.json"), "w", encoding="utf-8") as fh:
        json.dump({"news": {"source": "clawhub", "owner": owner, "slug": "news"}}, fh)
    plan = deps.make_plan("news")
    promised = [s["name"] for s in plan["steps"] if s["kind"] == "skill"]
    monkeypatch.setattr(install, "_install_skill", lambda f, say, sub, **kw: {"ok": True})
    monkeypatch.setattr(install.find_mod, "remember", lambda finds: None)
    from puffpai.finding import sites
    monkeypatch.setattr(sites, "clawhub_verify", lambda slug, owner="": {"version": "1"})
    lines: list = []
    refused = [n for n in deps.siblings_in(NEWS, "news")
               if not install.install_companion("news", n, lines.append).get("ok")]
    assert promised == ([] if not owner else ["summarizer", "scrape", "reading"])
    assert not set(promised) & set(refused), "the plan offered what the installer refused"


def test_a_short_clawhub_id_gets_its_owner_recorded(monkeypatch):
    from puffpai.finding import sites
    monkeypatch.setattr(sites, "clawhub_verify", lambda slug, owner="": {
        "verdict": "clean", "version": "1.0.0", "files": [], "summary": "",
        "page": f"https://{sites.CLAWHUB}/newsdesk/skills/{slug}"})
    asked = []
    monkeypatch.setattr(install, "clawhub_skill", lambda slug, owner, v: asked.append(owner) or {
        "ok": True, "files": {"SKILL.md": NEWS.encode()}, "zip_sha256": "0" * 64})
    monkeypatch.setattr(install, "_switch_off_line", lambda f, s: None)
    monkeypatch.setattr(install, "install_companion", lambda *a, **k: {"ok": False})
    find = {"id": "clawhub:news", "name": "news", "type": "skill", "can": "install",
            "site_name": "ClawHub", "url": f"https://{sites.CLAWHUB}", "raw": {}}
    lines: list = []
    out = install._install_skill(find, lines.append, [])  # noqa: SLF001
    assert out["ok"], lines
    assert install._read_lock()["news"]["owner"] == "newsdesk"  # noqa: SLF001
    assert asked == ["newsdesk"]
    # Its page now offers the siblings, and the installer agrees.
    assert [s["name"] for s in deps.read_skill("news")["skills"]] == [
        "summarizer", "scrape", "reading"]


# ── 5. an older bot gets its build record at start, with the brain ─────────

@pytest.mark.parametrize("exists,old,expect", [(False, True, 1), (True, True, 0), (False, False, 0)])
def test_the_backend_migrates_an_older_bot_once_with_the_brain(monkeypatch, exists, old, expect):
    from puffpai import api_server
    asked: list = []
    pkg = types.ModuleType("puffpai.build")
    pkg.__path__ = []
    mig = types.ModuleType("puffpai.build.migrate")
    mig.has_old = lambda: old
    mig.ensure = lambda ctx=None: asked.append(ctx) or {}
    sch = types.ModuleType("puffpai.build.schema")
    sch.exists = lambda: exists
    pkg.migrate, pkg.schema = mig, sch
    for name, mod in (("puffpai.build", pkg), ("puffpai.build.migrate", mig),
                      ("puffpai.build.schema", sch)):
        monkeypatch.setitem(sys.modules, name, mod)
    api_server.BUILD_MIGRATION_DONE.clear()
    api_server._migrate_build_at_start()  # noqa: SLF001
    assert asked == [{"use_brain": True}] * expect
    assert api_server.BUILD_MIGRATION_DONE.is_set()


def test_a_live_card_made_for_a_website_row_opens_its_job(tmp_path, monkeypatch):
    """things/feed-<card> of a website row a job uses goes to that job, by
    the card the Yes made for it (part ``live_card``) or the site it reads."""
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path))
    from puffpai import things
    record = {"jobs": [{"id": "earth-views", "ingredients": ["seed:nasa_epic"]}],
              "parts": {"seed:nasa_epic": {"live_card": "nasa_epic_earth_photos",
                                           "hosts": ["epic.gsfc.nasa.gov"]}}}
    assert things.owning_job("feed-nasa_epic_earth_photos", record, thing={})["id"] == \
        "earth-views"
    record["parts"]["seed:nasa_epic"]["live_card"] = ""
    apps = tmp_path / "apps" / "old_epic_card"
    apps.mkdir(parents=True)
    (apps / "app.json").write_text('{"feed": {"service": {"host": "epic.gsfc.nasa.gov"}}}')
    assert things.owning_job("feed-old_epic_card", record, thing={})["id"] == "earth-views"
    assert things.owning_job("feed-something_else", record, thing={}) is None
