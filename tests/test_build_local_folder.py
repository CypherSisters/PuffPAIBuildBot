"""A folder of her own code (the Developer page's "A folder of my code"):
read like a downloaded project, run in place, refused only for her vault,
BuildBot's own folders, system folders and a whole drive.

Copyright (c) 2025 Cypher Sisters. Apache-2.0 License.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from test_build_api import DEVICE, client, state  # noqa: E402,F401
from test_build_matrix import _project_ctx, run_goal  # noqa: E402

from puffpai.build import apps, pipeline, schema as S  # noqa: E402
from puffpai.build import toolsmith_run  # noqa: E402


def _folder(tmp_path, name="my-app", readme="", pkg=None, extra=None):
    d = tmp_path / "code" / name
    d.mkdir(parents=True)
    if readme:
        (d / "README.md").write_text(readme, encoding="utf-8")
    if pkg is not None:
        (d / "package.json").write_text(json.dumps(pkg), encoding="utf-8")
    for n, text in (extra or {}).items():
        (d / n).write_text(text, encoding="utf-8")
    return d


def test_a_folder_with_run_steps_is_an_app_run_in_place(state):
    d = _folder(state, readme="# My app\n\nTracks my plants.\n\n## Quick start\n\n"
                              "```\nnpm install\nnpm start\n```\n")
    c = apps.from_local_folder(str(d))
    assert c["direct"] is True and c["type"] == "app"
    assert c["readme_steps"] == ["npm install", "npm start"] and c["readme_runtime"] == "node"
    assert os.path.normcase(c["local_path"]) == os.path.normcase(os.path.realpath(d))
    assert c["ref"].startswith("local:") and c["url"].startswith("file:///")
    assert c["name"] == "my-app" and c["what"] == "Tracks my plants."
    # Nothing is written into her folder by reading it.
    assert sorted(os.listdir(d)) == ["README.md"]


def test_a_compose_file_or_package_scripts_say_how_it_starts(state):
    d = _folder(state, "composed", extra={"docker-compose.yml": "services: {}\n"})
    assert apps.from_local_folder(str(d))["readme_steps"] == ["docker compose up -d"]
    d2 = _folder(state, "noded", pkg={"name": "noded", "description": "A thing.",
                                       "scripts": {"start": "node server.js"}})
    c = apps.from_local_folder(str(d2))
    assert c.get("readme_steps"), c
    assert c["what"] == "A thing."


def test_a_folder_that_never_says_how_to_start_is_code_to_make_an_app_from(state):
    d = _folder(state, "lib", readme="# lib\n\nSome helpers.\n")
    c = apps.from_local_folder(str(d))
    assert c["ref"].startswith("code:local:") and c["kind"] == "the project you gave"
    assert c["full_name"] == "lib" and c["local_path"]


def test_refused_places(state, monkeypatch):
    from puffpai.config import get_state_dir
    os.makedirs(get_state_dir(), exist_ok=True)
    for bad in (get_state_dir(), os.path.abspath(os.sep), str(state / "nope")):
        with pytest.raises(ValueError):
            apps.from_local_folder(bad)
    assert apps.local_folder_problem("")[1]
    real, why = apps.local_folder_problem(str(_folder(state, "fine")))
    assert real and not why


def test_only_a_full_path_never_one_read_against_buildbots_own_folder(state, monkeypatch):
    fine = _folder(state, "fine")
    monkeypatch.chdir(fine)                   # even where a relative path WOULD be fine
    drive = os.path.splitdrive(str(fine))[0] or "C:"
    for bad in (drive, ".", "..", "src", "code/fine", "fine", drive + "fine",
                "\\fine" if os.name == "nt" else "fine/"):
        real, why = apps.local_folder_problem(bad)
        assert not real and why == apps.NOT_FULL_PATH, bad
    assert "C:\\Users\\you\\projects\\myapp" in apps.NOT_FULL_PATH
    assert apps.local_folder_problem(str(fine))[0]


def test_buildbots_own_code_is_never_her_project(state):
    root = apps.install_root()
    assert os.path.isdir(os.path.join(root, "src", "puffpai")) or \
        os.path.basename(root) == "puffpai"
    for p in (root, os.path.join(root, "src"), os.path.dirname(apps.__file__)):
        real, why = apps.local_folder_problem(p)
        assert not real and apps.OWN_CODE in why, p
    real, why = apps.local_folder_problem(os.path.dirname(root))   # a folder holding it
    assert not real and why


def test_the_data_folder_and_the_folders_buildbot_made_in_it_are_refused(state, monkeypatch):
    from puffpai import setup_ops
    from puffpai.knowledge import my_data
    base = state / "bbdata"
    packs = base / "knowledge_packs"
    mine = packs / "My files"
    hers = mine / "my-site"
    old = state / "old-data"
    for d in (hers, packs / "wikipedia", old / "My files" / "old-site"):
        d.mkdir(parents=True)
    monkeypatch.setattr(my_data, "data_dir", lambda: str(packs))
    monkeypatch.setattr(my_data, "previous_dirs", lambda: [str(old)])
    monkeypatch.setattr(setup_ops, "default_pack_dir", lambda: str(packs))
    for bad in (packs, mine, packs / "wikipedia", old, old / "My files"):
        real, why = apps.local_folder_problem(str(bad))
        assert not real and apps.DATA_FOLDER in why, bad
    for bad in (base, state):                          # folders holding it
        real, why = apps.local_folder_problem(str(bad))
        assert not real and why, bad
    for ok in (hers, old / "My files" / "old-site"):   # folders she made in My files
        real, why = apps.local_folder_problem(str(ok))
        assert real and not why, (ok, why)


def test_from_the_developer_page_it_gets_a_job_and_waits_for_the_yes(state, client, monkeypatch):
    d = _folder(state, "plant-app",
                readme="# Plant app\n\nWaters plants.\n\n## Run\n\n```\nnpm install\nnpm start\n```\n")
    run_goal("GardenBot")
    real = pipeline.plan_project
    monkeypatch.setattr(pipeline, "plan_project", lambda url, name, what, jid="", ctx=None: real(
        url, name, what, jid, ctx={**_project_ctx({}), **(ctx or {})}))
    r = client.post("/build/project", json={"path": str(d), "job": "new", "order": "list"})
    got = r.get_json()
    assert r.status_code == 200 and got["ok"] and got["new"] is True, got
    b = S.load_build()
    job = S.find_job(b, got["job"]["id"])
    assert job["status"] == S.WAITING_YES and job.get("her_order") == "list"
    part = next(p for ref, p in (b.get("parts") or {}).items() if ref.startswith("local:"))
    assert part["local_path"] and part["readme_steps"] == ["npm install", "npm start"]
    # A refused folder is a plain 422, never a job.
    from puffpai.config import get_state_dir
    os.makedirs(get_state_dir(), exist_ok=True)
    r = client.post("/build/project", json={"path": get_state_dir()})
    assert r.status_code == 422 and "settings folder" in r.get_json()["message"]
    # The verifier's cases: never BuildBot's own working folder or code.
    for bad in ("C:", ".", "src"):
        r = client.post("/build/project", json={"path": bad})
        assert r.status_code == 422 and r.get_json()["message"] == apps.NOT_FULL_PATH
    r = client.post("/build/project", json={"path": apps.install_root()})
    assert r.status_code == 422 and apps.OWN_CODE in r.get_json()["message"]


def test_toolsmith_runs_her_folder_in_place_and_keeps_its_files_in_buildbot(state, monkeypatch):
    d = _folder(state, "inplace", readme="# x\n\n```\nnpm install\nnpm start\n```\n")
    c = apps.from_local_folder(str(d))
    (d / ".buildbot").mkdir()
    assert toolsmith_run.own_files(str(d)) == os.path.join(str(d), ".buildbot", "logs")
    assert toolsmith_run.own_files(str(d), "admin") == os.path.join(str(d), ".buildbot", "admin")
    (d / ".buildbot").rmdir()
    down = os.path.join(toolsmith_run.apps_root(), "bb-x")
    assert toolsmith_run.own_files(down) == down + "-logs"
    monkeypatch.setattr(toolsmith_run, "listening_ports", lambda: set())
    monkeypatch.setattr(toolsmith_run.subprocess, "run",
                        lambda *a, **k: pytest.fail("ran " + str(a)))
    t = toolsmith_run.Run({"id": "j1", "name": "x"}, [c], lambda _t: None)
    got = t.download({"app": c["name"]})
    assert got["ok"] and os.path.normcase(got["folder"]) == os.path.normcase(c["local_path"])
    assert os.path.isdir(os.path.join(str(d), ".buildbot"))
    assert sorted(os.listdir(d)) == [".buildbot", "README.md"]
    # Never deleted as a try folder.
    assert toolsmith_run.remove_try_folder(str(d)) is False and os.path.isdir(d)


def test_a_desktop_program_job_carries_its_program_for_the_open_button(state, monkeypatch):
    from puffpai.build import api, make
    job = {"id": "j1", "name": "Editor", "route": S.A}
    got = make._working_app(job, {"app_id": "program:p1", "name": "Editor", "url": "",
                                  "desktop": True, "program_id": "p1",
                                  "path": r"C:\Apps\Editor\editor.exe"})
    assert got["made"]["program"] == {"id": "p1", "path": r"C:\Apps\Editor\editor.exe"}
    monkeypatch.setattr(make, "load_made", lambda j: {"made": {
        "app_name": "Editor", "app_ref": "github:o/editor", "program": got["made"]["program"]}})
    assert api.app_used(job)["program"] == {"id": "p1", "path": r"C:\Apps\Editor\editor.exe"}
    # A web app has none.
    web = make._working_app(job, {"app_id": "a1", "name": "Web", "url": "http://127.0.0.1:9"})
    assert "program" not in web["made"]
