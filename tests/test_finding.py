"""The find step: find_things over the shelves and code sites, install_thing,
undo, the skills folder, the chat tools and the routes. Recorded answers only
(tests/fixtures/finding, recorded 2026-10-04): no test goes online.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""

from __future__ import annotations

import io
import json
import os
import re
import types
import zipfile

import pytest

from finding_fakes import COMMIT, Sites


@pytest.fixture
def sites(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai.finding import hermes_index, net
    fake = Sites()
    monkeypatch.setattr(net, "_send", fake)
    hermes_index.forget()
    yield fake
    hermes_index.forget()


def _all(result):
    return [f for job in result["jobs"] for t in job["types"] for f in t["finds"]]


# ── finding ────────────────────────────────────────────────────────────────

def test_find_things_searches_shelves_and_code_sites(sites):
    from puffpai.finding import find
    result = find.find_things("wedding planner")
    finds = _all(result)
    by_site = {f["site"] for f in finds}
    assert {"clawhub", "skills.sh", "github", "codeberg", "npm",
            "mcp-registry"} <= by_site
    # The Hermes list is asked too; its one answer here is about marketing
    # portfolios, not weddings, so it is left out (find.on_subject).
    assert "nousresearch.github.io" in sites.hosts_asked()
    types_seen = {f["type"] for f in finds}
    assert "skill" in types_seen and "mcp" in types_seen
    assert types_seen - {"skill", "mcp"}, "apps and code are found too"
    for f in finds:
        assert f["type_words"] in ("skill", "browser app", "app that needs its own server",
                                   "command-line tool", "code library", "MCP tool server",
                                   "data/template")
        assert "raw" not in f
        assert f["type_words"] in f["line"]
        assert not f.get("ticked")
    # one plain list grouped by job, then type (shelves before code)
    job = result["jobs"][0]
    assert job["name"] == "wedding planner"
    assert [t["type"] for t in job["types"]][0] == "skill"


def test_clawhub_check_is_read_and_shown(sites):
    from puffpai.finding import find
    finds = _all(find.find_things("wedding planner"))
    wp = next(f for f in finds if f["id"] == "clawhub:ivangdavila/wedding-planner")
    assert wp["verdict"] == "benign"
    assert "ClawHub check: passed" in wp["line"]
    assert "MIT-0" in wp["line"] and "downloads" in wp["line"]


def test_each_site_keeps_its_own_order_and_nothing_is_hidden(sites):
    from puffpai.finding import find, sites as S
    raw = json.loads((__import__("finding_fakes").FIX / "github__3d_printer.json").read_text(
        encoding="utf-8"))
    order = [f"github:{it['full_name']}" for it in raw["items"]]
    got = [f["id"] for f in S.github("3d printer")]
    assert got == order                       # GitHub's best-match order, all of them
    assert any(not f["popularity"].get("stars") for f in S.codeberg("3d printer")), \
        "a project with no stars is still shown"
    shown = [f["id"] for f in _all(find.find_things("3d printer")) if f["site"] == "github"]
    assert set(shown) == set(order[:find.SHOW_PER_SITE])   # GitHub's top ones, by type
    for t in find.find_things("3d printer")["jobs"][0]["types"]:
        mine = [f["id"] for f in t["finds"] if f["site"] == "github"]
        assert mine == sorted(mine, key=order.index)


def test_kinds_ask_only_the_sites_for_that_kind(sites):
    from puffpai.finding import find
    result = find.find_things("wedding planner", kinds=["skill"])
    assert {f["type"] for f in _all(result)} == {"skill"}
    assert "api.github.com" not in sites.hosts_asked()
    assert "registry.npmjs.org" not in sites.hosts_asked()


def test_each_shelf_goes_under_its_own_switch(sites):
    from puffpai.finding import find
    find.find_things("wedding planner")
    assert sites.services_of("clawhub.ai") == {"shelf_clawhub"}
    assert sites.services_of("skills.sh") == {"shelf_skills_sh"}
    assert sites.services_of("nousresearch.github.io") == {"shelf_hermes"}
    assert sites.services_of("registry.modelcontextprotocol.io") == {"shelf_mcp_registry"}
    assert sites.services_of("api.github.com") == {"code_search"}


def test_a_shelf_switched_off_is_not_asked(sites):
    from puffpai.finding import find
    from puffpai.security import outside_switches
    outside_switches.set_switch("shelf_clawhub", False)
    result = find.find_things("wedding planner")
    assert "clawhub.ai" not in sites.hosts_asked()
    assert any("ClawHub is switched off" in n for n in result["notes"])


def test_a_site_that_does_not_answer_is_said(sites, monkeypatch):
    from puffpai.finding import find, net
    real = sites

    def flaky(url, **kw):
        if "codeberg.org" in url:
            return {"status": None, "body": None, "reason": "no_answer", "headers": {}}
        return real(url, **kw)
    monkeypatch.setattr(net, "_send", flaky)
    result = find.find_things("wedding planner")
    assert any("Codeberg did not answer" in n for n in result["notes"])


def test_find_for_goal_groups_by_her_jobs(sites):
    from puffpai.finding import find

    def asker(goal):
        assert goal == "a wedding planner bot"
        return {"kind": "wedding planner", "jobs": [
            {"name": "Plan the budget", "parts": [{"kind": "code-app",
                                                    "search": ["stock portfolio"]}]},
            {"name": "Print the place cards", "parts": [{"kind": "code-app",
                                                          "search": ["3d printer"]}]}]}
    result = find.find_for_goal("a wedding planner bot", asker=asker)
    names = [j["name"] for j in result["jobs"]]
    assert names == ["Everything for a wedding planner bot", "Plan the budget",
                     "Print the place cards"]
    ids = [f["id"] for f in _all(result)]
    assert len(ids) == len(set(ids)), "a find is shown once"


def test_find_for_goal_without_a_brain_still_looks(sites):
    from puffpai.finding import find
    result = find.find_for_goal("wedding planner", asker=lambda g: None)
    assert len(result["jobs"]) == 1 and result["count"] > 0


def test_finance_finds_mark_trading_and_suspicious(sites):
    from puffpai.finding import find
    finds = _all(find.find_things("stock portfolio"))
    sa = [f for f in finds if f["id"] == "clawhub:udiedrichsen/stock-analysis"]
    if sa:
        assert "suspicious" in sa[0]["line"]
    from puffpai.finding import sites as S
    f = S.make(site="npm", type_="mcp", id="npm:x", name="Robinhood trading tool",
               what="Place orders on your brokerage account", can="add_mcp")
    assert f["can_trade"] and "trades with real money" in f["line"]


def test_a_clawhub_skill_from_the_hermes_list_is_shown_once():
    from puffpai.finding import find
    seen = {"clawhub:g9pedro/bambu"}
    assert find._same_clawhub_skill("clawhub:bambu", seen)  # noqa: SLF001
    assert not find._same_clawhub_skill("clawhub:bambu-local", seen)  # noqa: SLF001
    assert not find._same_clawhub_skill("clawhub:other/bambu", seen)  # noqa: SLF001


def test_hermes_list_ranks_the_hermes_way():
    from puffpai.finding import hermes_index
    rows = [{"name": "my-stocks-helper", "description": "", "tags": [], "identifier": "a"},
            {"name": "stock tools", "description": "", "tags": [], "identifier": "b"},
            {"name": "x", "description": "about stocks", "tags": [], "identifier": "c"},
            {"name": "stocks", "description": "", "tags": [], "identifier": "d"},
            {"name": "stocksy", "description": "", "tags": [], "identifier": "e"}]
    got = [r["identifier"] for r in hermes_index.rank(
        rows, "stocks", lambda r: (r["name"], r["description"], r["tags"], r["identifier"]))]
    assert got == ["d", "e", "a", "c"]


def test_hermes_official_skill_is_installable(sites):
    from puffpai.finding import hermes_index
    finds = hermes_index.search("stocks")
    off = [f for f in finds if f["id"] == "hermes:official/finance/stocks"]
    assert off and off[0]["can"] == "install"
    assert "made by the Hermes team" in off[0]["line"]


def test_mcp_registry_words_and_latest_only(sites):
    from puffpai.finding import sites as S
    assert S.mcp_words("wedding planner") == ["wedding-planner", "wedding"]
    finds = S.mcp_registry("3d printer")
    names = [f["id"] for f in finds]
    assert len(names) == len(set(names))
    start = S.mcp_start({"remotes": [{"type": "streamable-http", "url": "https://x.example/mcp",
                                      "headers": [{"name": "Authorization",
                                                   "isRequired": True}]}]})
    assert not start["ok"] and "key" in start["why"]
    start = S.mcp_start({"packages": [{"registryType": "pypi", "identifier": "qidi-mcp",
                                       "version": "1.0", "transport": {"type": "stdio"}}]})
    assert start["ok"] and start["command"] == "uvx qidi-mcp==1.0"


def test_repo_types_from_what_the_site_says():
    from puffpai.finding import sites as S
    assert S.repo_type("home-assistant-mcp", "", [], "TypeScript") == "mcp"
    assert S.repo_type("budget", "self-hosted budget", ["self-hosted"], "Go") == "server_app"
    assert S.repo_type("wedding-checklist", "", ["template"], "HTML") == "data"
    assert S.repo_type("planner", "", [], "JavaScript") == "browser_app"
    assert S.repo_type("printer", "", [], "C") == "cli"
    assert S.repo_type("data", "", [], None) == "data"


def test_pypi_exact_names(sites):
    from puffpai.finding import sites as S
    assert S.pypi_names("wedding planner") == ["wedding-planner", "wedding"]
    got = S.pypi("octoprint")
    assert got and got[0]["id"] == "pypi:OctoPrint"


# ── installing ─────────────────────────────────────────────────────────────

def test_preview_shows_what_it_is_runs_and_the_check(sites):
    from puffpai.finding import find, install
    find.find_things("wedding planner", kinds=["skill"])
    p = install.preview("clawhub:ivangdavila/wedding-planner")
    text = "\n".join(p["lines"])
    assert p["ok"] and not p["refuse"] and p["question"]
    assert "It holds no scripts" in text
    assert "ClawHub check: passed" in text
    assert "https://clawhub.ai/ivangdavila/skills/wedding-planner" in text
    assert p["version"] == "1.0.0"


def test_install_a_clawhub_skill_pinned_then_undo(sites):
    from puffpai import skills
    from puffpai.finding import find, install
    find.find_things("wedding planner", kinds=["skill"])
    out = install.install_thing("clawhub:ivangdavila/wedding-planner")
    assert out["status"] == "installed", out
    folder = out["folder"]
    rows = skills.installed()
    assert [r["folder"] for r in rows] == [folder]
    lock = json.loads(open(os.path.join(skills.skills_dir(), ".lock.json"),
                           encoding="utf-8").read())
    assert lock[folder]["version"] == "1.0.0" and lock[folder]["source"] == "clawhub"
    assert re.fullmatch(r"[0-9a-f]{64}", lock[folder]["files"]["SKILL.md"])
    assert folder in skills.prompt_block()
    view = skills.view(folder)
    assert view["ok"] and "wedding" in view["text"].lower()
    entry = out["entry"]
    assert install.installed()[0]["entry"] == entry
    assert install.undo(entry)["ok"]
    assert skills.installed() == []
    assert skills.prompt_block() == skills.PROMPT_NONE


def test_files_not_matching_the_checked_version_are_refused(sites, monkeypatch):
    from puffpai.finding import find, install, sites as S
    find.find_things("wedding planner", kinds=["skill"])
    real = S.clawhub_verify

    def changed(slug, owner=""):
        v = real(slug, owner)
        v["files"] = [dict(x, sha256="0" * 64) for x in v["files"]]
        return v
    monkeypatch.setattr(S, "clawhub_verify", changed)
    out = install.install_thing("clawhub:ivangdavila/wedding-planner")
    assert out["status"] == "failed" and "version ClawHub checked" in out["line"]


def test_only_malicious_is_refused(sites, monkeypatch):
    from puffpai import skills
    from puffpai.finding import find, install, sites as S
    find.find_things("wedding planner", kinds=["skill"])
    monkeypatch.setattr(S, "clawhub_verify", lambda slug, owner="": {
        "decision": "fail", "verdict": "malicious", "summary": "", "version": "1.0.0",
        "files": []})
    out = install.install_thing("clawhub:ivangdavila/wedding-planner")
    assert out["status"] == "refused" and "malicious" in out["line"]
    assert skills.installed() == []
    monkeypatch.setattr(S, "clawhub_verify", lambda slug, owner="": {
        "decision": "fail", "verdict": "suspicious", "summary": "", "version": "1.0.0",
        "files": []})
    out = install.install_thing("clawhub:ivangdavila/wedding-planner")
    assert out["status"] == "installed"
    assert any("suspicious" in line for line in out["lines"])


def test_install_a_skills_sh_skill_from_github_at_one_commit(sites):
    from puffpai import skills
    from puffpai.finding import find, install
    find.remember([__import__("puffpai.finding.sites", fromlist=["make"]).make(
        site="skills.sh", type_="skill", id="skills.sh:earthtojake/text-to-cad/bambu-labs",
        name="bambu-labs", can="install", raw={"repo": "earthtojake/text-to-cad",
                                               "skill": "bambu-labs"})])
    p = install.preview("skills.sh:earthtojake/text-to-cad/bambu-labs")
    assert p["commit"] == COMMIT
    assert any("Websites it names" in line or "Files:" in line for line in p["lines"])
    out = install.install_thing("skills.sh:earthtojake/text-to-cad/bambu-labs")
    assert out["status"] == "installed", out
    folder = out["folder"]
    files = skills.files_of(os.path.join(skills.skills_dir(), folder))
    assert "SKILL.md" in files and "LICENSE" in files and "agents/openai.yaml" in files
    lock = json.loads(open(os.path.join(skills.skills_dir(), ".lock.json"),
                           encoding="utf-8").read())
    assert lock[folder]["commit"] == COMMIT
    for _service, url in sites.calls:
        if "raw.githubusercontent.com" in url:
            assert f"/{COMMIT}/" in url, "every file is read at the one pinned commit"


def test_unzip_refuses_a_file_that_climbs_out():
    from puffpai.finding import install
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("SKILL.md", "---\nname: x\n---\nhi")
        zf.writestr("../evil.txt", "no")
    out = install.unzip(buf.getvalue())
    assert not out["ok"]


def test_what_it_runs_reads_scripts_commands_and_websites():
    from puffpai.finding import install
    md = ("Run this:\n```bash\n$ python3 bambu.py status\npip install paho-mqtt\n```\n"
          "See https://wiki.bambulab.com/en/x and https://github.com/a/b.")
    got = install.what_it_runs(md, ["SKILL.md", "bambu.py"])
    assert got["scripts"] == ["bambu.py"]
    assert got["commands"] == ["python3 bambu.py status", "pip install paho-mqtt"]
    assert got["websites"] == ["wiki.bambulab.com", "github.com"]


def _mcp_find(**server):
    from puffpai.finding import find, sites as S
    f = S.make(site="mcp-registry", type_="mcp", id="mcp:com.wedding-seat/wedding-planner",
               name="wedding-planner", can="add_mcp" if S.mcp_start(server)["ok"] else "look",
               look_why=S.mcp_start(server).get("why", ""), raw={"server": server})
    find.remember([f])
    return f


def test_install_an_mcp_server_through_the_mcp_lane_then_undo(sites, monkeypatch):
    from puffpai.finding import install
    calls = []
    fake = types.ModuleType("puffpai.mcp_client")
    fake.add_server = lambda name, target, headers=None, **kw: calls.append(
        ("add", name, target, headers)) or {"ok": True, "message": "Wedding Seat is added.",
                                            "server": {"name": name}}
    fake.remove_server = lambda name: calls.append(("remove", name))
    monkeypatch.setattr(install, "_mcp_client", lambda: fake)
    _mcp_find(remotes=[{"type": "streamable-http", "url": "https://mcp.wedding-seat.com/mcp"}])
    p = install.preview("mcp:com.wedding-seat/wedding-planner")
    assert any("mcp.wedding-seat.com" in line for line in p["lines"])
    out = install.install_thing("mcp:com.wedding-seat/wedding-planner")
    assert out["status"] == "added"
    assert calls == [("add", "wedding-planner", "https://mcp.wedding-seat.com/mcp", None)]
    assert install.undo(out["entry"])["ok"]
    assert calls[-1] == ("remove", "wedding-planner")


def test_mcp_that_needs_a_key_is_look_yourself(sites):
    from puffpai.finding import install
    _mcp_find(remotes=[{"type": "streamable-http", "url": "https://x.example/mcp",
                        "headers": [{"name": "Authorization", "isSecret": True}]}])
    out = install.install_thing("mcp:com.wedding-seat/wedding-planner")
    assert out["status"] == "look" and "look at it yourself" in out["line"]


def test_code_library_is_look_yourself_with_a_reason(sites):
    from puffpai.finding import find, install
    lib = next(f for f in _all(find.find_things("wedding planner"))
               if f["type"] in ("library", "cli", "server_app"))
    out = install.install_thing(lib["id"])
    assert out["status"] == "look" and "because" in out["line"]


def test_browser_app_goes_through_the_round3_integrate_path(sites, monkeypatch):
    from puffpai.finding import find, install, sites as S
    from puffpai.integrate import apply as r3_apply
    from puffpai.integrate import records
    f = S.make(site="github", type_="browser_app", id="github:a/planner", name="planner",
               can="integrate", raw={"item": {"full_name": "a/planner"}})
    find.remember([f])
    pinned = {"repo": {"pinned_commit": "a" * 40}, "id": "x"}
    monkeypatch.setattr(install, "_round3_found", lambda f: pinned)
    seen = {}
    card = {"code_sha": "c" * 64, "host_sha": "h" * 64,
            "lines": ["Drawbridge checked it: it passed every check.", "  npm run start"],
            "host": {"how": "node_server", "commands": ["npm run start"], "missing": []}}

    def fake_prepare(item, ctx):
        from puffpai.wizard import find_parts
        seen["found"] = find_parts.found_for(item["id"])
        seen["prepared"] = "planner-abcdefgh"
        return {"status": "checked", "integration_id": "planner-abcdefgh", "card": card}

    def fake_finish(iid, *, code_sha, host_sha, progress):
        seen["finish"] = (iid, code_sha, host_sha)
        progress("Adding planner.")
        return {"status": "added", "line": "planner is added.", "integration_id": iid}
    monkeypatch.setattr(r3_apply, "prepare_code_project", fake_prepare)
    monkeypatch.setattr(r3_apply, "prepared_card",
                        lambda iid: card if iid == seen.get("prepared") else None)
    monkeypatch.setattr(r3_apply, "finish_prepared", fake_finish)
    removed = []
    monkeypatch.setattr(records, "remove", lambda iid, by: removed.append(iid) or {})
    p = install.preview("github:a/planner")           # the one yes card
    assert "  npm run start" in p["lines"] and p["commands"] == ["npm run start"]
    assert p["question"] and seen["found"] == pinned
    out = install.install_thing("github:a/planner", via="add_page")
    assert out["status"] == "added" and seen["finish"] == ("planner-abcdefgh", "c" * 64,
                                                           "h" * 64)
    assert "Adding planner." in out["lines"]
    assert install.undo(out["entry"])["ok"] and removed == ["planner-abcdefgh"]


def test_unknown_find_changes_nothing(sites):
    from puffpai.finding import install
    out = install.install_thing("clawhub:nobody/never-shown")
    assert out["status"] == "failed" and "Nothing was changed" in out["line"]
    assert install.undo("t_0000000000")["ok"] is False


# ── skills folder ──────────────────────────────────────────────────────────

def test_skills_folder_lists_and_reads(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai import skills
    folder = os.path.join(skills.skills_dir(create=True), "garden-helper")
    os.makedirs(os.path.join(folder, "references"))
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("﻿---\nname: garden-helper\ndescription: >\n  Plan a vegetable\n"
                 "  garden.\n---\n# Garden\nRead references/soil.md\n")
    with open(os.path.join(folder, "references", "soil.md"), "w", encoding="utf-8") as fh:
        fh.write("soil facts")
    rows = skills.installed()
    assert rows[0]["description"] == "Plan a vegetable garden."
    block = skills.prompt_block()
    assert "<available_skills>" in block and "garden-helper: Plan a vegetable garden." in block
    assert skills.view("garden-helper", "references/soil.md")["text"] == "soil facts"
    assert not skills.view("garden-helper", "../../secret")["ok"]
    assert not skills.view("nope")["ok"]
    assert skills.parse_frontmatter("---\nname: a: b\n  bad: [\n---\nbody")[1] == "body"


# ── chat tools ─────────────────────────────────────────────────────────────

def test_tools_register_in_the_tool_loop_registry(sites):
    from puffpai import brain_tools
    from puffpai.finding import tools
    tools.register_all()
    got = {t.name: t for t in brain_tools.available({})}
    assert {"find_things", "install_thing", "skill_view"} <= set(got)
    assert got["find_things"].needs_yes is False
    ask = got["install_thing"].needs_yes
    assert callable(ask)
    assert got["find_things"].schema["required"] == ["words"]
    text = got["find_things"].fn({"words": "wedding planner", "kinds": ["skill"]})
    assert "[clawhub:ivangdavila/wedding-planner]" in text
    text = got["find_things"].fn(words="wedding planner", ctx=None)
    assert "install_thing" in text
    q = tools.yes_question({"id": "clawhub:ivangdavila/wedding-planner"})
    assert "ClawHub check: passed" in q and "Shall BuildBot install it?" in q
    question = ask({"id": "clawhub:ivangdavila/wedding-planner"}, {})
    assert question["title"] == "Install Wedding Planner?"
    assert "It holds no scripts" in question["detail"]
    assert ask({"id": "clawhub:nobody/never-shown"}, {}) is None
    out = got["install_thing"].fn({"id": "clawhub:ivangdavila/wedding-planner"})
    assert "installed" in out
    assert "wedding" in got["skill_view"].fn({"name": "wedding-planner"}).lower()


def test_tools_register_through_the_registry_seam(sites, monkeypatch):
    from puffpai import skills
    from puffpai.finding import tools
    got, providers = [], {}
    fake = types.ModuleType("puffpai.brain_tools")
    fake.register = lambda name, description, json_schema, fn, needs_yes=False: got.append(
        (name, needs_yes if isinstance(needs_yes, bool) else "asks"))
    fake.register_provider = lambda key, fn: providers.update({key: fn})
    fake.make = lambda name, description, json_schema, fn, needs_yes=False, **kw: {
        "name": name, "description": description, "fn": fn}
    fake.available = lambda ctx: []
    monkeypatch.setattr(tools, "registry", lambda: fake)
    tools.register_all()
    assert got == [("find_things", False), ("install_thing", "asks")]
    view = providers["skills"]({})[0]
    assert view["name"] == "skill_view" and skills.PROMPT_NONE in view["description"]
    folder = os.path.join(skills.skills_dir(create=True), "garden-helper")
    os.makedirs(folder)
    with open(os.path.join(folder, "SKILL.md"), "w", encoding="utf-8") as fh:
        fh.write("---\nname: garden-helper\ndescription: Plan a garden.\n---\nhi")
    view = providers["skills"]({})[0]      # read fresh on the next turn
    assert "garden-helper: Plan a garden." in view["description"]


# ── routes ─────────────────────────────────────────────────────────────────

@pytest.fixture
def client(sites, monkeypatch):
    from flask import Flask
    from puffpai.finding import api, jobs
    real_start = jobs.start
    monkeypatch.setattr(jobs, "start", lambda kind, work, wait=False: real_start(
        kind, work, wait=True))
    app = Flask(__name__)
    app.register_blueprint(api.bp)
    return app.test_client()


def test_routes_find_preview_install_list_undo(client):
    r = client.post("/add/things/find", json={"words": "wedding planner", "kinds": ["skill"]})
    job = r.get_json()["job"]
    st = client.get(f"/add/things/jobs/{job}").get_json()
    assert st["done"] and st["lines"] and st["result"]["jobs"]
    fid = "clawhub:ivangdavila/wedding-planner"
    p = client.post("/add/things/preview", json={"id": fid}).get_json()
    assert p["ok"] and p["question"]
    job = client.post("/add/things/install", json={"ids": [fid]}).get_json()["job"]
    st = client.get(f"/add/things/jobs/{job}").get_json()
    assert st["result"]["results"][0]["status"] == "installed"
    assert any("installed" in line for line in st["lines"])
    rows = client.get("/add/things/installed").get_json()["installed"]
    assert rows and rows[0]["type"] == "skill"
    undo = client.post(f"/add/things/installed/{rows[0]['entry']}/undo").get_json()
    assert undo["ok"]
    assert client.get("/add/things/jobs/nope").status_code == 404
    assert client.post("/add/things/find", json={}).status_code == 400


def test_nothing_ticked_installs_nothing(client):
    job = client.post("/add/things/install", json={"ids": []}).get_json()["job"]
    st = client.get(f"/add/things/jobs/{job}").get_json()
    assert st["lines"] == ["Nothing was picked, so nothing was added."]


# ── words ──────────────────────────────────────────────────────────────────

def test_every_owner_sentence_is_plain():
    from puffpai import outside_services, plain_words
    from puffpai.finding import tools, wording as W
    texts = [v for k, v in vars(W).items() if k.isupper() and isinstance(v, str)]
    texts += list(W.CAN.values()) + list(W.LOOK_WHY.values()) + list(W.TYPES.values())
    for row in outside_services.SHELF_ROWS:
        texts += list(row[1:5])
    for text in texts:
        assert "—" not in text and "–" not in text, text
        assert not re.search(r"(?i)\bdoors?\b|\bbeta\b|\bwarts?\b", text), text
    for row in outside_services.SHELF_ROWS:
        for text in row[1:5]:
            assert plain_words.is_plain(text), plain_words.check(text)
    assert "—" not in tools.FIND_DESCRIPTION + tools.INSTALL_DESCRIPTION


def test_shelf_rows_are_on_the_outside_list(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    from puffpai import outside_services, source_fetch
    ids = {r["id"]: r for r in outside_services.rows()}
    for sid in ("shelf_clawhub", "shelf_skills_sh", "shelf_hermes", "shelf_mcp_registry"):
        assert ids[sid]["can_switch"] and ids[sid]["state"] == "on"
        assert source_fetch._what_for(sid) == "shelf_search"  # noqa: SLF001
    outside_services.switch_off("shelf_hermes")
    assert outside_services.row("shelf_hermes")["state"] == "off"


def test_licence_and_month_words():
    from puffpai.finding import wording as W
    assert W.licence_words("MIT") == "MIT: free to use and change"
    assert W.licence_words("AGPL-3.0").endswith("changes must stay open")
    assert W.licence_words(None) == "no licence shown"
    assert W.month_words("2026-05-20T07:09:04Z") == "changed May 2026"
    assert W.month_words(1789319302154).startswith("changed ")
    assert W.popularity_words({"stars": 11927}) == "11,927 stars"


# ── the wizard's step ──────────────────────────────────────────────────────

def test_wizard_goes_goal_identity_then_plan_no_pick_step(sites, monkeypatch, tmp_path):
    """One build foundation: no find-and-tick step before the plan; the plan
    itself finds, and nothing is installed before the one Yes."""
    from test_wizard_needs_step import answer_questions, pinned_machine
    from puffpai.finding import install
    from puffpai.wizard import service
    pinned_machine(monkeypatch, tmp_path)
    from puffpai.finding import net
    monkeypatch.setattr(net, "_send", sites)
    service.first_look_choose()
    answer_questions("a wedding planner bot")
    assert service.flow()["next"] == "summary"
    assert not install.entries(), "nothing installed before the plan"


def test_old_wizard_find_route_still_answers(sites, monkeypatch, tmp_path):
    from test_wizard_needs_step import answer_questions, pinned_machine
    from puffpai.finding import api, jobs
    from puffpai.wizard import service
    real_start = jobs.start
    monkeypatch.setattr(jobs, "start", lambda kind, work, wait=False: real_start(
        kind, work, wait=True))
    pinned_machine(monkeypatch, tmp_path)
    from puffpai.finding import net
    monkeypatch.setattr(net, "_send", sites)      # pinned_machine moved the state folder
    service.first_look_choose()
    answer_questions("a wedding planner bot")
    view = api.wizard_state()
    assert view["title"] == "Finding things for your bot"
    assert view["find"]["done"] and view["find"]["result"]["jobs"]
    finds = [f for j in view["find"]["result"]["jobs"] for t in j["types"]
             for f in t["finds"]]
    assert finds and not any(f.get("ticked") for f in finds), "nothing is ticked for her"
    assert api.wizard_state()["find"]["job"] == view["find"]["job"], "searched once a run"
    assert api.settle("skipped")
    assert service.flow()["next"] == "summary"
    run = service._saved(service._cfg(), service._args())  # noqa: SLF001
    assert run.entries[service.NEEDS_ENTRY]["outcome"] == "skipped"


def test_api_server_registers_the_routes_and_the_tools():
    from puffpai import api_server
    from puffpai.finding import tools
    rules = {r.rule for r in api_server.app.url_map.iter_rules()}
    assert {"/add/things/find", "/add/things/jobs/<job_id>", "/add/things/preview",
            "/add/things/install", "/add/things/installed",
            "/add/things/installed/<entry>/undo", "/wizard/find"} <= rules
    tools.register_all()          # once per registry; the import above already did it
    names = {getattr(t, "name", None) or t["name"] for t in tools.registry().available({})}
    assert {"find_things", "install_thing", "skill_view"} <= names


# ── try-it fixes ────────────────────────────────────────────────

@pytest.mark.parametrize("name, what, trades", [
    ("ticker-watch", "Stock prices and market data with no brokerage account or API key.",
     False),
    ("politicians-stock-tracker", "Track congress stock trades and politician stock trades",
     False),
    ("theme-detector", "A skill in the tradermonty/claude-trading-skills project on GitHub.",
     False),
    ("Interactive Brokers Suite", "Read-only Interactive Brokers (IBKR) diagnostics", False),
    ("Portfolio", "Manage investment portfolios. Use when adding positions.", False),
    ("Investing Analyst", "Thesis-driven equity and options trading analyst.", True),
    ("trading-discord-bot", "Bot for discord built around trading and investing", True),
    ("Engine", "portfolio risk management, and trade execution across asset classes", True),
    ("@pipedream/alpaca", "Pipedream Alpaca Components", True),
    ("Robinhood trading tool", "Place orders on your brokerage account", True),
])
def test_trading_label_is_for_things_that_place_trades(name, what, trades):
    from puffpai.finding import wording as W
    assert W.can_trade(name, what) is trades


def test_trading_things_are_not_offered_by_default(monkeypatch, tmp_path):
    from puffpai.finding import find, sites as S
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    made = [S.make(site="clawhub", type_="skill", id="clawhub:a/portfolio", name="Portfolio",
                   what="Track holdings.", can="install"),
            S.make(site="clawhub", type_="skill", id="clawhub:b/bot", name="Auto trader",
                   what="Auto-trade stocks with Alpaca.", can="install")]
    monkeypatch.setattr(find, "_SEARCHERS", {s: (lambda w, n: list(made)) if s == "clawhub"
                                             else (lambda w, n: []) for s in find.ALL_SITES})
    monkeypatch.setattr(find, "_add_verdicts", lambda finds: None)
    monkeypatch.setattr(find, "_switched_off", lambda site: False)
    got = find.find_things("stock portfolio")
    ids = [f["id"] for f in _all(got)]
    assert ids == ["clawhub:a/portfolio"]
    assert any("trades with real money" in n for n in got["jobs"][0]["notes"])
    got = find.find_things("stock trading")
    assert "clawhub:b/bot" in [f["id"] for f in _all(got)]


def test_every_code_project_can_be_added_with_or_without_a_licence():
    """Live 2026-10-14: one-small-step (no licence) showed only "Look at it
    yourself". Approval, not refusal: every code project gets Add; with no
    licence one plain line says it is her call."""
    from puffpai.finding import sites as S
    from puffpai.finding import wording as W
    for kind in ("cli", "library", "server_app", "browser_app", "data"):
        assert S._code_can(kind, "MIT") == ("integrate", ""), kind
        for none in (None, "", "none"):
            assert S._code_can(kind, none) == ("integrate", W.NO_LICENCE_LINE), kind
    assert S._code_can("browser_app", "NOASSERTION") == ("integrate", "")
    assert W.NO_LICENCE_LINE == ("Its maker has not said others may copy it. Using it on "
                                 "your own computer is your call.")
    f = S.make(site="github", type_="browser_app", id="github:branjames117/one-small-step",
               name="one-small-step", what="ISS tracker", licence=None, can="integrate",
               look_why=W.NO_LICENCE_LINE)
    assert f["no_licence"] is True and f["licence_words"] == "no licence shown"
    assert "your call" in f["can_words"] and "look at it yourself" not in f["line"].lower()
    # Skills and tool servers keep their own ways in, and say what to do.
    assert S._code_can("skill", "MIT") == ("look", W.LOOK_WHY["skill"])
    assert S._code_can("mcp", "MIT") == ("look", W.LOOK_WHY["mcp"])
    assert "Add a tool server" in W.LOOK_WHY["mcp"]


def test_npm_packages_must_mention_the_words():
    from puffpai.finding import sites as S
    assert not S._mentions("wedding guest list", "axios", "Promise based HTTP client", [])
    assert S._mentions("wedding guest list", "guestbook", "Guest list for a wedding", [])
    assert S._mentions("3d printing filament", "x", "", ["filaments"])


def test_chat_find_text_groups_by_type():
    from puffpai.finding import find, sites as S
    a = S.make(site="clawhub", type_="skill", id="clawhub:a/x", name="x", can="install")
    b = S.make(site="github", type_="browser_app", id="github:a/y", name="y", can="look")
    result = {"jobs": [find._group(find._job(1, "wedding", "wedding"), [a, b])], "notes": []}
    text = find.as_text(result)
    assert "  skill:" in text and "  browser app:" in text
    assert "AND the apps" in text


def test_clawhubs_own_meta_file_does_not_fail_the_check(sites, monkeypatch):
    from puffpai.finding import find, install, sites as S
    find.find_things("wedding planner", kinds=["skill"])
    real_verify, real_skill = S.clawhub_verify, install.clawhub_skill

    def verify(slug, owner=""):
        v = real_verify(slug, owner)
        v["files"] = list(v["files"]) + [{"path": "_meta.json", "size": 134,
                                          "sha256": "1" * 64}]
        return v

    def skill(slug, owner, version):
        got = real_skill(slug, owner, version)
        got["files"]["_meta.json"] = b'{"owner": "x"}'
        return got
    monkeypatch.setattr(S, "clawhub_verify", verify)
    monkeypatch.setattr(install, "clawhub_skill", skill)
    out = install.install_thing("clawhub:ivangdavila/wedding-planner")
    assert out["status"] == "installed", out


def test_a_skill_that_needs_openclaws_own_plugin_is_look_yourself(sites, monkeypatch):
    from puffpai import skills
    from puffpai.finding import find, install
    find.find_things("wedding planner", kinds=["skill"])
    monkeypatch.setattr(install, "_clawhub_skill_md", lambda *a: (
        "---\nname: ticker-watch\n---\nInstall the ClawLink plugin in OpenClaw, then pair."))
    p = install.preview("clawhub:ivangdavila/wedding-planner")
    assert p["can"] == "look" and not p["question"]
    assert any("OpenClaw" in line for line in p["lines"])
    out = install.install_thing("clawhub:ivangdavila/wedding-planner")
    assert out["status"] == "look" and skills.installed() == []


def test_the_install_card_lists_every_script_and_only_real_commands():
    from puffpai.finding import install
    md = ("```\n├── scripts/\n│   └── tracker.dart\n"
          "+-------+\n| a | b |\n```\n"
          "```bash\ndart run scripts/tracker.dart add\n```")
    got = install.what_it_runs(md, ["SKILL.md", "scripts/tracker.dart"])
    assert got["scripts"] == ["scripts/tracker.dart"]
    assert got["commands"] == ["dart run scripts/tracker.dart add"]


def test_chat_never_installs_without_her_yes_even_if_the_check_flips(sites, monkeypatch):
    """Safety probe: a malicious first check meant no question was
    asked, and a second check that came back empty then installed it."""
    from puffpai import brain_tools, skills
    from puffpai.finding import find, sites as S, tools
    brain_tools.clear()
    tools.register_all()
    find.find_things("wedding planner", kinds=["skill"])
    answers = [{"decision": "fail", "verdict": "malicious", "summary": "", "version": "1.0.0",
                "files": []}]
    monkeypatch.setattr(S, "clawhub_verify", lambda slug, owner="": answers.pop(0)
                        if answers else None)
    asked = []
    got = brain_tools.call("install_thing", {"id": "clawhub:ivangdavila/wedding-planner"},
                           {"caller": "owner", "ask": lambda q: asked.append(q) or True})
    assert asked == [] and skills.installed() == []
    assert got["ok"] and "is installed" not in str(got["result"])
    brain_tools.clear()


def test_a_github_skill_installs_the_commit_she_was_shown(sites, monkeypatch):
    from puffpai.finding import find, install
    find.remember([__import__("puffpai.finding.sites", fromlist=["make"]).make(
        site="skills.sh", type_="skill", id="skills.sh:earthtojake/text-to-cad/bambu-labs",
        name="bambu-labs", can="install", raw={"repo": "earthtojake/text-to-cad",
                                               "skill": "bambu-labs"})])
    install.preview("skills.sh:earthtojake/text-to-cad/bambu-labs")
    seen = {}
    real = install.github_skill

    def spy(repo, skill, **kw):
        seen["sha"] = kw.get("sha")
        return real(repo, skill, **kw)
    monkeypatch.setattr(install, "github_skill", spy)
    install.install_thing("skills.sh:earthtojake/text-to-cad/bambu-labs")
    assert seen["sha"] == COMMIT


def test_an_app_with_no_card_shown_is_checked_and_nothing_runs(sites, monkeypatch):
    """r6-host: a server app is never "look at it yourself" for the parked box.
    Without a yes card for exactly this check (fail closed) it is checked now
    and nothing runs; a needed program that is missing keeps it (no removal)."""
    from puffpai.finding import find, install, sites as S
    from puffpai.integrate import apply as r3_apply
    from puffpai.integrate import records
    find.remember([S.make(site="github", type_="browser_app", id="github:o/OpenStock",
                          name="OpenStock", url="https://github.com/o/OpenStock",
                          licence="AGPL-3.0", can="integrate", raw={"item": {}})])
    monkeypatch.setattr(install, "_round3_found",
                        lambda f: {"repo": {"pinned_commit": "a" * 40}, "id": "x"})
    card = {"code_sha": "c" * 64, "host_sha": "h" * 64, "lines": ["checked"],
            "host": {"how": "compose", "commands": [], "missing": ["docker"]}}
    monkeypatch.setattr(r3_apply, "prepare_code_project", lambda item, ctx: {
        "status": "checked", "integration_id": "openstock-abcdefgh", "card": card})
    monkeypatch.setattr(r3_apply, "prepared_card", lambda iid: card)
    finished = []
    monkeypatch.setattr(r3_apply, "finish_prepared",
                        lambda iid, **k: finished.append(iid) or {
                            "status": "held", "needs": ["docker"],
                            "integration_id": iid, "line": "It needs Docker."})
    removed = []
    monkeypatch.setattr(records, "remove", lambda iid, by: removed.append(iid) or {})
    out = install.install_thing("github:o/OpenStock", via="wizard")
    assert out["status"] == "held" and finished == [] and "card" in out["line"]
    p = install.preview("github:o/OpenStock")
    assert p["can"] == "look" and "Docker" in p["can_words"] and not p["question"]
    assert p["needs_programs"] == ["docker"]
    out = install.install_thing("github:o/OpenStock", via="wizard")
    assert out["status"] == "held" and removed == []          # kept, never removed
    assert "locked box" not in out["line"]
