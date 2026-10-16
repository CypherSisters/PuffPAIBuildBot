"""A persona can be edited, and editing it never costs the owner a row.

A scaffolded persona was write-once. The owner
picked "plantings" at the wizard, and adding "seed orders" three weeks later meant
hand-editing JSON in two places and then hoping the store followed.

``PATCH /persona/<id>`` and ``puffpai persona edit`` are the fix, and the tests
that matter are the store ones: a renamed list keeps every item, a new list
arrives empty, and no shape of request drops anything. ``remove_lists`` is not
offered at all - the refusal says why, and points at the delete flow.

Run:
    pytest tests/test_persona_edit.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import (
    api_server,
    app_data as app_data_mod,
    app_fastpath,
    app_registry,
    cli,
    persona_scaffold,
)

AUTH = {"Authorization": f"Bearer {api_server._API_KEY}"}


@pytest.fixture(autouse=True)
def _fresh_registry():
    app_registry.rescan()
    yield
    app_registry.rescan()


@pytest.fixture
def garden(tmp_path, monkeypatch):
    """GardenBot, installed, with two things on its plantings list."""
    monkeypatch.setenv("PUFFPAI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(app_data_mod, "_DATA_DIR", str(tmp_path / "app_data"))
    app_registry.rescan()
    persona_scaffold.scaffold("gardenbot", name="GardenBot",
                              goal="a garden and greenhouse bot",
                              lists="plantings, harvest log")
    data = app_data_mod.AppData()
    document = data.get_data("gardenbot")
    document["plantings"] = [{"item": "tomatoes"}, {"item": "basil"}]
    document["harvest_log"] = [{"item": "six courgettes"}]
    assert data.set_data("gardenbot", document)
    return tmp_path


def _manifest() -> dict:
    path = os.path.join(app_registry.user_apps_dir(), "gardenbot",
                        app_registry.MANIFEST_NAME)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _store() -> dict:
    return app_data_mod.AppData().get_data("gardenbot")


# ---------------------------------------------------------------------------
# adding a list
# ---------------------------------------------------------------------------

def test_add_list_reaches_the_manifest_the_store_and_the_fastpath(garden):
    result = persona_scaffold.edit("gardenbot", add_lists="seed orders")
    assert result["added"] == ["seed_orders"]
    assert result["lists"] == ["plantings", "harvest_log", "seed_orders"]
    assert result["installed"] is True

    manifest = _manifest()
    assert list(manifest["data"]["default"]) == [
        "plantings", "harvest_log", "seed_orders"]
    assert "seed orders" in manifest["vocabulary"]["keywords"]

    document = _store()
    assert document["seed_orders"] == []
    # Nothing that was there has moved.
    assert [r["item"] for r in document["plantings"]] == ["tomatoes", "basil"]

    # And the deterministic chat path can now write to the new list.
    reply = app_fastpath.handle("add pea seeds to the seed orders list",
                               app_data_mod.AppData(), app_registry.rescan())
    assert "Added pea seeds to the seed orders list" in reply


def test_adding_past_eight_lists_is_refused_and_changes_nothing(garden):
    persona_scaffold.edit("gardenbot", add_lists="a,b,c,d,e,f")
    before = _manifest()
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.edit("gardenbot", add_lists="g,h,i")
    assert "Nothing was changed" in str(exc.value)
    assert _manifest() == before


# ---------------------------------------------------------------------------
# renaming a list - the store test that matters
# ---------------------------------------------------------------------------

def test_rename_keeps_every_item_under_the_new_key(garden):
    result = persona_scaffold.edit(
        "gardenbot", rename_lists={"plantings": "seedlings"})
    assert result["renamed"] == {"plantings": "seedlings"}
    assert result["lists"] == ["seedlings", "harvest_log"]

    document = _store()
    assert "plantings" not in document
    assert [r["item"] for r in document["seedlings"]] == ["tomatoes", "basil"]
    # The other list is untouched.
    assert [r["item"] for r in document["harvest_log"]] == ["six courgettes"]

    reply = app_fastpath.handle("what is on the seedlings list",
                               app_data_mod.AppData(), app_registry.rescan())
    assert "tomatoes" in reply and "basil" in reply


def test_a_list_can_be_renamed_by_the_label_the_owner_reads(garden):
    result = persona_scaffold.edit(
        "gardenbot", rename_lists={"harvest log": "harvest"})
    assert result["renamed"] == {"harvest_log": "harvest"}
    assert [r["item"] for r in _store()["harvest"]] == ["six courgettes"]


def test_renaming_a_list_that_is_not_there_changes_nothing(garden):
    before = _manifest()
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.edit("gardenbot", rename_lists={"tools": "toolbox"})
    message = str(exc.value)
    assert "no list called 'tools'" in message
    assert "plantings" in message and "harvest log" in message
    assert _manifest() == before
    assert "plantings" in _store()


def test_a_rename_and_an_add_together_lose_nothing(garden):
    persona_scaffold.edit("gardenbot", name="Greenhouse",
                          add_lists="seed orders",
                          rename_lists={"plantings": "seedlings"})
    document = _store()
    assert [r["item"] for r in document["seedlings"]] == ["tomatoes", "basil"]
    assert [r["item"] for r in document["harvest_log"]] == ["six courgettes"]
    assert document["seed_orders"] == []
    assert _manifest()["name"] == "Greenhouse"


# ---------------------------------------------------------------------------
# the name, and the pack that carries it
# ---------------------------------------------------------------------------

def test_renaming_the_persona_regenerates_the_vocabulary_and_the_pack(garden):
    result = persona_scaffold.edit("gardenbot", name="Greenhouse")
    assert result["name"] == "Greenhouse"
    keywords = _manifest()["vocabulary"]["keywords"]
    assert "greenhouse" in keywords
    # Regeneration only ever widens: the old name still answers.
    assert "gardenbot" in keywords

    pack_path = os.path.join(app_registry.user_packs_dir(), "gardenbot",
                             app_registry.PACK_MANIFEST_NAME)
    with open(pack_path, encoding="utf-8") as fh:
        assert json.load(fh)["display_name"] == "Greenhouse pack"


def test_a_hand_edited_field_survives_an_edit(garden):
    path = os.path.join(app_registry.user_apps_dir(), "gardenbot",
                        app_registry.MANIFEST_NAME)
    manifest = _manifest()
    manifest["author"] = "A. Owner"
    manifest["ui"]["icon"] = "mdiFlower"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh)

    persona_scaffold.edit("gardenbot", add_lists="compost")
    after = _manifest()
    assert after["author"] == "A. Owner"
    assert after["ui"]["icon"] == "mdiFlower"


# ---------------------------------------------------------------------------
# what is refused
# ---------------------------------------------------------------------------

def test_a_shipped_app_cannot_be_edited_this_way(garden):
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.edit("calendar", add_lists="anything")
    assert "not a persona you made" in str(exc.value)


def test_remove_lists_is_not_offered_and_the_refusal_says_why(garden):
    client = api_server.app.test_client()
    response = client.patch("/persona/gardenbot",
                            json={"remove_lists": ["plantings"]}, headers=AUTH)
    assert response.status_code == 400
    error = response.get_json()["error"]
    assert "remove_lists" in error
    assert "DELETE /persona/gardenbot" in error
    # And the list is still there, with its rows.
    assert [r["item"] for r in _store()["plantings"]] == ["tomatoes", "basil"]
    # The docstring states the reason, so the contract is not only in a test.
    assert "data loss" in api_server.persona_edit.__doc__


def test_a_patch_that_asks_for_nothing_is_refused_not_answered_success(garden):
    client = api_server.app.test_client()
    response = client.patch("/persona/gardenbot", json={}, headers=AUTH)
    assert response.status_code == 400
    assert "asked for nothing" in response.get_json()["error"]


# ---------------------------------------------------------------------------
# the route and the CLI twin agree
# ---------------------------------------------------------------------------

def test_the_patch_route_edits_and_reports_what_it_did(garden):
    client = api_server.app.test_client()
    response = client.patch("/persona/gardenbot", json={
        "name": "Greenhouse",
        "add_lists": ["seed orders"],
        "rename_lists": {"plantings": "seedlings"},
    }, headers=AUTH)
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["edited"] is True
    assert body["persona"]["lists"] == ["seedlings", "harvest_log", "seed_orders"]
    assert body["persona"]["store"]["migrated"] is True
    assert any("renamed" in line for line in body["summary"])
    assert [r["item"] for r in _store()["seedlings"]] == ["tomatoes", "basil"]


def test_the_cli_twin_does_the_same_thing(garden, capsys):
    code = cli.main(["persona", "edit", "gardenbot", "--name", "Greenhouse",
                     "--add-list", "seed orders",
                     "--rename-list", "plantings=seedlings"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Edited Greenhouse (gardenbot)." in out
    assert "renamed: plantings -> seedlings" in out
    assert "nothing was dropped" in out
    assert [r["item"] for r in _store()["seedlings"]] == ["tomatoes", "basil"]


def test_the_cli_refuses_a_bad_rename_pair_without_touching_anything(garden,
                                                                    capsys):
    before = _manifest()
    assert cli.main(["persona", "edit", "gardenbot",
                     "--rename-list", "plantings"]) == 1
    assert "takes old=new" in capsys.readouterr().out
    assert _manifest() == before


def test_the_cli_says_so_when_asked_to_change_nothing(garden, capsys):
    assert cli.main(["persona", "edit", "gardenbot"]) == 1
    out = capsys.readouterr().out
    assert "Nothing to change" in out
    assert "data loss" in out
