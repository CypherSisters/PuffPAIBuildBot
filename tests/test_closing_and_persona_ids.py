"""The two things an owner met at the end of her first real install.

She ran ``install.ps1 -WithDashboard -Port 8100 -DashboardPort 3100 -StateDir
.\\state``, then the wizard, and built a space bot called Infinity. Two things
were wrong at the end of it:

* the closing screen printed the MANUAL recipe - ``puffpai serve``, a
  ``.env.local`` to write by hand, ``npm install && npm run build``, and
  ``localhost:3003`` - at an install that already had launchers, an env file and
  a dashboard on port 3100. Every line was work already done, at the wrong
  address;
* the persona was written as ``apps``: ``state/apps/apps``,
  ``state/packs/apps``, and a page under the ``/dashboard/apps`` route. Nothing
  in the chain refused a word this build already uses for its own pages, and
  there was no way to change an id afterwards.

Run:
    pytest tests/test_closing_and_persona_ids.py
"""
from __future__ import annotations

import json
import os

import pytest

from puffpai import app_registry, cli, install_pointer, persona_scaffold

#: The brand word the product owner rejected, built rather than typed so
#: this file does not become the thing tests/test_first_run.py scans for.
_BANNED_WORD = "h" + "atch"


SPACE_GOAL = ("knows everything about space, space facts, current space "
              "events, astronomy, Earth weather events")


# ---------------------------------------------------------------------------
# the launchers an installed build is started with
# ---------------------------------------------------------------------------

@pytest.fixture
def install(tmp_path, monkeypatch):
    """A checkout with a pointer and an env file, like an installer leaves."""
    def _make(dashboard_port: str | None = "3100",
              with_dashboard: str | None = None) -> str:
        env_path = tmp_path / "buildbot.env"
        lines = ["PUFFPAI_CHECKOUT=" + str(tmp_path),
                 "PUFFPAI_API_KEY=" + "a" * 64,
                 "PUFFPAI_PORT=8100"]
        if dashboard_port is not None:
            lines.append("PUFFPAI_DASHBOARD_PORT=" + dashboard_port)
        if with_dashboard is not None:
            lines.append("PUFFPAI_WITH_DASHBOARD=" + with_dashboard)
        env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        pointer = install_pointer.write_pointer(str(tmp_path), str(env_path))
        monkeypatch.setattr(install_pointer, "candidate_roots",
                            lambda: [str(tmp_path)])
        return pointer
    return _make


def test_an_older_install_without_the_flag_keeps_its_dashboard(install):
    """No flag at all is an env file from before it existed, not a refusal."""
    install("3100")
    assert cli.installed_launchers("posix")["dashboard"] == "./run-dashboard.sh"


def test_the_launchers_are_named_for_the_platform_they_run_on(install):
    install("3100")
    assert cli.installed_launchers("nt")["backend"] == ".\\run-backend.ps1"
    assert cli.installed_launchers("posix")["backend"] == "./run-backend.sh"
    assert cli.installed_launchers("posix")["dashboard"] == "./run-dashboard.sh"


# ---------------------------------------------------------------------------
# the id
# ---------------------------------------------------------------------------

def test_the_bot_name_is_what_makes_the_id():
    assert persona_scaffold.suggest_id("Infinity", SPACE_GOAL) == "infinity"


def test_a_name_that_makes_no_id_falls_back_to_the_goals_own_nouns():
    assert persona_scaffold.suggest_id("", SPACE_GOAL) == "space"
    assert persona_scaffold.suggest_id("!!!", SPACE_GOAL) == "space"


def test_a_reserved_name_is_not_suggested_either():
    # "Apps" slugifies to a word this build already uses, so the goal wins.
    assert persona_scaffold.suggest_id("Apps", SPACE_GOAL) == "space"
    assert persona_scaffold.suggest_id("Apps", "") == ""


def test_reserved_ids_are_refused_when_they_are_typed():
    for app_id in ("apps", "dashboard", "vault", "settings", "puffpai"):
        with pytest.raises(persona_scaffold.ScaffoldError) as exc:
            persona_scaffold.validate_request(app_id, name="Infinity")
        assert app_id in str(exc.value)
        assert "already uses" in str(exc.value)


def test_a_reserved_id_is_refused_before_anything_is_written():
    with pytest.raises(persona_scaffold.ScaffoldError):
        persona_scaffold.scaffold("apps", name="Infinity", goal=SPACE_GOAL)
    assert not os.path.isdir(
        os.path.join(app_registry.user_apps_dir(), "apps"))


def test_an_ordinary_id_is_still_accepted():
    assert persona_scaffold.validate_request("infinity",
                                             name="Infinity")["id"] == "infinity"


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------

def _read(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


@pytest.fixture
def scaffolded():
    """One persona written with the id the owner actually got."""
    # Written past the refusal on purpose: this is the build she has, and the
    # rename is what has to fix it.
    made = persona_scaffold.scaffold("apps2", name="Infinity", goal=SPACE_GOAL,
                                     lists="launches")
    return made


def test_a_rename_moves_the_folders_the_manifests_and_the_store(scaffolded):
    from puffpai.app_data import AppData

    data = AppData()
    document = data.get_data("apps2") or {}
    document["launches"] = ["artemis"]
    data.set_data("apps2", document)

    result = persona_scaffold.rename_id("apps2", "infinity")

    assert result["id"] == "infinity"
    assert not os.path.isdir(os.path.join(app_registry.user_apps_dir(), "apps2"))
    assert not os.path.isdir(os.path.join(app_registry.user_packs_dir(), "apps2"))

    manifest = _read(result["app_path"])
    assert manifest["id"] == "infinity"
    assert manifest["ui"]["route"] == f"{persona_scaffold.ROUTE_PREFIX}infinity"
    pack = _read(result["pack_path"])
    assert pack["id"] == "infinity"
    assert [entry["id"] for entry in pack["apps"]] == ["infinity"]

    assert result["installed"]
    assert AppData().get_data("infinity")["launches"] == ["artemis"]


def test_a_rename_refuses_a_reserved_target_and_changes_nothing(scaffolded):
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.rename_id("apps2", "dashboard")
    assert "already uses" in str(exc.value)
    assert os.path.isdir(os.path.join(app_registry.user_apps_dir(), "apps2"))


def test_a_rename_refuses_an_id_that_is_already_taken(scaffolded):
    persona_scaffold.scaffold("gardenbot", name="GardenBot", goal="a garden")
    with pytest.raises(persona_scaffold.ScaffoldError) as exc:
        persona_scaffold.rename_id("apps2", "gardenbot")
    assert "gardenbot" in str(exc.value)
    assert os.path.isdir(os.path.join(app_registry.user_apps_dir(), "apps2"))


def test_a_rename_refuses_an_app_this_build_ships(scaffolded):
    with pytest.raises(persona_scaffold.ScaffoldError):
        persona_scaffold.rename_id("calendar", "infinity")


def test_the_cli_renames_and_says_what_moved(scaffolded, capsys):
    import argparse

    args = argparse.Namespace(persona_action="edit", persona_id="apps2",
                              rename_id="infinity", name=None, add_list=None,
                              rename_list=None, goal=None, lists=None)
    assert cli._cmd_persona(args) == 0
    out = capsys.readouterr().out
    assert "Renamed apps2 to infinity" in out
    assert _BANNED_WORD not in out.lower()
