"""The persona path a stranger is promised, walked end to end.

docs/BUILD_A_PERSONA.md tells somebody who has never seen this repo that they
can turn a JSON file into a working part of their own bot. This file is that
claim under test, and it is deliberately the WHOLE path rather than four
separate unit checks, because every step of it used to pass alone while the
path itself was broken:

* the worked example in the doc was typed INTO the doc, so nothing ever
  checked that it validates. It now ships at ``apps/_examples/greenhouse`` and
  is the same file the doc quotes;
* installing it has to register the words it declares with the router, or the
  bot cannot be spoken to about it;
* it has to get a data store it can be written to and read back from through
  the API the dashboard already uses, or there is nothing to show;
* and its ``ui.route`` has to resolve, which is what the generic
  ``dashboard/app/dashboard/apps/[id]`` page is for.

No Ollama and no network: routing is keyword matching over the installed
manifests, and the store is a JSON file. The example is installed into a
TEMPORARY apps directory, never the repo's, so this file cannot leave a sixth
app behind.

Run:
    pytest tests/test_persona_path.py
"""
from __future__ import annotations

import json
import os
import shutil

import pytest

from puffpai import app_data as app_data_mod
from puffpai import api_server, app_registry, build_interview, cli, rulepacks
from puffpai.app_data import AppData

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPS_DIR = os.path.join(REPO_ROOT, "apps")
PACKS_DIR = os.path.join(REPO_ROOT, "packs")
EXAMPLE_APP = os.path.join(APPS_DIR, "_examples", "greenhouse")
EXAMPLE_PACK = os.path.join(PACKS_DIR, "_examples", "growbot")

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}


def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture(scope="module")
def manifest() -> dict:
    return _read(os.path.join(EXAMPLE_APP, "app.json"))


@pytest.fixture(scope="module")
def pack() -> dict:
    return _read(os.path.join(EXAMPLE_PACK, "pack.json"))


@pytest.fixture
def installed(tmp_path, monkeypatch):
    """The example installed the way an owner installs one: a folder, copied.

    Yields ``(apps_dir, data_dir)``. The registry's apps directory and the app
    data directory are both redirected, so the repo's ``apps/`` keeps its five
    and the repo's ``app_data/`` never grows a greenhouse.json.
    """
    apps_dir = tmp_path / "apps"
    data_dir = tmp_path / "app_data"
    apps_dir.mkdir()
    data_dir.mkdir()
    shutil.copytree(EXAMPLE_APP, apps_dir / "greenhouse")

    monkeypatch.setattr(app_registry, "_apps_dir", lambda: str(apps_dir))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(data_dir))
    return str(apps_dir), str(data_dir)


# ---------------------------------------------------------------------------
# 1. the file the doc quotes is a file, and it validates
# ---------------------------------------------------------------------------

def test_the_worked_example_validates_against_this_builds_taxonomy(manifest):
    known = set(build_interview.surface_taxonomy())
    assert app_registry.validate_manifest(manifest, known) is None


def test_the_example_pack_validates_once_its_app_is_installed(pack):
    assert app_registry.validate_pack(pack, {"greenhouse"}) is None


def test_the_example_pack_is_refused_when_its_app_is_not(pack):
    problem = app_registry.validate_pack(pack, set())
    assert problem and "greenhouse" in problem


def test_the_example_is_not_installed_in_this_build():
    """An example is a thing you copy, not a sixth app every owner inherits.

    ``_examples`` is skipped by both loaders (leading underscore), which is the
    only reason it can live inside apps/ and packs/ at all.
    """
    assert {str(m["id"]) for m in app_registry.load_manifests(APPS_DIR)} == {
        "alarmflow", "calendar", "freshtable", "fitfam", "trivia"}
    assert "growbot" not in {p["id"] for p in
                             app_registry.load_packs(PACKS_DIR)}


# ---------------------------------------------------------------------------
# 2. installing it gives the bot the words
# ---------------------------------------------------------------------------

def test_installing_it_registers_its_vocabulary(installed):
    apps_dir, _ = installed
    manifests = app_registry.load_manifests(apps_dir)
    assert [m["id"] for m in manifests] == ["greenhouse"]

    vocabulary = app_registry.assemble_vocabulary(manifests)
    assert "greenhouse" in vocabulary["keywords"]
    assert "how is the greenhouse doing" in vocabulary["examples"]


