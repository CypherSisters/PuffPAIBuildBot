"""Live check: a real data project from GitHub becomes a list the bot reads.

Runs only with ``PUFFPAI_R3_LIVE=1``. ``bripatch/plant-variety-database`` (a
CC BY data project) is pinned through github.com's git address (no search
budget), its details read from raw files, downloaded from codeload.github.com
at exactly that commit, checked by Drawbridge (the code check, then the data
check), and added as a list app the lists fastpath and the chat step read.
Everything else is the real build; only the plan's memory (find_parts) is a
stand-in that hands back the detailed find. Downloads land in
``PUFFPAI_APPS_ROOT`` / ``PUFFPAI_STATE_DIR`` (set these to scratch folders).
Keyless: ``GITHUB_TOKEN`` and ``GH_TOKEN`` must be unset.
"""
from __future__ import annotations

import json
import os
import re
import sys
import types

import pytest

from puffpai import source_fetch

_REAL_TRANSPORT = source_fetch._default_transport

pytestmark = [pytest.mark.live,
              pytest.mark.skipif(os.environ.get("PUFFPAI_R3_LIVE") != "1",
                                 reason="live check: set PUFFPAI_R3_LIVE=1")]

OWNER, NAME = "bripatch", "plant-variety-database"
ITEM_ID = f"apps_code:github:{OWNER}/{NAME}"


def _found(branch: str) -> dict:
    from puffpai.catalogs.searchers import github
    item = {"owner": {"login": OWNER}, "name": NAME, "full_name": f"{OWNER}/{NAME}",
            "default_branch": branch, "stargazers_count": 0, "size": 1,
            "description": "A database of plant varieties.", "license": None,
            "pushed_at": "2025-01-01T00:00:00Z", "archived": False, "fork": False}
    return github.found_from_item(item, part="data-template")


def test_the_plant_database_becomes_a_list_the_bot_reads(monkeypatch):
    assert not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN")
    from puffpai import app_fastpath, drawbridge
    from puffpai.app_data import AppData
    from puffpai.catalogs import where_to_look as w
    from puffpai.integrate import actions, apply, chat, data_import, records

    monkeypatch.setattr(source_fetch, "_default_transport", _REAL_TRANSPORT)
    budget = w.new_budget(wizard=True)
    detailed = None
    for branch in ("main", "master"):
        got = w.details(_found(branch), budget=budget)
        if (got.get("repo") or {}).get("pinned_commit"):
            detailed = got
            break
    assert detailed is not None, "could not pin the project"
    detailed["meaning"] = {"job": "j1", "state": "ticked"}
    mod = types.ModuleType("puffpai.wizard.find_parts")
    mod.found_for = lambda item_id: detailed if item_id == ITEM_ID else None
    monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", mod)
    monkeypatch.setattr(drawbridge, "_CHECKERS", {})
    monkeypatch.setattr(drawbridge, "_REGISTRY", {"closed": False})
    monkeypatch.setattr(actions, "_SOURCES", {})
    for kind, rows_fn, block, hold in (
            (apply.KIND, apply.code_project_rows, apply.BLOCK_CODES, apply.HOLD_CODES),
            (data_import.KIND, data_import.data_rows, data_import.BLOCK_CODES,
             data_import.HOLD_CODES)):
        drawbridge.register_checker(kind, rows_fn, block_codes=block, hold_codes=hold)
    actions.register_source("data_query", data_import.list_actions, data_import.run_action)

    class Ctx:
        online = True
        shared: dict = {}
        lines: list = []

        def progress(self, line):
            self.lines.append(line)
    ctx = Ctx()
    ctx.lines = []
    out = apply.apply_code_project({"id": ITEM_ID, "label": "Plant varieties"}, ctx)
    report = {"found": {k: detailed.get(k) for k in ("id", "mode_guess", "integrable")},
              "repo": {k: (detailed.get("repo") or {}).get(k)
                       for k in ("pinned_commit", "license_reported", "license_read")},
              "result": {k: out.get(k) for k in ("status", "line", "integration_id")},
              "lines": ctx.lines}
    from puffpai.security import consent_tickets
    consent_tickets.reset()
    report["yeses"] = []
    iid = out["integration_id"]
    for _stage in range(4):
        if out.get("status") != "held":
            break
        # Anything the plan did not show waits for her yes, one stage at a
        # time (the code check, then the data check); say yes as a paired
        # device would.
        held = (records.get(iid) or {}).get("held") or {}
        report["yeses"].append({"reasons": held.get("reasons"), "line": out.get("line")})
        out = apply.decide(iid, True, device_id="device:liveproof0001")
        out["integration_id"] = iid
    report["after_yes"] = {k: out.get(k) for k in ("status", "line")}
    rec = records.get(out["integration_id"])
    report["record"] = {k: rec.get(k) for k in ("status", "mode", "list_app_id", "book_id",
                                                "held", "drawbridge")}
    assert rec["status"] == "ready" and rec["mode"] == "data_template", report
    doc = AppData().get_data(rec["list_app_id"])
    lists = {k: len(v) for k, v in doc.items() if isinstance(v, list) and k != "credit"}
    report["lists"] = lists
    assert lists and max(lists.values()) > 0
    first_key = max(lists, key=lists.get)
    label = first_key.replace("_", " ")
    answer = app_fastpath.handle(f"what is on the {label} list", AppData())
    monkeypatch.setattr(chat, "_backend", lambda: None)
    reply = chat.try_answer(f"what is on the {label} list?", surface="chat",
                            device_id="device:liveproof0001")
    report["fastpath"] = (answer or "")[:300]
    report["chat"] = (reply or {}).get("reply", "")[:300]
    print(json.dumps(report, indent=1, default=str))
    assert answer and reply and "(read from" in reply["reply"]


