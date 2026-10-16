"""Removing an integration takes every part out.

Its app is uninstalled (every attempt's install id), its locked-box project
removed, a copy of its data kept 30 days in ``<state>/removed/``, its folders
deleted with the real ``safe_rmtree`` (a junction inside the app folder is
unlinked, never followed), its tile taken off, its actions gone. Only a
paired browser (or the terminal) removes; the app page's id (the app's own
install id) finds the record; the Add page's take-out path is used when the
thing was added there.
"""
from __future__ import annotations

import json
import os
import sys
import time
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import home_placement  # noqa: E402
from puffpai.integrate import actions, apply, records, wording  # noqa: E402

AUTH = None


def _junction(link: str, target: str) -> bool:
    try:
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(target, link)
        else:
            os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, AttributeError, NotImplementedError):
        return False


@pytest.fixture
def world(monkeypatch, tmp_path):
    state = tmp_path / "state"
    apps_root = tmp_path / "appsroot"
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(state))
    monkeypatch.setenv("PUFFPAI_APPS_ROOT", str(apps_root))
    monkeypatch.setattr(actions, "_SOURCES", {})
    uninstalled, removed_projects = [], []
    install = types.ModuleType("puffpai.webapps.install")
    install.uninstall = lambda app_id: uninstalled.append(app_id)
    monkeypatch.setitem(sys.modules, "puffpai.webapps.install", install)
    mirror = types.ModuleType("puffpai.webapps.mirror")
    mirror.get = lambda app_id: {"version": 4, "data": {"guests": [{"name": "Zanzibar Quill"}]}}
    monkeypatch.setitem(sys.modules, "puffpai.webapps.mirror", mirror)
    driver = types.ModuleType("puffpai.box.driver")
    driver.remove_project = lambda project: removed_projects.append(project)
    monkeypatch.setitem(sys.modules, "puffpai.box.driver", driver)
    iid = records.new_id("Big Day")
    app_id = records.new_id("Big Day")
    old_attempt = records.new_id("Big Day")
    rec = records.blank(iid, name="Big Day", need="j5")
    rec.update({"status": "ready", "mode": "static_web", "app_id": app_id,
                "box": {"project": iid, "image_mb": 10}})
    records.save(rec)
    apply._save_stash(iid, {"app_ids": [old_attempt, app_id]})
    from puffpai.security import safe_archive
    ext = safe_archive.extraction_root()
    for folder in (os.path.join(str(state), "apps", app_id),
                   os.path.join(str(state), "webapps", app_id),
                   os.path.join(ext, iid, "src")):
        sup.write(folder, "index.html", "<p>app</p>")
    outside = tmp_path / "outside_keep"
    outside.mkdir()
    (outside / "precious.txt").write_text("keep me", encoding="utf-8")
    linked = _junction(os.path.join(str(state), "webapps", app_id, "link"), str(outside))
    home_placement.place("app", iid, "Big Day")
    return {"id": iid, "app_id": app_id, "old": old_attempt, "state": state, "ext": ext,
            "outside": outside, "linked": linked, "uninstalled": uninstalled,
            "projects": removed_projects}


def test_every_part_goes_and_a_copy_stays(world):
    got = records.remove(world["id"], by="owner")
    assert got["ok"], got
    assert got["line"] == wording.REMOVED.format(name="Big Day")
    assert set(world["uninstalled"]) == {world["app_id"], world["old"]}
    assert world["projects"] == [world["id"]]
    state = str(world["state"])
    for path in (records.folder(world["id"]), os.path.join(state, "apps", world["app_id"]),
                 os.path.join(state, "webapps", world["app_id"]),
                 os.path.join(world["ext"], world["id"])):
        assert not os.path.lexists(path), path
    assert records.get(world["id"]) is None
    assert not home_placement.is_placed("app", world["id"])
    copies = os.listdir(records.removed_dir())
    assert len(copies) == 1 and copies[0].startswith(world["id"] + "-")
    kept = json.load(open(os.path.join(records.removed_dir(), copies[0]), encoding="utf-8"))
    assert kept["apps"][world["app_id"]]["data"]["guests"][0]["name"] == "Zanzibar Quill"
    assert "files_root" not in kept["record"]
    # the id is never issued again
    issued = open(records.issued_path(), encoding="utf-8").read().split()
    assert world["id"] in issued


def test_a_junction_inside_the_app_folder_is_not_followed(world):
    if not world["linked"]:
        pytest.skip("this computer cannot make a junction here")
    records.remove(world["id"], by="owner")
    assert (world["outside"] / "precious.txt").read_text(encoding="utf-8") == "keep me"


def test_copies_older_than_30_days_are_cleared(world):
    os.makedirs(records.removed_dir(), exist_ok=True)
    old = os.path.join(records.removed_dir(), "old-app-abcdefgh-2026-01-01.json")
    with open(old, "w", encoding="utf-8") as fh:
        fh.write("{}")
    past = time.time() - 31 * 86400
    os.utime(old, (past, past))
    records.remove(world["id"], by="owner")
    assert not os.path.exists(old)


def test_the_app_page_id_finds_it_and_only_a_paired_browser_removes(world):
    from puffpai import api_server
    client = api_server.app.test_client()
    auth = {"Authorization": f"Bearer {api_server._API_KEY}"}
    assert client.post(f"/integrations/{world['app_id']}/remove", headers=auth).status_code == 403
    assert client.post(f"/integrations/{world['app_id']}/remove").status_code == 401
    assert records.get(world["id"]) is not None
    resp = client.post(f"/integrations/{world['app_id']}/remove",
                       headers={**auth, "X-Puff-Device-Id": "deviceaaaa000001"})
    assert resp.status_code == 200 and resp.get_json()["line"].startswith("Removed Big Day.")
    assert records.get(world["id"]) is None


def test_the_add_page_take_out_path_is_used_when_it_was_added_there(world, monkeypatch):
    from puffpai import add_more
    monkeypatch.setitem(add_more._REMOVERS, "integration", apply._remove_by_undo)  # noqa: SLF001
    add_more._record_addition(  # noqa: SLF001
        {"label": "Big Day", "section": "apps_code",
         "apply": {"kind": "code_project", "ref": "github:someone/wedding-planner"}},
        {"status": "added", "undo": {"kind": "integration", "ref": world["id"]}},
        via="add_page", device="this laptop")
    entry = add_more.added()[-1]["entry"]
    line = add_more.take_out(entry, by="owner")
    assert line.startswith("Removed Big Day.")
    assert records.get(world["id"]) is None
    assert entry not in {r["entry"] for r in add_more.added()}


def test_unknown_ids_change_nothing(world):
    assert records.remove("nothing-here-abcdefgh", by="owner")["ok"] is False
    assert records.remove("../escape", by="owner")["ok"] is False
    assert records.get(world["id"]) is not None


def test_the_terminal_removes_from_the_command_line(world, capsys):
    import argparse
    from puffpai.integrate import cli
    parser = argparse.ArgumentParser()
    cli.add_parsers(parser.add_subparsers())
    args = parser.parse_args(["integrations", "list"])
    assert args.func(args) == 0
    assert "Big Day: ready" in capsys.readouterr().out
    args = parser.parse_args(["integrations", "remove", world["id"]])
    assert args.func(args) == 0
    assert "Removed Big Day." in capsys.readouterr().out
    args = parser.parse_args(["integrations", "list"])
    args.func(args)
    assert capsys.readouterr().out.strip() == wording.CLI_NONE
