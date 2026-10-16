"""App first: the personal-use licence rule, start detection for the
common app shapes, and the job page.

* Licence: an app she runs for herself on her own
  computer is never stopped over its licence; no licence, or terms about
  sharing, is a plain note. Drawbridge's code checks are unchanged (their own
  suite, ``test_drawbridge_code_project_checks.py``, still passes as it was).
* Start detection reads pnpm and yarn projects (through corepack, in the
  app's own folder), projects in parts, Django, uvicorn and Flask apps.

Nothing here reaches the internet.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "integrate"))
import integrate_support as sup  # noqa: E402

from puffpai import drawbridge  # noqa: E402
from puffpai.integrate import (apply, classify, fetch, records,  # noqa: E402
                               run_here, run_licence)

# ---------------------------------------------------------------------------
# 1. the licence reader (a table)
# ---------------------------------------------------------------------------

TEXTS = {
    "MIT": "MIT License\n\nCopyright (c) 2024 Someone\n\nPermission is hereby granted, free of "
           "charge, to any person obtaining a copy of this software ...",
    "BSD-2-Clause": "Copyright (c) 2020, A\nRedistribution and use in source and binary forms, "
                    "with or without modification, are permitted provided that ...",
    "BSD-3-Clause": "Copyright (c) 2020, A\nRedistribution and use in source and binary forms "
                    "... Neither the name of the copyright holder nor the names ...",
    "Apache-2.0": "                                 Apache License\n                           "
                  "Version 2.0, January 2004\n http://www.apache.org/licenses/",
    "ISC": "ISC License\n\nPermission to use, copy, modify, and/or distribute this software for "
           "any purpose with or without fee is hereby granted ...",
    "MPL-2.0": "Mozilla Public License Version 2.0\n==================================",
    "LGPL-3.0": "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n... the GNU General "
                "Public License ...",
    "GPL-2.0": "GNU GENERAL PUBLIC LICENSE\nVersion 2, June 1991\n... for noncommercial "
               "distribution and only if you received the program ...",
    "GPL-3.0": "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n... 13. Use with the GNU "
               "Affero General Public License ... distribute it noncommercially ...",
    "AGPL-3.0": "GNU AFFERO GENERAL PUBLIC LICENSE\nVersion 3, 19 November 2007\n",
    "EUPL-1.2": "EUROPEAN UNION PUBLIC LICENCE v. 1.2\nEUPL (c) the European Union 2007, 2016",
    "Unlicense": "This is free and unencumbered software released into the public domain.\n"
                 "... for any purpose, commercial or non-commercial ...",
    "CC0-1.0": "Creative Commons Legal Code\n\nCC0 1.0 Universal\n",
}


def _tree(tmp_path, files: dict) -> str:
    root = str(tmp_path / "proj")
    for rel, text in files.items():
        sup.write(root, rel, text)
    os.makedirs(root, exist_ok=True)
    return root


@pytest.mark.parametrize("spdx,text", sorted(TEXTS.items()))
def test_every_common_open_licence_passes(tmp_path, spdx, text):
    got = run_licence.read(_tree(tmp_path, {"LICENSE": text}))
    assert got["kind"] == run_licence.OPEN and got["name"] == spdx, got
    assert got["line"] == "" and got["file"] == "LICENSE"
    row = run_licence.rows(got)[0]
    assert row["result"] == "pass" and row["code"] == "license_ok"


@pytest.mark.parametrize("name", ["LICENCE", "license", "COPYING", "LICENSE.md", "LICENSE.txt",
                                  "UNLICENSE", "LICENSE-MIT"])
def test_licence_file_names_are_found(tmp_path, name):
    text = TEXTS["Unlicense"] if name == "UNLICENSE" else TEXTS["MIT"]
    assert run_licence.read(_tree(tmp_path, {name: text}))["kind"] == run_licence.OPEN


def test_a_licence_in_a_common_subfolder_is_found(tmp_path):
    got = run_licence.read(_tree(tmp_path, {"licenses/LICENSE": TEXTS["BSD-3-Clause"],
                                            "app.py": "x = 1\n"}))
    assert got["kind"] == run_licence.OPEN and got["file"] == "licenses/LICENSE"


def test_a_bsd_top_file_beside_a_folder_of_other_papers_is_open(tmp_path):
    """TiddlyWiki's shape: a BSD ``license`` file and a ``licenses/`` folder of
    contributor agreements (read as not free before)."""
    got = run_licence.read(_tree(tmp_path, {"license": TEXTS["BSD-3-Clause"],
                                            "licenses/cla-individual.md": "I agree ..."}))
    assert got["kind"] == run_licence.OPEN and got["name"] == "BSD-3-Clause"


@pytest.mark.parametrize("files,spdx", [
    ({"package.json": json.dumps({"license": "MIT"})}, "MIT"),
    ({"package.json": json.dumps({"license": "(MIT OR Apache-2.0)"})}, "(MIT OR Apache-2.0)"),
    ({"pyproject.toml": '[project]\nname = "x"\nlicense = "AGPL-3.0-only"\n'}, "AGPL-3.0-only"),
    ({"pyproject.toml": '[project]\nname = "x"\nclassifiers = ["License :: OSI Approved :: '
                        'MIT License"]\n'}, "MIT"),
    ({"setup.cfg": "[metadata]\nname = x\nlicense = BSD-3-Clause\n"}, "BSD-3-Clause"),
])
def test_manifest_licence_fields_are_read(tmp_path, files, spdx):
    got = run_licence.read(_tree(tmp_path, files))
    assert got["kind"] == run_licence.OPEN and got["name"] == spdx and got["file"] is None


def test_the_code_sites_reported_licence_is_the_last_resort(tmp_path):
    got = run_licence.read(_tree(tmp_path, {"app.py": "x = 1\n"}), reported="GPL-3.0")
    assert got["kind"] == run_licence.OPEN and got["source"] == "reported"


def test_no_licence_anywhere_is_a_note_with_her_words(tmp_path):
    got = run_licence.read(_tree(tmp_path, {"app.py": "x = 1\n"}), reported="")
    assert got["kind"] == run_licence.NONE
    assert got["line"] == ("This project has no licence file. You can run it for yourself; "
                           "you may not have the right to share it.")
    row = run_licence.rows(got)[0]
    assert row["result"] == "note" and row["why"] == got["line"]


@pytest.mark.parametrize("text,name", [
    ('"Commons Clause" License Condition v1.0\n' + TEXTS["MIT"], "Commons Clause"),
    ("Business Source License 1.1\n\nLicensor: Someone", "Business Source"),
    ("Server Side Public License\nVERSION 1, OCTOBER 16, 2018", "Server Side Public License"),
    ("Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)", "CC BY-NC"),
    ("PolyForm Noncommercial License 1.0.0", "PolyForm Noncommercial"),
])
def test_source_available_and_non_commercial_may_run_with_a_note(tmp_path, text, name):
    got = run_licence.read(_tree(tmp_path, {"LICENSE": text}))
    assert got["kind"] == run_licence.LIMITED and got["name"] == name
    assert "You can run it for yourself" in got["line"]
    assert run_licence.rows(got)[0]["result"] == "note"


def test_a_code_site_that_cannot_name_the_licence_is_its_own_words():
    assert run_licence.note_for_reported("NOASSERTION") == run_licence.CUSTOM_LINE
    assert run_licence.note_for_reported("") == run_licence.NO_LICENCE_LINE


def test_custom_words_may_run_with_a_note(tmp_path):
    got = run_licence.read(_tree(tmp_path, {"LICENSE": "You may look but not use."}))
    assert got["kind"] == run_licence.CUSTOM and "You can run it for yourself" in got["line"]


def test_a_licence_file_through_a_link_is_never_followed(tmp_path):
    root = _tree(tmp_path, {"app.py": "x = 1\n"})
    target = str(tmp_path / "outside.txt")
    sup.write(str(tmp_path), "outside.txt", TEXTS["MIT"])
    try:
        os.symlink(target, os.path.join(root, "LICENSE"))
    except (OSError, NotImplementedError):
        pytest.skip("links cannot be made here")
    assert run_licence.read(root)["kind"] == run_licence.NONE


def test_no_em_dashes_or_door_words_in_the_notes():
    for line in (run_licence.NO_LICENCE_LINE, run_licence.LIMITED_LINE, run_licence.CUSTOM_LINE):
        assert "\u2014" not in line and "door" not in line.lower()


# ---------------------------------------------------------------------------
# 2. Drawbridge: the licence is a note when it runs here; code checks stand
# ---------------------------------------------------------------------------

RID = "big-day-abcdefgh"
PLAN = {"license": "MIT", "mode": "static_web", "size_bytes": 600 * 1024,
        "runtime_hosts": [], "box": False}


@pytest.fixture
def db(monkeypatch, tmp_path):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    sup.fresh_drawbridge(monkeypatch)


def _verdict(root, *, plan=None, mode=None):
    lic = fetch.read_license(str(root))
    got_mode, scan = classify.classify_tree(str(root))
    mode = mode or got_mode
    rec = records.blank(RID, name="Big Day")
    rec["from"]["url"] = "https://github.com/someone/wedding-planner"
    rec["from"]["commit"] = sup.COMMIT
    rec["plan_facts"] = dict(plan or PLAN, mode=mode)
    run_lic = run_licence.read(str(root)) if mode in apply.RUN_MODES else None
    got = {"url": "https://codeload.github.com/someone/wedding-planner/tar.gz/" + sup.COMMIT,
           "commit": sup.COMMIT, "sha256": "ab" * 32, "bytes": 200_000,
           "final_host": "codeload.github.com", "extracted_bytes": 140_000}
    draft = apply.build_draft(rec, got=got, lic=lic, mode=mode, scan=scan,
                              description="A self-contained planner in one page.",
                              feature_lines=0, archived=False, run_lic=run_lic)
    return drawbridge.check_addition(draft, by="test")


def test_no_licence_file_runs_here_with_the_note(db, tmp_path):
    root = sup.static_tree(str(tmp_path / "src"))
    os.remove(os.path.join(root, "LICENSE"))
    v = _verdict(root)
    assert v["verdict"] == "allow", v["reasons"]
    row = next(r for r in v["results"] if r["code"] == "no_license")
    assert row["result"] == "note" and row["why"] == run_licence.NO_LICENCE_LINE


@pytest.mark.parametrize("text", ['"Commons Clause" License Condition v1.0\n' + TEXTS["MIT"],
                                  "Business Source License 1.1", "You may look but not use."])
def test_limited_or_custom_licences_run_here_with_a_note(db, tmp_path, text):
    root = sup.static_tree(str(tmp_path / "src"))
    sup.write(root, "LICENSE", text)
    v = _verdict(root)
    assert v["verdict"] == "allow", v["reasons"]
    assert any(r["code"] == "not_open" and r["result"] == "note" for r in v["results"])


def test_code_checks_still_stop_it_whatever_the_licence(db, tmp_path):
    root = sup.static_tree(str(tmp_path / "src"))
    os.remove(os.path.join(root, "LICENSE"))
    # A tunnel tool used in code (a "curl | sh" install line no longer counts:
    # owner, 2026-10-12).
    sup.write(root, "scripts/start.sh", "ngrok http 3000\n")
    v = _verdict(root)
    assert v["verdict"] == "block" and "tunnel" in v["reasons"]
    assert "no_license" not in v["reasons"]


def test_a_data_set_copied_into_the_bot_keeps_the_strict_rule(db, tmp_path):
    root = str(tmp_path / "data")
    sup.write(root, "guests.csv", "name,table\nA,1\nB,2\n")
    sup.write(root, "menu.csv", "dish\nsoup\n")
    v = _verdict(root, mode="data_template",
                 plan=dict(PLAN, mode="data_template", license=""))
    assert v["verdict"] == "block" and "no_license" in v["reasons"]


def test_the_yes_row_says_when_a_found_app_has_no_licence():
    from puffpai.build import setup as setup_mod
    parts = {"a": {"name": "Cookbook", "source": "github", "licence": ""},
             "b": {"name": "Planner", "source": "awesome-selfhosted", "licences": ["MIT"]},
             "c": {"name": "Shelf", "source": "awesome-selfhosted",
                   "licences": ["MIT", "Commons-Clause"]}}
    text = setup_mod._with_licence_notes("Drawbridge checks it first.", ["a", "b", "c"], parts)
    assert f"Cookbook: {run_licence.NO_LICENCE_LINE}" in text
    assert "Planner:" not in text
    assert "Shelf: Its licence (Commons Clause) limits" in text


# ---------------------------------------------------------------------------
# 3. start detection fixtures (read only, nothing runs)
# ---------------------------------------------------------------------------

PORT = 5123
IID = "proj-abcdefgh"


def _plan(tmp_path, files):
    return run_here.detect(_tree(tmp_path, files), port=PORT)


def test_a_pnpm_project_uses_corepack_pinned_from_package_manager(tmp_path, monkeypatch):
    spec = "pnpm@9.15.0+sha512." + "a" * 128
    plan = _plan(tmp_path, {
        "package.json": json.dumps({"name": "x", "packageManager": spec,
                                    "scripts": {"dev": "vite dev", "build": "vite build"},
                                    "dependencies": {"vite": "^5"}}),
        "pnpm-lock.yaml": "lockfileVersion: '9.0'\n"})
    assert plan["how"] == "node_server" and plan["pm"]["spec"] == spec
    shown = run_here.shown_lines(plan, IID)
    assert shown[0].startswith("corepack enable --install-directory ")
    assert shown[0].endswith(os.path.join(IID, "pm_bin") + " pnpm")
    assert shown[1] == f"corepack install -g {spec}"
    assert shown[2] == (f"corepack {spec} install --frozen-lockfile --ignore-scripts "
                        "--ignore-pnpmfile")
    assert shown[-1].startswith(f"corepack {spec} run dev --host 127.0.0.1 --port {PORT}")
    env_plan = dict(plan)
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    env = run_here._base_env(IID, env_plan)
    assert env["COREPACK_HOME"].startswith(run_here.home(IID))
    assert env["PATH"].split(os.pathsep)[0] == run_here.pm_bin(IID)
    assert env["COREPACK_ENABLE_AUTO_PIN"] == "0"


def test_a_pnpm_workspace_starts_its_part_from_its_folder(tmp_path):
    plan = _plan(tmp_path, {
        "package.json": json.dumps({"name": "mono", "private": True,
                                    "devDependencies": {"turbo": "1"}}),
        "pnpm-workspace.yaml": "packages:\n  - 'apps/*'\n",
        "pnpm-lock.yaml": "lockfileVersion: '6.0'\n",
        "apps/web/package.json": json.dumps({"name": "web", "scripts": {"dev": "next dev"}})})
    assert plan["how"] == "node_server" and plan["pm"]["spec"] == "pnpm@8.15.9"
    assert plan["start"]["dir"] == "apps/web"
    assert plan["start"]["args"][:3] == ["pnpm@8.15.9", "run", "dev"]
    assert "-H" in plan["start"]["args"]                     # next pinned to 127.0.0.1
    assert not plan["steps"][2].get("dir")                     # the workspace installs at the top


def test_frontend_and_backend_side_by_side_start_the_backend(tmp_path):
    plan = _plan(tmp_path, {
        "README.md": "# Meals\n",
        "frontend/package.json": json.dumps({"scripts": {"dev": "vite"},
                                             "dependencies": {"vite": "5"}}),
        "backend/package.json": json.dumps({"scripts": {"start": "node index.js"},
                                            "dependencies": {"express": "4"}}),
        "backend/index.js": "require('http')\n"})
    assert plan["how"] == "node_server" and plan["start"]["dir"] == "backend"
    assert all(st.get("dir") == "backend" for st in plan["steps"])
    assert run_here.shown_lines(plan, IID)[-1].startswith("(in its backend folder) npm run start")


def test_a_yarn_classic_project_pins_yarn_1(tmp_path):
    plan = _plan(tmp_path, {"package.json": json.dumps({"scripts": {"start": "node s.js"},
                                                        "dependencies": {"a": "1"}}),
                            "yarn.lock": "# yarn lockfile v1\n"})
    assert plan["pm"]["spec"] == "yarn@1.22.22"
    assert plan["steps"][2]["args"] == ["yarn@1.22.22", "install", "--frozen-lockfile",
                                        "--ignore-scripts", "--non-interactive"]


@pytest.mark.parametrize("files", [
    {"pnpm-workspace.yaml": "packages: ['a']\npnpmfile: ./hooks.cjs\n"},
    {"pnpm-workspace.yaml": "configDependencies:\n  x: 1.0.0\n"},
])
def test_pnpm_settings_that_load_code_are_refused(tmp_path, files):
    files = dict(files, **{"package.json": json.dumps({"packageManager": "pnpm@9.15.9",
                                                       "scripts": {"start": "node s.js"}})})
    plan = _plan(tmp_path, files)
    assert plan["how"] == "" and "cannot show" in plan["why_not"]


def test_an_unreadable_package_manager_field_is_refused(tmp_path):
    plan = _plan(tmp_path, {"package.json": json.dumps({"packageManager": "pnpm@latest; rm",
                                                        "scripts": {"start": "x"}})})
    assert plan["how"] == "" and "packageManager" in plan["why_not"]


def test_django_runs_its_notes_steps_then_its_own_server(tmp_path):
    plan = _plan(tmp_path, {
        "manage.py": "import django\n", "requirements.txt": "Django==5.0\n",
        "start.sh": "python manage.py migrate\npython manage.py collectstatic --noinput\n"
                    "gunicorn site.wsgi\n"})
    shown = run_here.shown_lines(plan, IID)
    assert plan["how"] == "python_server"
    assert "python manage.py migrate --noinput" in shown
    assert "python manage.py collectstatic --noinput" in shown
    assert shown[-1] == f"python manage.py runserver 127.0.0.1:{PORT} --noreload"


def test_django_one_folder_down_runs_from_there(tmp_path):
    plan = _plan(tmp_path, {"site/manage.py": "import django\n",
                            "site/requirements.txt": "Django==5.0\n"})
    assert plan["start"]["dir"] == "site"
    assert any(st["args"][-2:] == ["-r", "site/requirements.txt"] for st in plan["steps"])


def test_a_fastapi_app_in_its_package_runs_with_uvicorn(tmp_path):
    plan = _plan(tmp_path, {
        "pyproject.toml": '[project]\nname = "meals"\ndependencies = ["fastapi", '
                          '"uvicorn[standard]"]\n',
        "meals/__init__.py": "", "meals/app.py": "from fastapi import FastAPI\napp = FastAPI()\n"})
    assert plan["start"] == {"tool": "venv_bin", "bin": "uvicorn",
                             "args": ["meals.app:app", "--host", "127.0.0.1", "--port",
                                      str(PORT)]}


def test_a_readme_uvicorn_command_is_used(tmp_path):
    plan = _plan(tmp_path, {"requirements.txt": "uvicorn\n", "api/__init__.py": "",
                            "api/web.py": "x = 1\n",
                            "README.md": "Run it:\n\n    uvicorn api.web:server --reload\n"})
    assert plan["start"]["args"][:1] == ["api.web:server"]


@pytest.mark.parametrize("files,app", [
    ({".flaskenv": "FLASK_APP=cookbook.py\n", "cookbook.py": "from app import app\n",
      "requirements.txt": "flask\n"}, "cookbook"),
    ({"requirements.txt": "flask\n", "shelf/__init__.py":
      "from flask import Flask\n\ndef create_app():\n    return Flask(__name__)\n"}, "shelf"),
])
def test_flask_apps_run_with_flask_on_127_0_0_1(tmp_path, files, app):
    plan = _plan(tmp_path, files)
    assert plan["start"] == {"tool": "venv_bin", "bin": "flask",
                             "args": ["--app", app, "run", "--host", "127.0.0.1",
                                      "--port", str(PORT)]}


def test_a_chained_dev_script_gets_its_flags_on_the_last_command(tmp_path):
    assert run_here._dev_flags("pnpm prisma generate && vite dev", PORT)[:2] == \
        ["--host", "127.0.0.1"]


def test_a_django_server_only_answers_to_this_computers_names(tmp_path, monkeypatch):
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    env = run_here._base_env(IID, {"runtime": "python", "port": PORT})
    assert env["ALLOWED_HOSTS"] == "127.0.0.1,localhost"


# ---------------------------------------------------------------------------
# 4. the job page: an app that refuses frames opens in its own window
# ---------------------------------------------------------------------------

def _serve(headers: dict):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            for k, v in headers.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(b"<h1>its own page</h1>")

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/"


@pytest.mark.parametrize("headers,ok", [
    ({}, True),
    ({"X-Frame-Options": "DENY"}, False),
    ({"X-Frame-Options": "SAMEORIGIN"}, False),
    ({"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"}, False),
    ({"Content-Security-Policy": "frame-ancestors *"}, True),
])
def test_the_apps_own_frame_rule_decides(headers, ok):
    from puffpai.build import api
    srv, url = _serve(headers)
    try:
        api._FRAME_SEEN.clear()  # noqa: SLF001
        assert api.frame_allowed(url) is ok
    finally:
        srv.shutdown()


def test_only_this_computer_is_asked_about_frames():
    from puffpai.build import api
    assert api.frame_allowed("http://192.0.2.10:8000/") is False


def test_the_job_names_the_app_it_really_uses(monkeypatch):
    from puffpai.build import api, make
    from puffpai.build import schema as S
    monkeypatch.setattr(make, "load_made", lambda job: {"made": {
        "app_ref": "github:someone/foodganizer", "app_name": "Foodganizer"}})
    assert api.app_used({"route": S.A, "id": "j"}) == {"ref": "github:someone/foodganizer",
                                                        "name": "Foodganizer"}
    assert api.app_used({"route": S.B, "id": "j"}) == {}