def test_a_declared_phrase_reaches_the_apps_surface(installed):
    """The router's apps pack carries the installed app's own words.

    This is the mechanism ``rulepacks._apply_app_manifests`` exists for: the
    pack's shell is core, its keywords come from the manifests on disk. Matching
    is checked against the freshly loaded packs rather than the router's
    import-time copy, because that copy is frozen at import and would only ever
    show the five shipped apps.
    """
    apps_dir, _ = installed
    packs = rulepacks.load_packs(apps_dir=apps_dir)
    apps_pack = next(p for p in packs
                     if p.get("surface") == app_registry.APPS_SURFACE)

    assert "greenhouse" in apps_pack["keywords"]
    assert rulepacks.matches(apps_pack, "how is the greenhouse doing")
    assert not rulepacks.matches(apps_pack, "what is the weather tomorrow")


# ---------------------------------------------------------------------------
# 3. it gets a store, reachable over the API the dashboard already uses
# ---------------------------------------------------------------------------

@pytest.fixture
def client(installed, monkeypatch):
    """The Flask test client, with the app registry pointed at the example."""
    ad = AppData()
    assert ad.VALID_APPS == ["greenhouse"]
    monkeypatch.setattr(api_server, "_get_app_data", lambda: ad)
    return api_server.app.test_client()


def test_a_new_app_starts_at_its_declared_default(client):
    resp = client.get("/apps/greenhouse/data", headers=AUTH)
    assert resp.status_code == 200
    assert resp.get_json()["readings"] == []


def test_an_item_can_be_stored_and_listed_back(client):
    """Store one reading and read it back - the generic page's whole loop.

    PUT replaces the document, so the page pulls, appends and pushes. That is
    the write path a declarative app gets without anybody writing Python.
    """
    reading = {"id": "r1", "bed": "tomatoes", "moisture": 31, "temp_c": 24}

    resp = client.put("/apps/greenhouse/data",
                      json={"readings": [reading]}, headers=AUTH)
    assert resp.status_code == 200, resp.get_data(as_text=True)

    resp = client.get("/apps/greenhouse/data", headers=AUTH)
    assert resp.status_code == 200
    assert resp.get_json()["readings"] == [reading]


def test_the_store_survives_a_second_write(client):
    client.put("/apps/greenhouse/data", json={"readings": []}, headers=AUTH)
    client.put("/apps/greenhouse/data",
               json={"readings": [{"id": "a"}, {"id": "b"}]}, headers=AUTH)
    assert [r["id"] for r in
            client.get("/apps/greenhouse/data",
                       headers=AUTH).get_json()["readings"]] == ["a", "b"]


def test_the_store_still_refuses_an_app_that_is_not_installed(client):
    assert client.get("/apps/nosuchapp/data", headers=AUTH).status_code == 404


# ---------------------------------------------------------------------------
# 4. the route the manifest declares resolves
# ---------------------------------------------------------------------------

def test_the_declared_ui_route_has_a_page_to_land_on(manifest):
    """``ui.route`` pointed at a page that did not exist for any new app.

    Every shipped app has a hand-written page under its own folder; an app an
    owner adds has none, so the link in the nav 404'd. The generic ``[id]``
    route is what makes the declared route true, and Next resolves the static
    segments first, so a hand-written page still wins.
    """
    assert manifest["ui"]["route"] == "/dashboard/apps/greenhouse"
    generic = os.path.join(REPO_ROOT, "dashboard", "app", "dashboard",
                           "apps", "[id]", "page.tsx")
    assert os.path.isfile(generic), (
        "the generic app page is gone, so every declared ui.route for an app "
        "without a hand-written page is a 404 again")


# ---------------------------------------------------------------------------
# 5. the commands the wizard prints, in the shell a Windows owner reads them in
# ---------------------------------------------------------------------------
# `&&` is not a statement separator in PowerShell 5.1, so the wizard's last
# instruction - the one that starts the dashboard - could not be pasted into
# the shell that opens when a Windows owner clicks "Windows PowerShell".


def test_the_pairing_command_follows_the_same_rule():
    # One word after puffpai: nothing to separate, so it runs in every shell.
    assert "&&" not in cli.pair_code_command("nt")
    assert cli.pair_code_command("nt") == "puffpai pair"
    assert cli.pair_code_command("posix") == "puffpai pair"


def test_both_forms_are_joined_from_one_list_of_steps():
    """So a fifth step cannot be added to one platform and not the other."""
    for command, sep in ((cli.DASHBOARD_START_COMMAND, " && "),
                         (cli.DASHBOARD_START_COMMAND_WINDOWS, "; ")):
        assert command.split(sep) == list(cli.DASHBOARD_START_STEPS)