# -- a web app built in the locked box (web_build), end to end --------------------
#
# Needs the locked box, the web apps and the
# existing dev box: PUFFPAI_BOX_LIVE=1, PUFFPAI_BOX_DISTRO=PuffPAI-sandbox-dev,
# PUFFPAI_BOX_ROOT (the box folder). This install is added to the box's claims
# for the run (the box's own share step) and dropped again at the end. The
# project is a real public web app whose front end builds into client/dist
# (the one the box's own npm check builds), found, detailed and pinned the
# same way the plan does.

#: The web project (``owner/name``); ``PUFFPAI_R3_WEB_REPO`` picks another.
#: JFrusher/Tableaux builds, then the web app check stops it on a privacy-policy
#: sentence (an xfail, recorded); mdn/todo-vue (MPL-2.0, vite) is added end to
#: end.
_WEB = os.environ.get("PUFFPAI_R3_WEB_REPO") or "JFrusher/Tableaux"
if not re.match(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$", _WEB):
    _WEB = "JFrusher/Tableaux"
WEB_OWNER, WEB_NAME = _WEB.split("/")
WEB_ITEM = f"apps_code:github:{WEB_OWNER}/{WEB_NAME}"


@pytest.mark.skipif(not (os.environ.get("PUFFPAI_BOX_LIVE") == "1"
                         and os.environ.get("PUFFPAI_BOX_DISTRO") == "PuffPAI-sandbox-dev"
                         and os.environ.get("PUFFPAI_BOX_ROOT")),
                    reason="live box check: set PUFFPAI_BOX_LIVE=1, PUFFPAI_BOX_DISTRO, "
                           "PUFFPAI_BOX_ROOT")
def test_a_web_app_is_built_in_the_locked_box_and_added(monkeypatch):
    import time
    assert not os.environ.get("GITHUB_TOKEN") and not os.environ.get("GH_TOKEN")
    driver = pytest.importorskip("puffpai.box.driver")
    install = pytest.importorskip("puffpai.webapps.install")
    assert callable(getattr(driver, "put_project", None)) and callable(install.install_static)
    from puffpai import factory_wiring
    from puffpai.catalogs import where_to_look as w
    from puffpai.catalogs.searchers import github
    from puffpai.integrate import apply, records
    from puffpai.security import consent_tickets

    monkeypatch.setattr(source_fetch, "_default_transport", _REAL_TRANSPORT)
    distro = os.environ["PUFFPAI_BOX_DISTRO"]
    report: dict = {"steps": []}
    claimed = driver.add_claim(distro)
    try:
        report["box"] = driver.status().as_dict()
        assert report["box"]["state"] == "ready", report
        report["wiring"] = factory_wiring.connect_round3()
        # The project as a search answer gives it (one keyless call): the plan
        # compares its size, licence and branch with what is downloaded.
        import urllib.request
        with urllib.request.urlopen(urllib.request.Request(
                f"https://api.github.com/repos/{WEB_OWNER}/{WEB_NAME}",
                headers={"Accept": "application/vnd.github+json",
                         "User-Agent": "puffpai-live-check"}), timeout=30) as resp:
            item = json.load(resp)
        report["search_item"] = {k: item.get(k) for k in ("size", "default_branch",
                                                          "stargazers_count", "pushed_at")}
        report["search_item"]["license"] = (item.get("license") or {}).get("spdx_id")
        budget = w.new_budget(wizard=True)
        detailed = w.details(github.found_from_item(item, part="code-app"), budget=budget)
        if not (detailed.get("repo") or {}).get("pinned_commit"):
            detailed = None
        assert detailed is not None, "could not pin the project"
        detailed["meaning"] = {"job": "j1", "state": "ticked"}
        report["found"] = {"mode_guess": detailed.get("mode_guess"),
                           "integrable": detailed.get("integrable"),
                           "pinned_commit": detailed["repo"]["pinned_commit"],
                           "license_reported": detailed["repo"].get("license_reported"),
                           "license_read": detailed["repo"].get("license_read")}
        mod = types.ModuleType("puffpai.wizard.find_parts")
        mod.found_for = lambda item_id: detailed if item_id == WEB_ITEM else None
        monkeypatch.setitem(sys.modules, "puffpai.wizard.find_parts", mod)

        class Ctx:
            online = True
            shared: dict = {}

            def __init__(self):
                self.lines: list = []

            def progress(self, line):
                self.lines.append(line)
        ctx = Ctx()
        real_install = install.install_static

        def watched_install(*a, **k):
            got = real_install(*a, **k)
            report["install_static"] = {key: got.get(key) for key in (
                "app_id", "verdict", "lines", "drivable", "hosts")} if isinstance(got, dict)                 else repr(got)
            return got
        monkeypatch.setattr(install, "install_static", watched_install)
        t0 = time.monotonic()
        out = apply.apply_code_project({"id": WEB_ITEM, "label": WEB_NAME}, ctx)
        iid = out.get("integration_id")
        report["steps"].append({"status": out.get("status"), "line": out.get("line")})
        consent_tickets.reset()
        for _stage in range(4):
            if out.get("status") != "held":
                break
            held = (records.get(iid) or {}).get("held") or {}
            report["steps"].append({"held": held.get("reasons"), "line": out.get("line")})
            out = apply.decide(iid, True, device_id="device:liveproof0001")
            report["steps"].append({"status": out.get("status"), "line": out.get("line"),
                                    "lines": out.get("lines")})
        report["seconds"] = round(time.monotonic() - t0, 1)
        report["lines"] = ctx.lines
        rec = records.get(iid) or {}
        report["record"] = {k: rec.get(k) for k in ("status", "mode", "app_id", "lock", "box",
                                                    "outside", "drawbridge", "held")}
        report["record"]["from"] = {k: (rec.get("from") or {}).get(k)
                                    for k in ("url", "commit", "archive_sha256", "license_read")}
        # Toolsmith's part: pinned download, Drawbridge, npm ci and the build in
        # the locked box, the built folder exported and handed to the web app
        # step, which checks it again.
        assert rec.get("mode") == "web_build" and "install_static" in report, report
        assert any("Building it in the locked box" in ln for ln in ctx.lines), report
        project = (rec.get("box") or {}).get("project") or ""
        listing = driver._text(driver._wsl(
            ["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c",
             "ls /srv/box/images /srv/box/projects 2>/dev/null"], timeout=60).stdout)
        report["box_after"] = project in listing
        installed = report["install_static"] if isinstance(report["install_static"], dict) else {}
        app_verdict = installed.get("verdict") or {}
        if rec.get("status") == "refused" and app_verdict.get("verdict") == "block":
            # The web app step stopped it: nothing of it is left anywhere.
            assert project and project not in listing, report
            fails = [r.get("code") for r in app_verdict.get("rows") or []
                     if r.get("result") == "fail"]
            pytest.xfail(f"the web app check stopped the built app: {fails}")
        assert rec.get("status") == "ready", report
        assert (rec.get("lock") or {}).get("kind") == "npm"
        assert rec.get("app_id")
        from puffpai.webapps import host
        app = host.read_record(rec["app_id"]) or {}
        report["webapp"] = {k: app.get(k) for k in ("status", "origin", "name")}
        assert app.get("status") == "installed", report
        gone = records.remove(iid, by="terminal")
        report["removed"] = {k: gone.get(k) for k in ("ok", "line")}
        assert gone.get("ok"), report
        after = driver._text(driver._wsl(
            ["-d", distro, "-u", "root", "--exec", "/bin/sh", "-c",
             "ls /srv/box/images /srv/box/projects 2>/dev/null"], timeout=60).stdout)
        report["box_after_remove"] = project in after
        assert project not in after, report
        report["record_after_remove"] = (records.get(iid) or {}).get("status")
    finally:
        print(json.dumps(report, indent=1, default=str))
        if claimed:
            driver.drop_claim(distro)
